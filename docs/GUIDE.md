# Detailed guide

Full walkthrough, CLI reference, and file schemas for `snowflake-detector`.
See the [README](../README.md) for the quick-start version.

## How it all fits together

If you've never used this tool before, here's the whole journey from "empty
folder" to "semantic view SQL ready to run in Snowflake," in plain terms.

**Step 1 — Inventory your Snowflake schema** (`/scan-snowflake-inventory`)
Connects to Snowflake and lists everything in the databases/schemas you
point it at — tables, views, and more — along with each table's columns.
Nothing here uses AI; it's plain SQL (`SHOW ...` commands) run through the
official `snowflake-connector-python` library. The result is saved as JSON
files on your machine (e.g. `tables.json`), so this step never needs to be
repeated unless your schema changes.

**Step 2 — Draft a semantic view** (`/build-semantic-view`, classify mode)
Reads the JSON from step 1, figures out which tables are relevant to what
you're trying to analyze (either from a plain-English description or a list
of table names you give it), and inspects those tables' actual data —
counting distinct values and pulling a few samples per column — to guess
which columns are keys, which are measures ("facts," like a dollar amount
or a count), and which are descriptive groupings ("dimensions," like a
state or a date). This step is the only one that talks to Snowflake and the
only one that uses heuristics/guesswork — everything after it works from
the saved results instead. It produces a first-draft SQL file plus a
companion "review" JSON you can edit if something was misclassified.

**Step 3 — Fill in missing descriptions** (part of the same skill, done by
Claude)
Snowflake lets you attach a plain-English `COMMENT` to a table or column,
and Snowflake's Cortex Analyst/Agent uses those comments to understand what
your data means — not just as a nice-to-have. Most real schemas don't have
comments on every column, so Claude writes a short, sensible description
for anything that's missing one, using the column's name, type, and
neighboring columns as clues. Anything that already has a real comment in
Snowflake is left untouched — this step only fills gaps, never overwrites.
No external AI service is called for this; it's Claude itself, during the
skill session.

**Step 4 — Review and fix (optional)**
If something looks wrong — say, a column was guessed to be a unique "key"
when it's actually a measure you want to total up — you (or Claude, on your
behalf) tweak the review JSON and re-render the SQL. This step is instant
and needs no Snowflake connection at all, since it just re-reads the saved
review file.

**Step 5 — Run it in Snowflake (manual, your call)**
The tool never runs the generated `CREATE SEMANTIC VIEW` statement for you.
Once you're happy with the draft, you copy it into Snowflake yourself,
ideally a piece at a time, since semantic view syntax can be picky about
how tables relate to each other.

| Step | What happens | Tech/tools used |
| --- | --- | --- |
| 1. Inventory | List tables/views/columns | `snowflake-connector-python`, plain `SHOW` SQL |
| 2. Draft & classify | Shortlist tables, profile columns, guess roles | `snowflake-connector-python` (live queries); `sentence-transformers` (only if you scope by description instead of table names) |
| 3. Fill descriptions | Write missing comments, gap-fill only | Claude itself, editing the review JSON directly (no external AI call) |
| 4. Review & fix | Correct any misclassified column | Plain JSON editing, re-rendered with no Snowflake connection needed |
| 5. Apply | Run the SQL for real | You, pasting into a Snowflake worksheet |

Steps 1–4 are driven by the two Claude Code skills; see
[Semantic view builder](#semantic-view-builder) below for the equivalent raw
CLI commands if you're scripting this outside Claude Code.

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
  "databases": [
    "DB2",
    {"database": "DB1", "schemas": ["SCHEMA_A", "SCHEMA_B"]}
  ]
}
```

Each entry in `databases` is either a bare database name (every schema in it
is scanned) or a `{"database": ..., "schemas": [...]}` object restricting it
to specific schemas — `DB2` above has no schema restriction, `DB1` is
limited to `SCHEMA_A`/`SCHEMA_B`, and the database name is never repeated.

## Authentication

Controlled by `--auth-method` (default `externalbrowser`):

| Method | Needs |
| --- | --- |
| `externalbrowser` | SSO via browser popup, no secrets needed |
| `password` | `SNOWFLAKE_PASSWORD` env var |
| `keypair` | PEM key via `--private-key-path`/`SNOWFLAKE_PRIVATE_KEY_PATH`, optional `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` |

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

| File | Contents |
| --- | --- |
| `databases_schemas.json` | One entry per database with the schemas scanned under it, e.g. `[{"database": "DB1", "schemas": ["SCHEMA_A", "SCHEMA_B"]}]` |
| `tables.json` / `views.json` | One entry per table/view with full column schema nested inline (see example below) |
| `stages.json`, `pipes.json`, `streams.json`, `tasks.json`, `functions.json`, `procedures.json`, `sequences.json`, `file_formats.json`, `materialized_views.json`, `external_tables.json`, `dynamic_tables.json` | Minimal `database`/`schema`/`name` entries |

`tables.json`/`views.json` entry example:

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
step that touches Snowflake or involves any heuristics. A single run can
build **one or several views** — a `--scope-file` may list multiple
`{"view_name", "description"|"tables"}` entries — and each view gets its
own `--output-dir/<view_name>/` folder containing two files: the draft SQL
(`semantic_view.sql`) and an editable **review JSON**
(`semantic_view_review.json`) containing every column's classification —
including columns the heuristic dropped from the draft SQL (e.g. numeric
measures misclassified as `key`), so nothing is silently hidden. Tables
shared by more than one view in the same run are profiled only once.

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
python src/semantic_view_builder.py --from-review semantic_views_output/ORDER_ANALYSIS/semantic_view_review.json
```

Render mode is a pure function of the review file: the same review JSON
always produces byte-identical SQL, written as `semantic_view.sql` in the
same folder as the review file. This is the intended way to fix
classification issues — not hand-editing the generated SQL directly, which
isn't reproducible if the tool is ever re-run from scratch.

You can provide the scope either inline on the command line or via a JSON
file with `--scope-file` — useful for saving/reusing/editing scope without
retyping it, and required if you want to build **more than one view in a
single run**. `templates/semantic_view_scope.example.json` holds a
`"views"` list, one entry per view, each with a `view_name` plus either a
`description` (natural-language mode) or a `tables` list (explicit-table
mode) — mix both freely in the same file:

```json
{
  "views": [
    {
      "view_name": "ORDER_ANALYSIS",
      "description": "Customer order analysis: orders, customers, order line items, and returns/refunds. Interested in revenue, order counts, and customer signup trends."
    },
    {
      "view_name": "HR_HEADCOUNT",
      "database": "DB1",
      "schema": "SCHEMA_A",
      "tables": ["EMPLOYEES", "DEPARTMENTS"]
    }
  ]
}
```

A view entry's optional `database`/`schema` fields are a shorthand: any
table name in `tables` without a `.` in it is resolved against those
defaults, so a view whose tables all live in one schema doesn't need to
repeat that schema on every line. A table string that already contains a
`.` (partial `schema.table` or full `db.schema.table`) is used as-is,
so mixing schemas within one view still works — and even without
`database`/`schema` set, a bare table name is matched against the whole
inventory as long as it's unique (a warning is printed if it isn't).

Copy the template, edit it, and pass it via `--scope-file` (it takes
precedence over `--description`/`--tables`/`--view-name` if given). Each
view lands in its own `--output-dir/<view_name>/` folder:

```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --scope-file scope.json \
  --output-dir semantic_views_output \
  --account <account> --user <user>
```

Or pass scope inline without a file, for a single quick one-off view:

```
python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output \
  --description "customer order analysis" \
  --view-name ORDER_ANALYSIS --output-dir semantic_views_output \
  --account <account> --user <user>
```

Classify mode reuses the same connection flags and `.env`/`.secrets` config
as `sf_detector.py`. The generated SQL is a **best-effort draft** — verify it
against current Snowflake `CREATE SEMANTIC VIEW` syntax before running it.
See `.claude/skills/build-semantic-view/SKILL.md` for the guided flow,
including the recommended 2-round cap on review-edit-rerender cycles before
just fixing the SQL directly.
