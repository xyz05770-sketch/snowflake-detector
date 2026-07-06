# snowflake-detector

Inventories Snowflake objects (tables, views, stages, tasks, streams, pipes,
functions, procedures, etc.) and drafts a Snowflake `CREATE SEMANTIC VIEW`
statement from that inventory.

> Full walkthrough, CLI reference, and file schemas: **[docs/GUIDE.md](docs/GUIDE.md)**

## Skills (recommended entry point)

| Skill | Does | Under the hood |
| --- | --- | --- |
| `/scan-snowflake-inventory` | Inventory a Snowflake account/database/schema | `src/sf_detector.py` |
| `/build-semantic-view` | Draft a `CREATE SEMANTIC VIEW` statement, incl. review-and-regenerate loop | `src/semantic_view_builder.py` |

Use these in Claude Code instead of running the scripts directly. The
sections below cover raw CLI use.

## Setup

```
pip install -r requirements.txt
cp templates/.env.example .env
cp templates/input.example.json input.json
```

Fill in `.env` (connection details) and `input.json` (scope) — see
[docs/GUIDE.md](docs/GUIDE.md#authentication) for auth options.

## Quick usage

| Task | Command |
| --- | --- |
| Inventory a schema | `python src/sf_detector.py --account <acct> --user <user> --input input.json` |
| Draft semantic view(s) | `python src/semantic_view_builder.py --inventory-dir ./snowflake_inventory_output --scope-file scope.json --output-dir semantic_views_output --account <acct> --user <user>` |
| Re-render after edits | `python src/semantic_view_builder.py --from-review semantic_views_output/<view_name>/semantic_view_review.json` |
| Fresh start | `python cleanup.py` |

Details on scope files, output schemas, auth methods, and the
classify/render workflow: **[docs/GUIDE.md](docs/GUIDE.md)**.

## Starting fresh

`cleanup.py` deletes generated artifacts — everything inside
`snowflake_inventory_output/` and `semantic_views_output/` (every view
folder, however many were built) — without touching `.env`, `input.json`,
or `scope.json`. It's optional (both scripts overwrite their own output on
rerun anyway), but useful for a guaranteed clean slate. It lists what it's
about to delete and asks for confirmation first, since these paths are
gitignored and the delete isn't recoverable via git.

```
python cleanup.py
```

## Project layout

```
.
├── .env                 # your connection settings (gitignored)
├── input.json            # your database/schema scope (gitignored)
├── README.md
├── docs/GUIDE.md          # detailed walkthrough & CLI reference
├── requirements.txt
├── CLAUDE.md
├── src/
│   ├── sf_detector.py            # inventory tool
│   └── semantic_view_builder.py  # semantic view drafting tool
├── templates/
│   ├── .env.example
│   ├── input.example.json
│   └── semantic_view_scope.example.json
└── .claude/skills/build-semantic-view/SKILL.md
```
