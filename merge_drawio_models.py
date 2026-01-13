#!/usr/bin/env python3
"""
merge_drawio_models.py

Merge multiple diagrams.net (draw.io) Data Vault model exports into a single draw.io model.

Features:
- Reads only *.drawio.xml files from an input folder
- Deduplicates DV objects (Hubs, Links, Satellites) by table name
- Preserves all relationships (edges)
- Includes ALL source tables (no dedupe)
- Writes merged output to a separate folder
- Always overwrites the merged output file

Default folders (relative to this script):
  ./model          -> input draw.io models
  ./merged_output  -> merged draw.io output

Usage:
  python merge_drawio_models.py
  python merge_drawio_models.py --input-dir model --output-dir merged_output --output merged.drawio.xml
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import urllib.parse
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# draw.io decoding helpers
# ---------------------------------------------------------------------------

def _try_decode_diagram_payload(payload: str) -> Optional[str]:
    s = (payload or "").strip()
    if not s:
        return None

    if s.startswith("<mxGraphModel"):
        return s

    if "<mxGraphModel" in s and "</mxGraphModel>" in s:
        return s[s.find("<mxGraphModel"): s.rfind("</mxGraphModel>") + len("</mxGraphModel>")]

    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return None

    for wbits in (-15, zlib.MAX_WBITS):
        try:
            inflated = zlib.decompress(raw, wbits).decode("utf-8", errors="replace")
            inflated = urllib.parse.unquote(inflated)
            if "<mxGraphModel" in inflated:
                return inflated[inflated.find("<mxGraphModel"): inflated.rfind("</mxGraphModel>") + len("</mxGraphModel>")]
        except Exception:
            continue

    return None


def extract_mxgraphmodel(drawio_text: str) -> ET.Element:
    root = ET.fromstring(drawio_text)

    if root.tag == "mxGraphModel":
        return root

    if root.tag == "mxfile":
        diagrams = root.findall(".//diagram")
        for d in diagrams:
            mx_child = d.find(".//mxGraphModel")
            if mx_child is not None:
                return mx_child
            decoded = _try_decode_diagram_payload(d.text or "")
            if decoded:
                return ET.fromstring(decoded)
        raise ValueError("Could not decode any <diagram> into <mxGraphModel>.")

    if root.tag == "diagram":
        mx_child = root.find(".//mxGraphModel")
        if mx_child is not None:
            return mx_child
        decoded = _try_decode_diagram_payload(root.text or "")
        if decoded:
            return ET.fromstring(decoded)

    raise ValueError("Unable to locate <mxGraphModel> in draw.io XML.")


# ---------------------------------------------------------------------------
# mxGraph helpers
# ---------------------------------------------------------------------------

def _iter_cells(mx: ET.Element) -> Iterable[ET.Element]:
    return mx.findall(".//mxCell")


def _clean_label(v: str) -> str:
    v = v or ""
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.IGNORECASE)
    v = re.sub(r"</?[^>]+>", "", v)
    v = v.replace("&nbsp;", " ")
    return v.strip()


def _cell_title(cell: ET.Element) -> str:
    txt = _clean_label(cell.attrib.get("value", ""))
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    return lines[0] if lines else ""


def _is_vertex(cell: ET.Element) -> bool:
    return cell.attrib.get("vertex") == "1" and cell.attrib.get("edge") != "1"


def _is_edge(cell: ET.Element) -> bool:
    return cell.attrib.get("edge") == "1"


def _is_source_table(cell: ET.Element) -> bool:
    style = cell.attrib.get("style", "")
    return "shape=table" in style and "shape=tableRow" not in style


def _build_indexes(cells: List[ET.Element]):
    by_id = {}
    children = defaultdict(list)
    for c in cells:
        cid = c.attrib.get("id")
        if cid:
            by_id[cid] = c
        pid = c.attrib.get("parent")
        if pid and cid:
            children[pid].append(cid)
    return by_id, children



def _collect_subtree(root_id: str, children: Dict[str, List[str]]) -> List[str]:
    out, q, seen = [], [root_id], set()
    while q:
        cur = q.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for ch in children.get(cur, []):
            q.append(ch)
    return out


# Helper to compute area of a cell from its mxGeometry
def _cell_area(cell: ET.Element) -> float:
    geo = cell.find("./mxGeometry")
    if geo is None:
        return 0.0
    try:
        w = float(geo.attrib.get("width", "0") or 0)
        h = float(geo.attrib.get("height", "0") or 0)
        return w * h
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _clone(elem: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(elem, encoding="utf-8"))


def merge_models(input_dir: Path, output_dir: Path, output_name: str) -> Path:
    model_files = [p for p in sorted(input_dir.iterdir()) if p.name.lower().endswith(".drawio.xml")]
    if not model_files:
        raise ValueError("No .drawio.xml files found to merge.")

    logging.info("Merging %d model(s)", len(model_files))

    # Global unique id counter (string-based to avoid collisions)
    uid_counter = 0
    def new_id(prefix: str) -> str:
        nonlocal uid_counter
        uid_counter += 1
        return f"{prefix}_{uid_counter}"

    # Preserve mxGraphModel attributes from the first model for maximum draw.io compatibility
    first_mx = extract_mxgraphmodel(model_files[0].read_text(encoding="utf-8", errors="replace"))
    merged_mx = ET.Element("mxGraphModel", attrib=dict(first_mx.attrib))
    root = ET.SubElement(merged_mx, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    merged_by_name: Dict[str, str] = {}
    edge_keys: Set[Tuple[str, str, str]] = set()

    x_offset = 0.0
    x_stride = 2000.0

    for model in model_files:
        logging.info("Processing model: %s", model.name)
        model_prefix = Path(model.name).stem

        mx = extract_mxgraphmodel(model.read_text(encoding="utf-8", errors="replace"))
        cells = list(_iter_cells(mx))
        by_id, children = _build_indexes(cells)

        # dv_roots_by_name keeps the *container* root per DV table name.
        # In draw.io templates, multiple inner cells can contain the table name;
        # we choose the largest-area vertex as the container root.
        dv_roots_by_name: Dict[str, Tuple[str, float]] = {}
        source_roots: List[str] = []
        edges: List[str] = []

        for c in cells:
            cid = c.attrib.get("id")
            if not cid:
                continue

            if _is_edge(c):
                edges.append(cid)
                continue

            if not _is_vertex(c):
                continue

            title = _cell_title(c)
            if title:
                low_title = title.lower()
                if low_title.startswith(("h_", "l_", "s_")):
                    a = _cell_area(c)
                    prev = dv_roots_by_name.get(low_title)
                    if prev is None or a > prev[1]:
                        dv_roots_by_name[low_title] = (cid, a)
                    continue

            if _is_source_table(c):
                source_roots.append(cid)

        dv_roots = {nm: cid for nm, (cid, _a) in dv_roots_by_name.items()}

        # Map only DV root ids for edge endpoint remapping.
        endpoint_map: Dict[str, str] = {}

        # copy DV objects (dedupe by name)
        for name, rid in dv_roots.items():
            if name in merged_by_name:
                endpoint_map[rid] = merged_by_name[name]
                continue

            subtree = _collect_subtree(rid, children)
            local_map: Dict[str, str] = {oid: new_id(model_prefix) for oid in subtree}

            for oid in subtree:
                oc = by_id[oid]
                nc = _clone(oc)
                nc.attrib["id"] = local_map[oid]

                # Remap parent inside subtree; otherwise attach to main layer
                op = oc.attrib.get("parent")
                if op in local_map:
                    nc.attrib["parent"] = local_map[op]
                else:
                    nc.attrib["parent"] = "1"

                # Remap any embedded terminals if present
                if "source" in nc.attrib and nc.attrib.get("source") in local_map:
                    nc.attrib["source"] = local_map[nc.attrib["source"]]
                if "target" in nc.attrib and nc.attrib.get("target") in local_map:
                    nc.attrib["target"] = local_map[nc.attrib["target"]]

                # Offset the container root so models do not overlap
                geo = nc.find("./mxGeometry")
                if oid == rid and geo is not None and "x" in geo.attrib:
                    geo.attrib["x"] = str(float(geo.attrib.get("x", "0")) + x_offset)

                root.append(nc)

            merged_by_name[name] = local_map[rid]
            endpoint_map[rid] = local_map[rid]

        # copy source tables (no dedupe). Always allocate fresh IDs per table subtree.
        for rid in source_roots:
            subtree = _collect_subtree(rid, children)
            local_map: Dict[str, str] = {oid: new_id(model_prefix) for oid in subtree}

            for oid in subtree:
                oc = by_id[oid]
                nc = _clone(oc)
                nc.attrib["id"] = local_map[oid]

                op = oc.attrib.get("parent")
                if op in local_map:
                    nc.attrib["parent"] = local_map[op]
                else:
                    nc.attrib["parent"] = "1"

                if "source" in nc.attrib and nc.attrib.get("source") in local_map:
                    nc.attrib["source"] = local_map[nc.attrib["source"]]
                if "target" in nc.attrib and nc.attrib.get("target") in local_map:
                    nc.attrib["target"] = local_map[nc.attrib["target"]]

                geo = nc.find("./mxGeometry")
                if oid == rid and geo is not None and "x" in geo.attrib:
                    geo.attrib["x"] = str(float(geo.attrib.get("x", "0")) + x_offset)

                root.append(nc)

        # copy edges (relationships). Endpoints are expected to be DV container root ids.
        for eid in edges:
            e = by_id[eid]
            s_old = e.attrib.get("source")
            t_old = e.attrib.get("target")
            s = endpoint_map.get(s_old)
            t = endpoint_map.get(t_old)
            if not s or not t:
                continue

            label = _clean_label(e.attrib.get("value", ""))
            key = (s, t, label.lower())
            if key in edge_keys:
                continue
            edge_keys.add(key)

            ne = _clone(e)
            ne.attrib.update({"id": new_id(model_prefix), "parent": "1", "source": s, "target": t})
            root.append(ne)

        x_offset += x_stride

    mxfile = ET.Element("mxfile", host="app.diagrams.net")
    diagram = ET.SubElement(mxfile, "diagram", name="Merged")

    # Embed mxGraphModel as a child element (matches standard uncompressed draw.io exports)
    diagram.append(merged_mx)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / output_name

    # Write with UTF-8 and XML declaration for maximum compatibility
    out_xml = ET.tostring(mxfile, encoding="utf-8", xml_declaration=True)
    out_path.write_bytes(out_xml)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Merge draw.io models into a single merged diagram")
    parser.add_argument("--input-dir", default="model")
    parser.add_argument("--output-dir", default="merged_output")
    parser.add_argument("--output", default="merged.drawio.xml")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")

    script_dir = Path(__file__).resolve().parent
    out = merge_models(script_dir / args.input_dir, script_dir / args.output_dir, args.output)
    logging.info("Merged model written to %s", out)


if __name__ == "__main__":
    main()