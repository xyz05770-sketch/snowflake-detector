# Commands

Raw CLI reference. In Claude Code, `/scan-snowflake-inventory` and
`/build-semantic-view` run these commands for you — use this file if you
prefer running them directly or are scripting outside Claude Code.

## 1. Setup

```
pip install -r requirements.txt
cp templates/.env.example .env
cp templates/input.example.json input.json
```

Edit `.env` (auth) and `input.json` (scope) before running anything below.

## 2. sf_detector — inventory

Pick ONE scope mode and ONE auth mode.

### Scope: JSON file
```
python src/sf_detector.py --account <account> --user <user> --input input.json
```

### Scope: flags
```
python src/sf_detector.py --account <account> --user <user> \
  --databases DB1,DB2 --schemas SCHEMA_A,DB1.SCHEMA_B
```

### Auth: externalbrowser (default, no extra flags)
```
python src/sf_detector.py --account <account> --user <user> --input input.json
```

### Auth: password
```
export SNOWFLAKE_PASSWORD=<password>
python src/sf_detector.py --account <account> --user <user> \
  --auth-method password --input input.json
```

### Auth: keypair
```
python src/sf_detector.py --account <account> --user <user> \
  --auth-method keypair --private-key-path <path-to.p8> --input input.json
```

### Using .env instead of flags (any auth mode)
```
python src/sf_detector.py --input input.json
```

### Optional: filter object types, change output format/dir
```
python src/sf_detector.py --input input.json \
  --object-types TABLES,VIEWS \
  --output-dir ./snowflake_inventory_output --format json
```

## 3. semantic_view_builder — draft a semantic view

Requires step 2 to have been run first (reads its `tables.json`/`views.json`).
Pick ONE scope mode.

### Scope: natural-language description (inline)
```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --account <account> --user <user> \
  --description "customer order analysis" \
  --view-name ORDER_ANALYSIS --output semantic_view.sql
```

### Scope: explicit table list (inline)
```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --account <account> --user <user> \
  --tables DB1.SCHEMA_A.CUSTOMERS,DB1.SCHEMA_A.ORDERS \
  --view-name ORDER_ANALYSIS --output semantic_view.sql
```

### Scope: JSON scope file (description or tables — see templates/)
```
cp templates/semantic_view_scope.description.example.json scope.json
# or: cp templates/semantic_view_scope.tables.example.json scope.json

python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --account <account> --user <user> \
  --scope-file scope.json \
  --view-name ORDER_ANALYSIS --output semantic_view.sql
```

### Any of the above with .env already configured
```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --scope-file scope.json --view-name ORDER_ANALYSIS
```

## 4. semantic_view_builder — review and re-render (no Snowflake needed)

Step 3 also writes an editable review JSON (`ORDER_ANALYSIS_review.json` by
default, or wherever `--review-file` points). Edit its column `"role"`
values, `"primary"` flags, or `relationships`/`metrics`, then re-render:

```
python src/semantic_view_builder.py --from-review ORDER_ANALYSIS_review.json --output semantic_view.sql
```

No `--inventory-dir`/`--scope-file`/connection flags needed or allowed here —
this step is a pure, deterministic function of the review file. Repeat at
most twice; beyond that, edit the rendered SQL directly instead.
