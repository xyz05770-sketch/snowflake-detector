# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup / running

```
pip install -r requirements.txt
python src/sf_detector.py --help
python src/semantic_view_builder.py --help
```

No build step, linter, or test suite — these are single, dependency-light
scripts, kept deliberately simple (no package structure, no automated tests).
Verify changes by running the relevant script's `--help` and, where a live
Snowflake connection isn't available, by exercising individual functions with
synthetic data via `python -c "..."`.

## Architecture

Two standalone CLI scripts in `src/`, both reusing Snowflake connection/auth
logic from `sf_detector.py`:

- **`src/sf_detector.py`** — inventories Snowflake objects. Uses
  `SHOW <OBJECT_TYPE> IN SCHEMA <db>.<schema>` uniformly across all object
  types (tables, views, stages, tasks, streams, pipes, functions, procedures,
  etc.) rather than `INFORMATION_SCHEMA`/`ACCOUNT_USAGE`, since it works
  consistently across object types and doesn't need elevated privileges.
  `SHOW COLUMNS` output is merged into the `tables`/`views` output as nested
  column lists rather than exported as its own file. Exports one JSON file
  per object type (only if that type has data in scope).
- **`src/semantic_view_builder.py`** — drafts a Snowflake
  `CREATE SEMANTIC VIEW` statement from `sf_detector.py`'s `tables.json`/
  `views.json` output, plus live profiling (`APPROX_COUNT_DISTINCT` + sample
  values) of shortlisted columns. Column role classification (key/fact/
  dimension) and relationship inference (`<X>_ID` → `<X>S.ID`) are heuristic
  — the generated SQL is a draft to be reviewed, not guaranteed-correct
  output. `render_sql()` is kept isolated from the classification logic so
  the SQL grammar can be corrected in one place if Snowflake's syntax changes.
  Invoked via the `.claude/skills/build-semantic-view` skill.

Both scripts support three Snowflake auth methods (`externalbrowser`,
`password`, `keypair`) selected via `--auth-method`, and load connection
settings from `.env`/`.secrets` (see `templates/.env.example`) with
precedence `--env-file` > `.secrets` > `.env` > already-exported shell env
vars taking priority over all of the above.

`templates/` holds versioned example/input files; the actual working copies
(`.env`, `input.json`, scope files) live at the project root and are
gitignored.
