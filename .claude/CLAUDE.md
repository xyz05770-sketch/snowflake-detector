# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup / running

```
pip install -r requirements.txt
python src/sf_detector.py --help
python src/semantic_view_builder.py --help
```

No build step, linter, or test suite — these are single scripts, kept
deliberately simple (no package structure, no automated tests). One
exception to the "dependency-light" spirit: `semantic_view_builder.py`'s
`--description` table shortlisting uses `sentence-transformers`
(`all-mpnet-base-v2`) for local semantic matching, which pulls in
`torch`/`transformers`/`numpy` transitively — the model is downloaded from
HuggingFace Hub on first use (needs internet once) and cached locally after
that. That import is lazy and scoped to the `--description` path only, and
fails loudly with a clear message (rather than crashing with a raw
traceback or silently degrading) if the import or model load fails, so
`--tables`/`--from-review` flows are unaffected either way. Verify changes
by running the relevant script's `--help` and, where a live Snowflake
connection isn't available, by exercising individual functions with
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
  `views.json` output, plus live profiling (exact `COUNT(DISTINCT)` — not
  `APPROX_COUNT_DISTINCT`, so classification is deterministic across repeated
  runs on unchanged data — + sample values) of shortlisted columns. Column
  role classification (key/fact/
  dimension) and relationship inference (`<X>_ID` → `<X>S.ID`) are heuristic
  — the generated SQL is a draft to be reviewed, not guaranteed-correct
  output. `render_sql()` is kept isolated from the classification logic so
  the SQL grammar can be corrected in one place if Snowflake's syntax changes.
  Per Snowflake's `CREATE SEMANTIC VIEW` grammar, `FACTS`/`DIMENSIONS`/
  `METRICS` entries are all `<table_alias>.<new_name> AS <sql_expr>` — the
  new semantic name (here, always `<TABLE>_<COLUMN>` to stay unique across
  tables that share column names like `STATE`/`QUARTER`) comes before `AS`,
  the real column/aggregate expression comes after. Table and column
  `COMMENT`s captured from the source schema are carried through into
  `COMMENT='...'` on the corresponding `TABLES`/`FACTS`/`DIMENSIONS` entries
  (a fact's comment is also reused for its derived `METRICS` entry) —
  Cortex Analyst/Agent reads these as interpretation instructions, not just
  human-facing notes, so a column with a real, well-written comment in
  Snowflake is a meaningfully stronger signal than the same column with
  none. Two things this tool
  can't verify and the user should check before running generated SQL:
  `RELATIONSHIPS` isn't supported on every Snowflake account/version (a
  syntax error there means the account doesn't support the clause, not a
  bug in this tool), and joined tables at mismatched grain can fail at
  creation time ("Dimension entity must be related to and have an equal or
  lower level of granularity") since role/relationship inference doesn't
  check grain compatibility — validate incrementally (one table's
  dimensions, then metrics, then the next table) rather than running a
  large multi-table draft all at once. The Python script itself never
  invents comment text — filling in a natural-language description for a
  table/fact/dimension/metric that has no real Snowflake comment is the
  `build-semantic-view` skill's job (its step 3.5), not this script's:
  Claude generates the description during the skill session and writes it
  into the review JSON's existing `comment` fields (fill-gaps-only — a real
  Snowflake comment is never overwritten), so it flows through the same
  `_comment_clause()`/`_col_comment()` machinery described above. This
  keeps the script itself free of any LLM dependency and matches Cortex
  Analyst/Agent's treatment of comments as interpretation instructions
  (worth every entry having one).
  Table shortlisting from a natural-language `--description` uses local
  `sentence-transformers` embeddings (cosine similarity against each table's
  name + comment + column names/comments) with a self-calibrating cutoff
  over the sorted scores: it finds the split point that minimizes combined
  within-group variance (Jenks natural breaks / 1D k-means for two groups),
  not the single largest gap between neighboring scores — the largest raw
  gap can fall inside the truly-relevant group rather than at its boundary.
  No absolute score threshold is hardcoded, and ranked scores are printed to
  the console for transparency. `--tables`/`--from-review` never load the
  embedding model.
  Invoked via the `.claude/skills/build-semantic-view` skill.

Both scripts support three Snowflake auth methods (`externalbrowser`,
`password`, `keypair`) selected via `--auth-method`, and load connection
settings from `.env`/`.secrets` (see `templates/.env.example`) with
precedence `--env-file` > `.secrets` > `.env` > already-exported shell env
vars taking priority over all of the above.

`templates/` holds versioned example/input files; the actual working copies
(`.env`, `input.json`, scope files) live at the project root and are
gitignored.
