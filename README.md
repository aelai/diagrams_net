# diagrams_net – IRiS Metadata Generator

This project provides a lightweight way to generate **IRiS metadata Excel files** directly from **diagrams.net (draw.io) Data Vault models**.

The initial focus of the project is to generate the **Source metadata XLSX** from a draw.io diagram that contains one or more **source tables**.

The diagram is treated as the **single source of truth**:
- Object names come directly from the model
- Column names come directly from the model
- No renaming or inference is performed by the script

---

## Project Structure

The repository follows a very simple, convention-based layout:

```
.
├── diagrams_to_iris_metadata_files_generator.py
├── model/
│   └── <model>.drawio.xml
├── output/
│   └── <MODEL_NAME>/
│       └── source_<MODEL_NAME>.xlsx
└── README.md
```

### Folders

- **`model/`**  
  Contains one or more draw.io XML exports (`.drawio.xml` or `.xml`).  
  Each file represents a single logical model.

- **`output/`**  
  Generated artifacts. A subfolder is created per model using the model file name.

---

## Diagram Requirements (Source Tables)

To be detected as a source table, a table in diagrams.net must:

- Use the **draw.io table shape**
- Contain a header row with at least:
  - `Column`
  - `Datatype`
- Optionally include:
  - `Size`
  - `Scale`

Notes:
- Empty cells are supported
- Additional columns (e.g. Target / Relationship) are ignored for Source metadata
- Only the Source attributes are written to the XLSX

---

## How the Script Works

For each model file:

1. The draw.io XML is parsed
2. The embedded `<mxGraphModel>` is extracted (supports multiple draw.io export formats)
3. Source tables are located and reconstructed using cell geometry
4. A **new Source metadata XLSX** is generated

The script always creates a **fresh XLSX**; it does not append or merge.

---

## How to Run the Script

### Prerequisites

- Python 3.9+
- `openpyxl`

Install dependencies:

```bash
pip install openpyxl
```

---

### Run for a Single Model

1. Place your draw.io XML file in the `model/` folder
2. Run:

```bash
python diagrams_to_iris_metadata_files_generator.py \
  --schema landing \
  --model service_servicessystem.drawio.xml
```

This will create:

```
output/
└── service_servicessystem/
    └── source_service_servicessystem.xlsx
```

---

### Run for All Models

To process **all `.xml` files** in the `model/` folder:

```bash
python diagrams_to_iris_metadata_files_generator.py --schema landing --all
```

Each model will get its own output folder.

---

## Output – Source Metadata XLSX

The generated **Source** worksheet contains the following columns:

| Column Name | Description |
|------------|------------|
| Table Schema | Schema supplied at runtime (`--schema`) |
| Table Name | Source table name from the diagram |
| Column | Column name from the diagram |
| Datatype | Datatype from the diagram |
| Size | Size (if provided) |
| Scale | Scale (if provided) |

---

## Design Principles

- The **diagram is authoritative**
- No hidden logic or naming rules
- Changes are made in draw.io, not in code
- The script is intentionally modular to allow future extensions

---

## Roadmap (Planned)

- Target metadata generation
- Source-to-target mapping generation
- Model merging (shared hubs across models)
- Entity template enforcement

---

## License

Internal / personal project – no license defined yet.
