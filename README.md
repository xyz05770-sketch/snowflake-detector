# snowflake-detector

Inventories Snowflake objects (tables, views, stages, tasks, streams, pipes,
functions, procedures, etc.) across a set of databases/schemas. Prints a
summary table to the console and exports full detail to disk (JSON by
default; CSV/Excel also supported). Also includes a semantic view builder
that drafts a Snowflake `CREATE SEMANTIC VIEW` statement from that inventory.

## Skills (recommended entry point)

If you're working in Claude Code, use these two skills instead of running
the scripts directly — Claude runs the underlying commands for you:

- `/scan-snowflake-inventory` — inventory a Snowflake account/database/schema
  (`src/sf_detector.py` under the hood).
- `/build-semantic-view` — draft a `CREATE SEMANTIC VIEW` statement from that
  inventory, including the review-and-regenerate loop
  (`src/semantic_view_builder.py` under the hood).

The rest of this README and `COMMANDS.md` document the raw CLI for direct/
scripted use.

## Project layout

```
.
├── .env                 # your connection settings (gitignored)
├── input.json            # your database/schema scope (gitignored)
├── README.md
├── requirements.txt
├── CLAUDE.md
├── src/
│   ├── sf_detector.py            # inventory tool
│   └── semantic_view_builder.py  # semantic view drafting tool
├── templates/
│   ├── .env.example
│   ├── input.example.json
│   ├── semantic_view_scope.description.example.json
│   └── semantic_view_scope.tables.example.json
└── .claude/skills/build-semantic-view/SKILL.md
```

`.env`/`input.json` at the root are your own working copies (both
gitignored); the `templates/` folder holds the versioned examples they're
copied from.

## Setup

```
pip install -r requirements.txt
cp templates/.env.example .env
cp templates/input.example.json input.json
```

Edit both to fill in your connection details and scope, then fill in the
values described below.

## Usage

Specify scope with flags:

```
python src/sf_detector.py --account <account> --user <user> \
  --databases DB1,DB2 --schemas SCHEMA_A,DB1.SCHEMA_B
```

- A bare schema name (`SCHEMA_A`) applies to every listed database.
- A qualified name (`DB1.SCHEMA_B`) applies to just that database.
- Omit `--schemas` for a database to scan every schema in it.

Or specify scope with a JSON file (see `templates/input.example.json`):

```
python src/sf_detector.py --account <account> --user <user> --input input.json
```

```json
{
  "databases": ["DB1", "DB2"],
  "schemas": {
    "DB1": ["SCHEMA_A", "SCHEMA_B"]
  }
}
```

`DB2` has no entry, so every schema in it is scanned.

## Authentication

Controlled by `--auth-method` (default `externalbrowser`):

- `externalbrowser` — SSO via browser popup, no secrets needed.
- `password` — reads `SNOWFLAKE_PASSWORD` from the environment.
- `keypair` — reads a PEM private key from `--private-key-path` (or
  `SNOWFLAKE_PRIVATE_KEY_PATH`); optional passphrase via
  `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`.

`--account`, `--user`, `--role`, `--warehouse` all fall back to
`SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_USER` / `SNOWFLAKE_ROLE` /
`SNOWFLAKE_WAREHOUSE` env vars.

### Configuring via .env / .secrets

Instead of passing flags or exporting env vars by hand, copy
`templates/.env.example` to `.env` (or `.secrets`) in the project root and
fill in the values for whichever auth method you're using:

```
cp templates/.env.example .env
```

On startup, both `sf_detector.py` and `semantic_view_builder.py`
automatically load env vars from, in order of precedence:

1. `--env-file <path>`, if given
2. `.secrets` in the current directory, if present
3. `.env` in the current directory, if present

Values already exported in your shell always take priority over anything in
the file. Both `.env` and `.secrets` are covered by `.gitignore` so they
won't be committed accidentally.

Each auth method only needs its own fields set — see the three commented
blocks in `templates/.env.example`:

- `externalbrowser`: just `SNOWFLAKE_ACCOUNT`/`SNOWFLAKE_USER` (+ `SNOWFLAKE_AUTH_METHOD=externalbrowser`).
- `password`: adds `SNOWFLAKE_PASSWORD`.
- `keypair`: adds `SNOWFLAKE_PRIVATE_KEY_PATH` (and optionally `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`).

## Output

Results land in `--output-dir` (default `./snowflake_inventory_output`), one
file per object type — only created if that type has any objects in scope.
Use `--format csv|xlsx` to change format from the JSON default.

- `databases_schemas.json` — one entry per database with the schemas scanned under it:
  ```json
  [{"database": "DB1", "schemas": ["SCHEMA_A", "SCHEMA_B"]}]
  ```
- `tables.json` / `views.json` — one entry per table/view with its full column schema nested inline:
  ```json
  [{
    "database": "DB1",
    "schema": "SCHEMA_A",
    "name": "CUSTOMERS",
    "columns": [
      {"column_name": "ID", "data_type": "NUMBER(38,0)"},
      {"column_name": "NAME", "data_type": "TEXT(100)"}
    ]
  }]
  ```
- All other object types (`stages.json`, `pipes.json`, `streams.json`, `tasks.json`,
  `functions.json`, `procedures.json`, `sequences.json`, `file_formats.json`,
  `materialized_views.json`, `external_tables.json`, `dynamic_tables.json`) —
  minimal `database`/`schema`/`name` entries.

Object types that error out (insufficient privileges, feature not enabled)
are skipped and reported in a warnings table on the console rather than
aborting the run.

## Semantic view builder

`src/semantic_view_builder.py` drafts a Snowflake native `CREATE SEMANTIC VIEW`
statement from the `tables.json`/`views.json` produced above. It shortlists
relevant tables (by natural-language description or an explicit
`db.schema.table` list), profiles their columns live in Snowflake
(exact `COUNT(DISTINCT)` + ordered sample values, for reproducible
classification) to heuristically classify columns as keys/facts/dimensions,
infers relationships by matching `<X>_ID`-style foreign keys to another
table's primary key, and renders a draft SQL file plus a console summary for
review.

### Two modes: classify, then render

Running the tool against live data (**classify mode**, below) is the only
step that touches Snowflake or involves any heuristics. It writes two files:
the draft SQL and an editable **review JSON** (`--review-file`, default
`<output-stem>_review.json`) containing every column's classification —
including columns the heuristic dropped from the draft SQL (e.g. numeric
measures misclassified as `key`), so nothing is silently hidden.

The console output and the review JSON both include a **Suggested Review
Points** list flagging the columns most worth checking: non-primary `key`
columns that either look like an unmatched foreign key or don't look like a
key at all (a likely sign the uniqueness-ratio heuristic mistook a measure
for a key). Start there instead of reading every column.

To correct a misclassification, edit the review JSON's `"role"` values
(`key`/`fact`/`dimension`), `"primary"` flags, or `relationships`/`metrics`
lists, then re-render in **render mode** — no Snowflake connection, no
reprofiling, and no re-running the heuristics:

```
python src/semantic_view_builder.py --from-review semantic_view_review.json --output semantic_view.sql
```

Render mode is a pure function of the review file: the same review JSON
always produces byte-identical SQL. This is the intended way to fix
classification issues — not hand-editing the generated SQL directly, which
isn't reproducible if the tool is ever re-run from scratch.

You can provide the scope either inline on the command line or via a JSON
file with `--scope-file` — useful for saving/reusing/editing scope without
retyping it. Two templates are provided, one per mode:

- `templates/semantic_view_scope.description.example.json` — natural-language mode:
  ```json
  {
    "description": "Customer order analysis: orders, customers, order line items, and returns/refunds. Interested in revenue, order counts, and customer signup trends."
  }
  ```
- `templates/semantic_view_scope.tables.example.json` — explicit table list mode:
  ```json
  {
    "tables": [
      "DB1.SCHEMA_A.CUSTOMERS",
      "DB1.SCHEMA_A.ORDERS",
      "DB1.SCHEMA_A.ORDER_ITEMS"
    ]
  }
  ```

Copy whichever fits, edit it, and pass it via `--scope-file` (it takes
precedence over `--description`/`--tables` if both are given):

```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --scope-file scope.json \
  --view-name ORDER_ANALYSIS --output semantic_view.sql \
  --account <account> --user <user>
```

Or pass scope inline without a file:

```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --description "customer order analysis" \
  --view-name ORDER_ANALYSIS --output semantic_view.sql \
  --account <account> --user <user>
```

Classify mode reuses the same connection flags and `.env`/`.secrets` config
as `sf_detector.py`. The generated SQL is a **best-effort draft** — verify it
against current Snowflake `CREATE SEMANTIC VIEW` syntax before running it.
See `.claude/skills/build-semantic-view/SKILL.md` for the guided flow,
including the recommended 2-round cap on review-edit-rerender cycles before
just fixing the SQL directly.
