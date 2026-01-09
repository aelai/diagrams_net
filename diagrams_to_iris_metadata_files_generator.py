#!/usr/bin/env python3
"""
diagrams_to_iris_metadata_files_generator.py

V1: Generate IRiS Source metadata XLSX from a diagrams.net (draw.io) XML export.

Folder convention (relative to this script):
  ./model   -> input draw.io XML files (one or many)
  ./output  -> generated artifacts
    ./output/<MODEL_NAME>/source_<MODEL_NAME>.xlsx

MODEL_NAME is the input filename stem (filename without extensions).

Usage examples:
  python diagrams_to_iris_metadata_files_generator.py --schema landing --model SERVICE_SERVICESSYSTEM.drawio.xml
  python diagrams_to_iris_metadata_files_generator.py --schema landing --all
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import logging
import re
import urllib.parse
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from openpyxl import Workbook


# ----------------------------- Data structures -----------------------------

@dataclasses.dataclass(frozen=True)
class SourceColumn:
    column: str
    datatype: str
    size: str
    scale: str


@dataclasses.dataclass(frozen=True)
class SourceTable:
    table_name: str
    columns: List[SourceColumn]


# ----------------------------- Draw.io decoding ----------------------------

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _try_decode_diagram_payload(payload: str) -> Optional[str]:
    """
    Attempt to decode diagrams.net compressed diagram content.

    Common patterns:
      - Plain XML: '<mxGraphModel>...'
      - Base64 + raw DEFLATE (zlib -15) -> URL-encoded XML

    Returns decoded XML text if successful, else None.
    """
    s = (payload or "").strip()
    if not s:
        return None

    # Plain mxGraphModel or related XML
    if s.startswith("<mxGraphModel") or s.startswith("<mxfile") or s.startswith("<mxGraph"):
        return s

    # Sometimes mxGraphModel is embedded inline with other text
    if "<mxGraphModel" in s and "</mxGraphModel>" in s:
        start = s.find("<mxGraphModel")
        end = s.rfind("</mxGraphModel>") + len("</mxGraphModel>")
        return s[start:end]

    # Otherwise: try base64 decode + inflate
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return None

    # Try raw DEFLATE first (most common), then zlib wrapper
    for wbits in (-15, zlib.MAX_WBITS):
        try:
            inflated = zlib.decompress(raw, wbits).decode("utf-8", errors="replace")
            inflated = urllib.parse.unquote(inflated)
            if "<mxGraphModel" in inflated and "</mxGraphModel>" in inflated:
                start = inflated.find("<mxGraphModel")
                end = inflated.rfind("</mxGraphModel>") + len("</mxGraphModel>")
                return inflated[start:end]
        except Exception:
            continue

    return None


def extract_mxgraphmodel_xml(drawio_xml_text: str) -> str:
    """
    Given a draw.io XML export (mxfile/diagram), return the embedded
    <mxGraphModel>...</mxGraphModel> XML text.

    Supports both compressed and uncompressed diagram payloads.

    Raises ValueError if not found/decodable.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(drawio_xml_text)
    except Exception as e:
        raise ValueError(f"Input is not valid XML: {e}") from e

    # Case 1: file already is mxGraphModel
    if root.tag == "mxGraphModel":
        return drawio_xml_text

    # Case 2: standard <mxfile><diagram>...</diagram></mxfile>
    if root.tag == "mxfile":
        diagram_elems = list(root.findall(".//diagram"))
        if not diagram_elems:
            raise ValueError("No <diagram> elements found in <mxfile>.")
        diagram = diagram_elems[0]  # first diagram by default

        # Some exports embed <mxGraphModel> as a child element (not as encoded text)
        mx_child = diagram.find(".//mxGraphModel")
        if mx_child is not None:
            return ET.tostring(mx_child, encoding="unicode")

        # Otherwise, the diagram content is typically encoded in the diagram text
        decoded = _try_decode_diagram_payload(diagram.text or "")
        if decoded:
            return decoded

        # Sometimes text may be split; try itertext
        decoded2 = _try_decode_diagram_payload("".join(diagram.itertext()))
        if decoded2:
            return decoded2

        raise ValueError("Could not decode <diagram> payload into <mxGraphModel>.")

    # Case 3: Some exports have <diagram> as root
    if root.tag == "diagram":
        # Some exports embed <mxGraphModel> as a child element (not as encoded text)
        mx_child = root.find(".//mxGraphModel")
        if mx_child is not None:
            return ET.tostring(mx_child, encoding="unicode")

        decoded = _try_decode_diagram_payload(root.text or "")
        if decoded:
            return decoded
        raise ValueError("Could not decode <diagram> payload into <mxGraphModel>.")

    # Fallback: look for mxGraphModel anywhere
    mx = root.find(".//mxGraphModel")
    if mx is not None:
        return ET.tostring(mx, encoding="unicode")

    raise ValueError("Unable to locate <mxGraphModel> in input.")


# ----------------------------- mxGraph parsing -----------------------------

def parse_mxgraphmodel(mxgraph_xml_text: str):
    import xml.etree.ElementTree as ET
    return ET.fromstring(mxgraph_xml_text)


def _iter_cells(mxgraph_root) -> Iterable:
    for cell in mxgraph_root.findall(".//mxCell"):
        yield cell


def _cell_attr(cell, name: str) -> str:
    return cell.attrib.get(name, "")


def _cell_value(cell) -> str:
    """Extract label text from an mxCell value attribute (light HTML stripping)."""
    v = cell.attrib.get("value", "")
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.IGNORECASE)
    v = re.sub(r"</?[^>]+>", "", v)
    v = v.replace("&nbsp;", " ").strip()
    return v


def _cell_geometry(cell) -> Tuple[float, float, float, float]:
    geo = cell.find("./mxGeometry")
    if geo is None:
        return (0.0, 0.0, 0.0, 0.0)

    def _f(k: str) -> float:
        try:
            return float(geo.attrib.get(k, 0) or 0)
        except Exception:
            return 0.0

    return (_f("x"), _f("y"), _f("width"), _f("height"))


def _is_vertex(cell) -> bool:
    return _cell_attr(cell, "vertex") == "1"


# --------------------------- Source table extraction ------------------------

def _cluster_by_y(
    cells_with_geom: List[Tuple[float, float, float, float, str]],
    tolerance: float = 6.0,
):
    """
    Group cell labels into rows based on y coordinate.
    Returns list of rows, each row is list of (x, label).
    """
    sorted_cells = sorted(cells_with_geom, key=lambda t: (t[1], t[0]))
    rows: List[List[Tuple[float, str]]] = []
    row_ys: List[float] = []

    for x, y, w, h, label in sorted_cells:
        placed = False
        for i, ry in enumerate(row_ys):
            if abs(y - ry) <= tolerance:
                rows[i].append((x, label))
                placed = True
                break
        if not placed:
            row_ys.append(y)
            rows.append([(x, label)])

    for r in rows:
        r.sort(key=lambda t: t[0])

    return rows


def _looks_like_source_table_header(row_labels: List[str]) -> bool:
    norm = [re.sub(r"\s+", " ", x.strip().lower()) for x in row_labels if x.strip()]
    return ("column" in norm) and ("datatype" in norm)


def extract_source_tables_from_drawio(drawio_xml_text: str) -> List[SourceTable]:
    """
    Extract source tables from a draw.io diagram.

    Strategy:
      - Decode <mxGraphModel>
      - Find vertex cells with style containing 'shape=table'
      - Collect descendant vertices (table cells are often grandchildren under tableRow nodes)
      - Reconstruct a grid using geometry (y groups rows, x orders columns)
      - Identify header row containing 'Column' and 'Datatype'
      - Extract Column/Datatype/Size/Scale from subsequent rows
    """
    mx_xml = extract_mxgraphmodel_xml(drawio_xml_text)
    root = parse_mxgraphmodel(mx_xml)

    cells = list(_iter_cells(root))
    by_id: Dict[str, any] = {c.attrib.get("id", ""): c for c in cells if c.attrib.get("id")}

    # parent -> children (cells store "parent" attribute)
    children_by_parent: Dict[str, List[any]] = {}
    for c in cells:
        pid = c.attrib.get("parent", "")
        if pid:
            children_by_parent.setdefault(pid, []).append(c)

    def iter_descendant_vertices(parent_id: str) -> Iterable:
        """Yield all descendant mxCells (vertex=1) under a parent id (BFS)."""
        queue = [parent_id]
        seen = set()
        while queue:
            pid = queue.pop(0)
            for ch in children_by_parent.get(pid, []):
                cid = ch.attrib.get("id", "")
                if cid and cid not in seen:
                    seen.add(cid)
                    queue.append(cid)
                if _is_vertex(ch):
                    yield ch

    # Candidate: draw.io "table" stencil. Style often contains "shape=table".
    candidates = []
    for c in cells:
        if not _is_vertex(c):
            continue
        style = c.attrib.get("style", "")
        if "shape=table" in style:
            candidates.append(c)

    source_tables: List[SourceTable] = []

    for table_cell in candidates:
        tid = table_cell.attrib.get("id", "")
        if not tid:
            continue

        # Collect descendant vertices that actually hold cell labels (value) and geometry
        descendant_vertices = list(iter_descendant_vertices(tid))
        if not descendant_vertices:
            continue

        def abs_xy(cell_id: str) -> Tuple[float, float]:
            """Compute absolute x,y by summing mxGeometry x,y up the parent chain."""
            ax = 0.0
            ay = 0.0
            cur_id = cell_id
            seen = set()
            while cur_id and cur_id not in seen:
                seen.add(cur_id)
                cell = by_id.get(cur_id)
                if cell is None:
                    break
                x, y, _, _ = _cell_geometry(cell)
                ax += x
                ay += y
                cur_id = cell.attrib.get("parent", "")
                # Stop at top-level (parent '1' or '0' typically has no geometry impact)
                if cur_id in ("0", "1", ""):
                    break
            return ax, ay

        labeled_cells: List[Tuple[float, float, float, float, str]] = []
        for cc in descendant_vertices:
            style = cc.attrib.get("style", "")
            # Ignore row-container cells; we only want actual table-cell rectangles.
            if "shape=tableRow" in style:
                continue

            label = _cell_value(cc)
            x, y, w, h = _cell_geometry(cc)

            # Keep empty-label cells if they look like real table cells.
            # This preserves column positions for rows where e.g. Scale is blank.
            if not label.strip():
                if w <= 0 and h <= 0:
                    continue
                label = ""

            ax, ay = abs_xy(cc.attrib.get("id", ""))
            labeled_cells.append((ax, ay, w, h, label))

        if not labeled_cells:
            continue

        rows = _cluster_by_y(labeled_cells)

        header_idx = None
        header_labels = None
        for i, row in enumerate(rows):
            labels = [lbl for _, lbl in row if str(lbl).strip()]
            if _looks_like_source_table_header(labels):
                header_idx = i
                header_labels = labels
                break
        if header_idx is None or header_labels is None:
            continue

        header_norm = [re.sub(r"\s+", " ", h.strip().lower()) for h in header_labels]

        def idx_of(name: str) -> Optional[int]:
            try:
                return header_norm.index(name)
            except ValueError:
                return None

        col_i = idx_of("column")
        dt_i = idx_of("datatype")
        size_i = idx_of("size")
        scale_i = idx_of("scale")

        if col_i is None or dt_i is None:
            continue

        table_name = _cell_value(table_cell) or tid or "source_table"

        columns: List[SourceColumn] = []
        for r in rows[header_idx + 1 :]:
            vals = [lbl for _, lbl in r]
            max_len = max(col_i, dt_i, size_i or 0, scale_i or 0) + 1
            if len(vals) < max_len:
                vals += [""] * (max_len - len(vals))

            col = (vals[col_i] if col_i < len(vals) else "").strip()
            dt = (vals[dt_i] if dt_i < len(vals) else "").strip()
            sz = (vals[size_i] if size_i is not None and size_i < len(vals) else "").strip()
            sc = (vals[scale_i] if scale_i is not None and scale_i < len(vals) else "").strip()

            # Skip empty rows
            if not col and not dt and not sz and not sc:
                continue
            if not col:
                continue

            columns.append(SourceColumn(column=col, datatype=dt, size=sz, scale=sc))

        if columns:
            source_tables.append(SourceTable(table_name=table_name, columns=columns))

    return source_tables


# ----------------------------- XLSX generation -----------------------------

SOURCE_HEADERS = ["Table Schema", "Table Name", "Column", "Datatype", "Size", "Scale"]


def write_source_xlsx(schema: str, source_tables: List[SourceTable], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Source"

    ws.append(SOURCE_HEADERS)

    for st in source_tables:
        for col in st.columns:
            ws.append([schema, st.table_name, col.column, col.datatype, col.size, col.scale])

    ws.freeze_panes = "A2"
    widths = {"A": 14, "B": 32, "C": 26, "D": 14, "E": 10, "F": 10}
    for col_letter, w in widths.items():
        ws.column_dimensions[col_letter].width = w

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


# ----------------------------- Public function -----------------------------

def generate_source_metadata(model_filename: str, schema: str) -> Path:
    """
    Generate a brand-new IRiS Source metadata XLSX for a given draw.io XML model file.

    Looks for input in:  ./model/<model_filename>
    Writes output to:    ./output/<MODEL_NAME>/source_<MODEL_NAME>.xlsx

    Returns the output file path.
    """
    script_dir = Path(__file__).resolve().parent
    model_dir = script_dir / "model"
    out_root = script_dir / "output"

    model_path = model_dir / model_filename
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    # Determine model name from filename stem (strip common multi-extensions)
    name = model_path.name
    model_name = re.sub(r"\.drawio\.xml$", "", name, flags=re.IGNORECASE)
    model_name = re.sub(r"\.xml$", "", model_name, flags=re.IGNORECASE)
    model_name = Path(model_name).stem

    out_dir = out_root / model_name
    out_file = out_dir / f"source_{model_name}.xlsx"

    drawio_text = _read_text(model_path)
    source_tables = extract_source_tables_from_drawio(drawio_text)
    if not source_tables:
        raise ValueError(
            "No source tables found. Expected at least one draw.io table shape with header cells "            "including 'Column' and 'Datatype'."
        )

    write_source_xlsx(schema=schema, source_tables=source_tables, out_path=out_file)
    return out_file


# ----------------------------- CLI (optional) ------------------------------

def _list_model_files(model_dir: Path) -> List[Path]:
    if not model_dir.exists():
        return []
    return sorted([p for p in model_dir.iterdir() if p.is_file() and p.suffix.lower() == ".xml"])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate IRiS Source metadata XLSX from draw.io XML models.")
    parser.add_argument("--schema", required=True, help="Schema to write into the Source XLSX (e.g., landing).")
    parser.add_argument("--model", help="Model filename under ./model (e.g., SERVICE_SERVICESSYSTEM.drawio.xml).")
    parser.add_argument("--all", action="store_true", help="Process all .xml files in ./model.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    script_dir = Path(__file__).resolve().parent
    model_dir = script_dir / "model"

    try:
        if args.all:
            model_files = _list_model_files(model_dir)
            if not model_files:
                logging.error("No model files found under ./model.")
                return 2
            for p in model_files:
                logging.info("Processing model: %s", p.name)
                out = generate_source_metadata(model_filename=p.name, schema=args.schema)
                logging.info("Wrote: %s", out)
            return 0

        if not args.model:
            logging.error("Provide --model <filename> or use --all.")
            return 2

        out = generate_source_metadata(model_filename=args.model, schema=args.schema)
        logging.info("Wrote: %s", out)
        return 0

    except Exception as e:
        logging.error("%s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
