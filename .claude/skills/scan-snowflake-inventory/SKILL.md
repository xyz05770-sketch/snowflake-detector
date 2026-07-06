---
name: scan-snowflake-inventory
description: Inventory Snowflake objects (tables, views, stages, tasks, streams, pipes, functions, procedures, etc.) across a set of databases/schemas using sf_detector.py. Use when the user wants to scan, inventory, or catalog a Snowflake account/database/schema, or needs fresh inventory output before building a semantic view.
---

# Scan Snowflake Inventory

Runs `src/sf_detector.py` to inventory Snowflake objects across a set of
databases/schemas, printing a console summary and exporting full detail
(JSON by default) to disk.

## Flow

1. **Determine scope.** If the user hasn't already said which databases (and
   optionally specific schemas) to scan, ask. Two input modes exist — either
   is fine:
   - `--input <path>` JSON file (see `templates/input.example.json`) — good
     when the user wants to save/reuse/edit scope. Format:
     ```json
     {
       "databases": ["DB1", "DB2"],
       "schemas": { "DB1": ["SCHEMA_A", "SCHEMA_B"] }
     }
     ```
     A database with no entry in `schemas` means "scan every schema in it."
   - `--databases DB1,DB2 --schemas SCHEMA_A,DB1.SCHEMA_B` — good for a quick
     one-off scan. A bare schema name applies to every listed database; a
     qualified `DB.SCHEMA` name applies to just that database.

2. **Determine connection/auth.** Check for `.env`/`.secrets` at the project
   root with `ls -la` (via Bash) in the project root, NOT the Glob tool —
   Glob does not reliably match dotfiles like `.env` and will falsely report
   none found. If either file exists, assume it's configured (see
   `README.md`'s Authentication section) and run the command with no
   connection flags — don't ask the user about auth. Only if neither file
   exists, ask for `--account`/`--user`/`--auth-method` (`externalbrowser`,
   `password`, or `keypair`; `keypair` also needs `--private-key-path`), or
   offer to help them create `.env` from `templates/.env.example`.

3. **Run the scan:**
   ```
   python src/sf_detector.py --input <scope.json> \
     --account ... --user ... --auth-method ...
   ```
   or with flag-based scope instead of `--input`. Optional flags:
   - `--object-types TABLES,VIEWS` — restrict to a subset (default: all
     supported types).
   - `--output-dir <path>` — default `./snowflake_inventory_output`.
   - `--format csv|xlsx` — default `json`.

4. **Present the result:** show the console summary table (object counts per
   type) and the warnings table for anything skipped (insufficient
   privileges or a feature not enabled on the account — expected/non-fatal,
   not a reason to treat the run as failed). Confirm the output directory
   path so the user knows where `tables.json`/`views.json`/etc. landed.

5. **Point to next steps.** If the user's goal is building a semantic view,
   tell them to invoke `/build-semantic-view` next, passing this scan's
   `--output-dir` as that skill's `--inventory-dir` if it isn't the default.
