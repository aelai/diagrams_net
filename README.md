# diagrams_net – IRiS Metadata Generator

This project generates **IRiS metadata Excel files** directly from **diagrams.net (draw.io) Data Vault models**.

The diagram is treated as the **single source of truth** for:
- Table names
- Column names
- Relationships
- Vault type (Raw Vault vs Business Vault)

No renaming or inference is performed beyond clearly defined, documented rules.

---

## What This Tool Generates

For each model file, the generator produces:

- **Source metadata XLSX**
- **Target metadata XLSX**
- **Source-to-Target Mapping XLSX**

Each model is processed independently.

---

## Project Structure

```
.
├── diagrams_to_iris_metadata_files_generator.py
├── IRiS_DV_Modelling.xml
├── model/
│   └── <model>.drawio.xml
├── output/
│   └── <MODEL_NAME>/
│       ├── source_<MODEL_NAME>.xlsx
│       ├── target_<MODEL_NAME>.xlsx
│       └── mapping_<MODEL_NAME>.xlsx
└── README.md
```

### Folders

- **`model/`**  
  Contains draw.io XML exports (`.drawio.xml` or `.xml`).  
  Each file represents **one logical model** and **one source table**.

- **`output/`**  
  Generated artifacts. A subfolder is created per model using the model filename.

- **`IRiS_DV_Modelling.xml`**  
  The draw.io template library used to identify Data Vault object types (Hub, Link, Satellite, Same-as Link, Dependent Child Satellite, Multi-Active Satellite, etc.).  
  This file must reside in the **same directory as the Python script**.

---

## Diagram Requirements

### Source Tables

To be detected as a **source table**, the diagram must contain a **draw.io table shape** with a header row including:

- `Column`
- `Datatype`

Optional columns:
- `Size`
- `Scale`
- `Target`

Notes:
- Empty cells are supported
- Only **source attributes** are written to the Source XLSX
- The `Target` column is used internally for Target + Mapping generation
- Each model file must contain **exactly one source table**

---

## Data Vault Naming Conventions

The generator relies on **explicit naming conventions** to determine object type and behaviour.

### Object Prefixes

| Object Type | Prefix |
|------------|--------|
| Hub | `h_` |
| Link | `l_` |
| Satellite | `s_` |

### Satellite Specialisations

| Satellite Type | Prefix |
|---------------|--------|
| Standard Satellite | `s_` |
| Dependent Child Satellite | `s_dc_` |
| Multi-Active Satellite | `s_ma_` |

These prefixes **must appear immediately after `s_`**.

---

## Business Vault (BV) Naming

Business Vault objects are identified **purely by naming**, not by separate templates.

### Rule

Business Vault is expressed as part of the **table name**, using the token:

```
bv
```

The token appears **after any satellite specialisation** and before the business concept.

### Correct Examples

#### Hubs

- `h_customer` (Raw Vault)
- `h_bv_customer` (Business Vault)

#### Links

- `l_customer_order`
- `l_bv_customer_order`
- `l_sa_bv_customer` (Same-as + Business Vault)

#### Satellites

Raw Vault:
- `s_customer_details`
- `s_dc_customer_address`
- `s_ma_customer_email`

Business Vault:
- `s_bv_customer_details`
- `s_dc_bv_customer_address`
- `s_ma_bv_customer_email`

### Why This Works

- No template duplication is required
- All existing logic (same-as, dependent child, multi-active) continues to work
- Business Vault becomes a namespace, not a new structural type

---

## Same-as Links

Same-as links are detected by:

- Naming prefixes: `l_sa_`, `l_same_`, `l_sameas_`
- Or matching the **Same-as Link** template in `IRiS_DV_Modelling.xml` (located alongside the Python script)

### Behaviour

For same-as links:
- Multiple mappings to the same hub are allowed
- The link Target + Mapping metadata contains **multiple BKCC/BK pairs**
- Link column names are derived from **source column names**, not hub concepts

---

## Generated Excel Files

### Source Metadata XLSX

Columns:

| Column | Description |
|------|-------------|
| Table Schema | Schema supplied at runtime |
| Table Name | Source table name |
| Column | Source column name |
| Datatype | Source datatype |
| Size | Size (if supplied) |
| Scale | Scale (if supplied) |

---

### Target Metadata XLSX

The Target XLSX contains **Hub, Link, and Satellite definitions**.

Rules:
- All BKCC and business key columns default to `varchar(100)`
- Satellite attributes inherit datatype/size/scale from the source table
- Dependent child satellites use subtype `dependent child`
- Multi-active satellites use subtype `multi active`
- Special column types supported:
  - `Source extract date`
  - `Dependent child key`

---

### Mapping Metadata XLSX

Columns:

| Column | Description |
|------|-------------|
| Source Table | Source table name |
| Source Column | Source column name |
| Target Table | Target DV table |
| Target Column | Target DV column |
| Mapping Set Name | Derived from model name |

Same-as links generate **multiple mapping rows per hub** when applicable.

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

Output:

```
output/
└── service_servicessystem/
    ├── source_service_servicessystem.xlsx
    ├── target_service_servicessystem.xlsx
    └── mapping_service_servicessystem.xlsx
```

---

### Run for All Models

```bash
python diagrams_to_iris_metadata_files_generator.py --schema landing --all
```

Notes:
- Only files ending in **`.drawio.xml`** inside the `model/` folder are processed
- Template libraries such as `IRiS_DV_Modelling.xml` must **not** be placed in `model/`

Each model file will be processed independently.

---

## Design Principles

- The **diagram is authoritative**
- No implicit inference beyond documented naming rules
- Changes are made in draw.io, not in Excel
- Logic is modular and extensible

---


## Merging Multiple Models into a Single Diagram

In addition to generating IRiS metadata Excel files, this project includes a **model merge utility** that combines multiple draw.io Data Vault models into **one consolidated draw.io diagram**.

The merged diagram is intended for **visualisation and analysis only**. It is a **derived artefact** and should not be manually edited.

---

### Merge Script

```
merge_drawio_models.py
```

This script:
- Reads **multiple draw.io model files**
- Merges them into **one valid draw.io diagram**
- Removes duplicate Data Vault objects
- Preserves all relationships
- Includes all source tables

The output can be opened directly in **diagrams.net (draw.io)**.

---

### What Gets Merged

The script processes all files ending in:

```
.drawio.xml
```

from the input folder.

#### Deduplication Rules

| Object Type | Deduplication Rule |
|------------|--------------------|
| Hub | One hub per unique hub name |
| Link | One link per unique link name |
| Satellite | One satellite per unique satellite name |
| Source Tables | **Not deduplicated** (all are kept) |

- Object identity is based **only on the table name** shown in the diagram
- Relationships (edges) are merged as a union
- No inference or renaming is performed

---

### Project Structure (with Merge Output)

```
.
├── diagrams_to_iris_metadata_files_generator.py
├── merge_drawio_models.py
├── IRiS_DV_Modelling.xml
├── model/
│   └── <model>.drawio.xml
├── output/
│   └── <MODEL_NAME>/
│       ├── source_<MODEL_NAME>.xlsx
│       ├── target_<MODEL_NAME>.xlsx
│       └── mapping_<MODEL_NAME>.xlsx
├── merged_output/
│   └── merged.drawio.xml
└── README.md
```

---

### How the Merge Works (Conceptual)

1. Each input model is decoded into an internal `mxGraphModel`
2. Data Vault objects (Hubs, Links, Satellites) are identified by name
3. Objects with the same name are merged into a single node
4. All relationships between objects are preserved
5. All source tables are copied into the merged diagram
6. All internal draw.io IDs are regenerated to guarantee uniqueness
7. A new, valid draw.io file is written

The merged file is always **fully regenerated** and **overwritten** on each run.

---

### How to Run the Merge

From the project root:

```bash
python merge_drawio_models.py
```

Optional arguments:

```bash
python merge_drawio_models.py \
  --input-dir model \
  --output-dir merged_output \
  --output merged.drawio.xml \
  --verbose
```

Defaults:
- Input folder: `./model`
- Output folder: `./merged_output`
- Output file: `merged.drawio.xml`

---

### Important Notes

- The merged diagram is **not** used to generate IRiS metadata
- Metadata generation always operates on **individual model files**
- The merged diagram is for:
  - cross-model visualisation
  - impact analysis
  - design review
- The merged file should not be manually edited

---

### Design Philosophy

- Individual models remain small, focused, and source-aligned
- The merged model provides a **holistic enterprise view**
- Diagrams remain the single source of truth
- All automation is repeatable and deterministic

---

## Roadmap

- Cross-model hub consolidation
- Model merge visualisation
- Validation rules and warnings
- Optional vault-layer reporting

---

## License

Internal / personal project – no license defined yet.
