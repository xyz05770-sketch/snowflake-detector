"""Semantic view builder.

Reads the table/view inventory produced by sf_detector.py, shortlists tables
relevant to a described analysis goal (or an explicit table list), profiles
their columns live in Snowflake (cardinality + sample values), heuristically
classifies columns as keys/facts/dimensions, infers relationships between
tables, and renders a draft `CREATE SEMANTIC VIEW` statement for review.

The generated SQL is a best-effort DRAFT. Snowflake's CREATE SEMANTIC VIEW
grammar should be verified against current docs before running it.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from snowflake.connector.errors import ProgrammingError

from sf_detector import get_connection, load_env_file

console = Console()

ID_LIKE_RE = re.compile(r"(^ID$|_ID$|_KEY$)", re.IGNORECASE)
NUMERIC_TYPES = ("NUMBER", "FIXED", "FLOAT", "REAL", "INT", "DECIMAL")

_EMBED_MODEL = None
_EMBED_MODEL_NAME = "all-mpnet-base-v2"


def load_inventory(output_dir):
    """Load and merge tables.json + views.json from an sf_detector output dir."""
    out = Path(output_dir)
    tables = []
    for stem, kind in (("tables", "table"), ("views", "view")):
        path = out / f"{stem}.json"
        if path.exists():
            with open(path) as f:
                for entry in json.load(f):
                    entry["kind"] = kind
                    tables.append(entry)
    if not tables:
        sys.exit(f"No tables.json/views.json found in {output_dir}. Run sf_detector.py first.")
    return tables


def load_scope_file(path):
    """Read a scope file containing either {"description": "..."} or {"tables": [...]}."""
    with open(path) as f:
        spec = json.load(f)
    description = spec.get("description")
    tables = spec.get("tables")
    if not description and not tables:
        sys.exit(f"{path} must contain a non-empty 'description' or 'tables' field.")
    return description, tables


def _get_embedding_model():
    """Lazily load and cache the sentence-transformers model for the life of
    this process. sentence_transformers is imported here (not at module top)
    so --tables/--from-review/--help never require it."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer(_EMBED_MODEL_NAME)
    return _EMBED_MODEL


def _table_text_blob(t):
    parts = [t["name"]]
    if t.get("comment"):
        parts.append(t["comment"])
    for c in t["columns"]:
        parts.append(c["column_name"])
        if c.get("comment"):
            parts.append(c["comment"])
    return " ".join(parts)


def _select_by_elbow(ranked):
    """ranked: list of (score, table) sorted descending by score. Finds the
    cut point that best separates the scores into a "relevant" top group and
    a "less relevant" bottom group, using the same variance-minimization
    idea as Jenks natural breaks / 1D k-means: for every possible cut point,
    measure the combined within-group variance versus one big group, and
    keep the cut that minimizes it.

    This replaced a "cut at the single largest gap between consecutive
    scores" rule that looked reasonable but was empirically wrong: on real
    Snowflake schemas the biggest raw jump between neighboring scores can
    fall *inside* the group of tables that should all be kept (e.g. between
    the 1st and 2nd most-relevant tables), not at the true boundary between
    relevant and irrelevant tables. Variance minimization weighs every
    possible boundary's overall group cohesion rather than a single local
    jump, and correctly finds the true boundary in that situation."""
    if len(ranked) <= 2:
        return ranked
    scores = [s for s, _ in ranked]
    n = len(scores)
    overall_mean = sum(scores) / n
    baseline_ss = sum((s - overall_mean) ** 2 for s in scores)
    if baseline_ss <= 1e-12:
        # All scores are effectively identical - no basis to prefer any subset.
        return ranked
    best_k, best_ss = n, baseline_ss
    for k in range(1, n):
        group1, group2 = scores[:k], scores[k:]
        mean1, mean2 = sum(group1) / len(group1), sum(group2) / len(group2)
        ss = sum((s - mean1) ** 2 for s in group1) + sum((s - mean2) ** 2 for s in group2)
        if ss <= best_ss:
            best_k, best_ss = k, ss
    return ranked[:best_k]


def _print_shortlist_scores(ranked, keep):
    kept_keys = {id(t) for _, t in keep}
    table = Table(title="Semantic Shortlist Ranking (description match)")
    table.add_column("Table")
    table.add_column("Score", justify="right")
    table.add_column("Kept?")
    for score, t in ranked:
        fq = f"{t['database']}.{t['schema']}.{t['name']}"
        table.add_row(fq, f"{score:.3f}", "yes" if id(t) in kept_keys else "-")
    console.print(table)


def shortlist_tables(inventory, description=None, explicit_tables=None):
    if explicit_tables:
        wanted = {t.strip().upper() for t in explicit_tables}
        shortlisted = [
            t for t in inventory
            if f"{t['database']}.{t['schema']}.{t['name']}".upper() in wanted
            or t["name"].upper() in wanted
        ]
        if not shortlisted:
            sys.exit(f"No inventory entries matched --tables: {explicit_tables}")
        return shortlisted

    if description:
        try:
            model = _get_embedding_model()
        except ImportError:
            sys.exit(
                "sentence-transformers is required for --description matching but "
                "isn't installed. Run `pip install -r requirements.txt`, or use "
                "--tables/--scope-file instead."
            )
        except Exception as e:
            sys.exit(
                f"Could not load the sentence-transformers model ({e}). This usually "
                "means no internet access for the one-time model download. Fix that, "
                "or use --tables/--scope-file instead."
            )

        blobs = [_table_text_blob(t) for t in inventory]
        embeddings = model.encode(blobs, normalize_embeddings=True)
        query_vec = model.encode([description], normalize_embeddings=True)[0]
        scores = embeddings @ query_vec
        ranked = sorted(zip(scores.tolist(), inventory), key=lambda sv: sv[0], reverse=True)
        keep = _select_by_elbow(ranked)
        _print_shortlist_scores(ranked, keep)
        return [t for _, t in keep]

    return inventory


def profile_columns(conn, tables):
    """Run one aggregate query per table: row count + exact distinct count per column.

    Uses COUNT(DISTINCT col) rather than APPROX_COUNT_DISTINCT so that role
    classification is deterministic across repeated runs on unchanged data.
    """
    cur = conn.cursor()
    profiles = {}

    for t in tables:
        key = (t["database"], t["schema"], t["name"])
        fq_name = f'{t["database"]}.{t["schema"]}.{t["name"]}'
        col_names = [c["column_name"] for c in t["columns"]]
        select_list = ["COUNT(*) AS ROW_COUNT"] + [
            f'COUNT(DISTINCT "{c}") AS "{c}__distinct"' for c in col_names
        ]
        sql = f"SELECT {', '.join(select_list)} FROM {fq_name}"
        try:
            cur.execute(sql)
            columns = [c[0] for c in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        except ProgrammingError as e:
            console.print(f"[yellow]Skipping profiling for {fq_name}: {e}[/yellow]")
            continue

        row_count = row.get("ROW_COUNT", 0) or 0
        col_profiles = {}
        for c in col_names:
            distinct_count = row.get(f"{c}__distinct", 0) or 0
            samples = []
            try:
                cur.execute(
                    f'SELECT DISTINCT "{c}" FROM {fq_name} WHERE "{c}" IS NOT NULL '
                    f'ORDER BY "{c}" LIMIT 5'
                )
                samples = [r[0] for r in cur.fetchall()]
            except ProgrammingError:
                pass
            col_profiles[c] = {"distinct_count": distinct_count, "samples": samples}

        profiles[key] = {"row_count": row_count, "columns": col_profiles}

    cur.close()
    return profiles


def _col_comment(table, column_name):
    return next(
        (c.get("comment") for c in table["columns"] if c["column_name"] == column_name),
        None,
    )


def classify_columns(table, profile):
    """Heuristically assign each column a role: key (primary/foreign), fact, or dimension."""
    row_count = profile["row_count"] if profile else 0
    roles = {}

    for c in table["columns"]:
        name = c["column_name"]
        data_type = (c.get("data_type") or "").upper()
        col_profile = (profile or {}).get("columns", {}).get(name, {})
        distinct_count = col_profile.get("distinct_count", 0)
        uniqueness_ratio = (distinct_count / row_count) if row_count else 0

        is_id_like = bool(ID_LIKE_RE.search(name))
        is_numeric = any(data_type.startswith(t) for t in NUMERIC_TYPES)

        if is_id_like or (row_count and uniqueness_ratio > 0.95):
            is_primary = name.upper() == "ID" or name.upper() == f"{table['name'].upper().rstrip('S')}_ID"
            roles[name] = {"role": "key", "primary": is_primary}
        elif is_numeric and row_count and uniqueness_ratio > 0.3:
            roles[name] = {"role": "fact"}
        else:
            roles[name] = {"role": "dimension"}

    return roles


def infer_relationships(tables_with_roles):
    """Match foreign-key-shaped columns (CUSTOMER_ID) to another table's primary key (CUSTOMERS.ID)."""
    by_singular_name = {}
    for t in tables_with_roles:
        singular = t["name"].upper().rstrip("S")
        by_singular_name.setdefault(singular, t)

    relationships = []
    for t in tables_with_roles:
        for name, info in t["roles"].items():
            if info["role"] != "key" or info.get("primary"):
                continue
            base = re.sub(r"_ID$", "", name, flags=re.IGNORECASE).upper()
            target = by_singular_name.get(base)
            if target and target["name"] != t["name"]:
                target_pk = next(
                    (n for n, i in target["roles"].items() if i["role"] == "key" and i.get("primary")),
                    None,
                )
                if target_pk:
                    relationships.append(
                        {
                            "from_table": t["name"],
                            "from_column": name,
                            "to_table": target["name"],
                            "to_column": target_pk,
                        }
                    )
    return relationships


def build_review_hints(tables_with_roles, relationships):
    """Flag columns most likely to need a human look before rendering.

    A non-primary "key" is the exact shape that misfires: it's either a real
    foreign key (already explained by an entry in `relationships`) or an
    artifact of the >0.95 uniqueness heuristic misclassifying a measure
    (e.g. AMOUNT, COUNT) - and those are silently excluded from FACTS/
    DIMENSIONS unless reclassified. Surfacing only the *unexplained* ones
    keeps this from just re-listing every key column.
    """
    matched_fk = {(r["from_table"], r["from_column"]) for r in relationships}
    hints = []
    for t in tables_with_roles:
        for name, info in t["roles"].items():
            if info["role"] != "key" or info.get("primary"):
                continue
            if (t["name"], name) in matched_fk:
                continue
            looks_like_fk = bool(re.search(r"_ID$", name, flags=re.IGNORECASE)) or name.upper() == "ID"
            if looks_like_fk:
                hint = (
                    "Looks like a foreign key but no matching table/primary-key was found in scope. "
                    "If it references a table outside --tables/--scope-file, leave as key; otherwise "
                    "add an entry to `relationships` manually."
                )
            else:
                hint = (
                    "Classified as key from a high uniqueness ratio, but it's not a primary key and "
                    "doesn't look like a foreign key. If this is actually a measure, change \"role\" "
                    "to \"fact\" (or \"dimension\") so it isn't silently excluded from the SQL."
                )
            hints.append({"table": t["name"], "column": name, "hint": hint})
            info["hint"] = hint
    return hints


def build_draft(tables, profiles, view_name):
    tables_with_roles = []
    for t in tables:
        key = (t["database"], t["schema"], t["name"])
        roles = classify_columns(t, profiles.get(key))
        tables_with_roles.append({**t, "roles": roles})

    relationships = infer_relationships(tables_with_roles)
    review_hints = build_review_hints(tables_with_roles, relationships)

    facts = [
        {"table": t["name"], "column": name, "comment": _col_comment(t, name)}
        for t in tables_with_roles
        for name, info in t["roles"].items()
        if info["role"] == "fact"
    ]
    dimensions = [
        {"table": t["name"], "column": name, "comment": _col_comment(t, name)}
        for t in tables_with_roles
        for name, info in t["roles"].items()
        if info["role"] == "dimension"
    ]
    metrics = [
        {
            "name": f"total_{f['column'].lower()}",
            "table": f["table"],
            "expression": f"SUM({f['column']})",
            "comment": f.get("comment"),
        }
        for f in facts
    ]

    return {
        "view_name": view_name,
        "tables": tables_with_roles,
        "relationships": relationships,
        "facts": facts,
        "dimensions": dimensions,
        "metrics": metrics,
        "review_hints": review_hints,
    }


VALID_ROLES = {"key", "fact", "dimension"}


def emit_review(draft, path):
    """Write the draft as a human-editable JSON review file.

    This is the artifact the user is expected to open and correct (roles,
    primary-key flags, relationships, metrics) before rendering the final
    SQL via load_review()/render_sql() — no reprofiling or reclassification
    involved in that second pass, so the same edited file always renders to
    the same SQL.
    """
    with open(path, "w") as f:
        json.dump(draft, f, indent=2, default=str)


def load_review(path):
    """Read a (possibly hand-edited) review JSON back into render_sql()'s draft shape.

    Per-table `roles` is the single source of truth: `facts`/`dimensions` are
    rebuilt from it so that editing a column's role actually changes the
    rendered SQL. `metrics` are reconciled against the rebuilt facts rather
    than regenerated wholesale, so hand-edited expressions/extra metrics
    survive while newly-promoted facts still get a sensible default.
    """
    with open(path) as f:
        draft = json.load(f)

    for t in draft.get("tables", []):
        for name, info in t.get("roles", {}).items():
            role = info.get("role")
            if role not in VALID_ROLES:
                sys.exit(f"{path}: table {t.get('name')} column {name} has invalid role {role!r} (expected one of {sorted(VALID_ROLES)}).")
            info.pop("hint", None)

    draft["review_hints"] = build_review_hints(draft.get("tables", []), draft.get("relationships", []))

    facts = [
        {"table": t["name"], "column": name, "comment": _col_comment(t, name)}
        for t in draft.get("tables", [])
        for name, info in t.get("roles", {}).items()
        if info["role"] == "fact"
    ]
    dimensions = [
        {"table": t["name"], "column": name, "comment": _col_comment(t, name)}
        for t in draft.get("tables", [])
        for name, info in t.get("roles", {}).items()
        if info["role"] == "dimension"
    ]

    existing_metrics = {(m["table"], m["name"]): m for m in draft.get("metrics", [])}
    default_keys = set()
    metrics = []
    for f in facts:
        default_name = f"total_{f['column'].lower()}"
        default_keys.add((f["table"], default_name))
        if (f["table"], default_name) in existing_metrics:
            metrics.append(existing_metrics[(f["table"], default_name)])
        else:
            metrics.append(
                {
                    "name": default_name,
                    "table": f["table"],
                    "expression": f"SUM({f['column']})",
                    "comment": f.get("comment"),
                }
            )
    metrics += [m for key, m in existing_metrics.items() if key not in default_keys]

    draft["facts"] = facts
    draft["dimensions"] = dimensions
    draft["metrics"] = metrics

    return draft


def _comment_clause(text):
    """Snowflake COMMENT='...' clause, or '' if there's nothing to say.

    Column/table comments are a strong signal for Cortex Analyst/Agent -
    per Snowflake's own guidance, it reads these comments as instructions
    for interpreting the semantic model, not just human-facing notes - so
    every comment captured from the source schema is carried through into
    the generated SQL rather than dropped.
    """
    if not text:
        return ""
    escaped = text.replace("'", "''")
    return f" COMMENT='{escaped}'"


def render_sql(draft):
    """Render a best-effort CREATE SEMANTIC VIEW statement. VERIFY SYNTAX before running."""
    lines = [f"-- DRAFT: verify against current Snowflake CREATE SEMANTIC VIEW syntax before running."]
    lines.append(f"CREATE OR REPLACE SEMANTIC VIEW {draft['view_name']}")

    table_lines = []
    for t in draft["tables"]:
        pk = next((n for n, i in t["roles"].items() if i["role"] == "key" and i.get("primary")), None)
        fq_name = f'{t["database"]}.{t["schema"]}.{t["name"]}'
        pk_clause = f" PRIMARY KEY ({pk})" if pk else ""
        comment_clause = _comment_clause(t.get("comment"))
        table_lines.append(f"    {t['name'].lower()} AS {fq_name}{pk_clause}{comment_clause}")
    lines.append("  TABLES (")
    lines.append(",\n".join(table_lines))
    lines.append("  )")

    if draft["relationships"]:
        rel_lines = [
            f"    {r['from_table'].lower()}_to_{r['to_table'].lower()} AS "
            f"{r['from_table'].lower()} ({r['from_column']}) REFERENCES {r['to_table'].lower()} ({r['to_column']})"
            for r in draft["relationships"]
        ]
        lines.append("  RELATIONSHIPS (")
        lines.append(",\n".join(rel_lines))
        lines.append("  )")

    # Per Snowflake's grammar, FACTS/DIMENSIONS/METRICS are all
    # `<table_alias>.<new_name> AS <sql_expr>` - the new semantic name comes
    # before AS, the expression (here, just the raw column) comes after.
    # Semantic names must be unique across the whole view even though
    # table.column on its own is already unique - tables commonly share
    # column names (QUARTER, STATE, NAME, ...) - so facts/dimensions are
    # always prefixed with the table name rather than using the bare
    # column name.
    if draft["facts"]:
        fact_lines = [
            f"    {f['table'].lower()}.{f['table'].upper()}_{f['column'].upper()} AS {f['column']}"
            f"{_comment_clause(f.get('comment'))}"
            for f in draft["facts"]
        ]
        lines.append("  FACTS (")
        lines.append(",\n".join(fact_lines))
        lines.append("  )")

    if draft["dimensions"]:
        dim_lines = [
            f"    {d['table'].lower()}.{d['table'].upper()}_{d['column'].upper()} AS {d['column']}"
            f"{_comment_clause(d.get('comment'))}"
            for d in draft["dimensions"]
        ]
        lines.append("  DIMENSIONS (")
        lines.append(",\n".join(dim_lines))
        lines.append("  )")

    if draft["metrics"]:
        metric_lines = [
            f"    {m['table'].lower()}.{m['name']} AS {m['expression']}{_comment_clause(m.get('comment'))}"
            for m in draft["metrics"]
        ]
        lines.append("  METRICS (")
        lines.append(",\n".join(metric_lines))
        lines.append("  )")

    return "\n".join(lines) + ";\n"


def print_summary(draft):
    table = Table(title=f"Semantic View Draft: {draft['view_name']}")
    table.add_column("Table")
    table.add_column("Keys")
    table.add_column("Facts")
    table.add_column("Dimensions")

    for t in draft["tables"]:
        keys = [n for n, i in t["roles"].items() if i["role"] == "key"]
        facts = [n for n, i in t["roles"].items() if i["role"] == "fact"]
        dims = [n for n, i in t["roles"].items() if i["role"] == "dimension"]
        table.add_row(t["name"], ", ".join(keys) or "-", ", ".join(facts) or "-", ", ".join(dims) or "-")
    console.print(table)

    if draft["relationships"]:
        rel_table = Table(title="Inferred Relationships")
        rel_table.add_column("From")
        rel_table.add_column("To")
        for r in draft["relationships"]:
            rel_table.add_row(
                f"{r['from_table']}.{r['from_column']}", f"{r['to_table']}.{r['to_column']}"
            )
        console.print(rel_table)

    if draft.get("review_hints"):
        hint_table = Table(title="Suggested Review Points (see review JSON for details)")
        hint_table.add_column("Table")
        hint_table.add_column("Column")
        hint_table.add_column("Why it's flagged")
        for h in draft["review_hints"]:
            hint_table.add_row(h["table"], h["column"], h["hint"])
        console.print(hint_table)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Draft a Snowflake CREATE SEMANTIC VIEW from an sf_detector inventory."
    )
    parser.add_argument(
        "--env-file",
        help="Path to a .env/.secrets file with connection settings (default: auto-detect .secrets then .env in the CWD).",
    )
    parser.add_argument(
        "--inventory-dir",
        help="sf_detector output directory (contains tables.json/views.json). "
        "Required unless --from-review is given.",
    )
    parser.add_argument(
        "--scope-file",
        help="JSON file with {\"description\": \"...\"} or {\"tables\": [\"db.schema.table\", ...]} "
        "(see templates/semantic_view_scope.*.example.json). Overrides --description/--tables.",
    )
    parser.add_argument("--description", help="Natural-language description of the analysis goal, used to shortlist tables.")
    parser.add_argument("--tables", help="Comma-separated explicit table list (name or db.schema.name).")
    parser.add_argument(
        "--from-review",
        help="Render SQL directly from a (possibly hand-edited) review JSON emitted by a prior run, "
        "skipping the inventory/profiling/classification steps entirely. No Snowflake connection needed. "
        "Mutually exclusive with --inventory-dir/--scope-file/--description/--tables.",
    )
    parser.add_argument(
        "--review-file",
        help="Path to write the editable classification review JSON to (default: <output>_review.json). "
        "Ignored when --from-review is given.",
    )
    parser.add_argument("--view-name", default="MY_SEMANTIC_VIEW", help="Name for the generated semantic view.")
    parser.add_argument("--output", default="./semantic_view.sql", help="Path to write the draft SQL.")

    parser.add_argument("--account", default=os.environ.get("SNOWFLAKE_ACCOUNT"))
    parser.add_argument("--user", default=os.environ.get("SNOWFLAKE_USER"))
    parser.add_argument("--role", default=os.environ.get("SNOWFLAKE_ROLE"))
    parser.add_argument("--warehouse", default=os.environ.get("SNOWFLAKE_WAREHOUSE"))
    parser.add_argument(
        "--auth-method",
        choices=["externalbrowser", "password", "keypair"],
        default=os.environ.get("SNOWFLAKE_AUTH_METHOD", "externalbrowser"),
    )
    parser.add_argument("--private-key-path", default=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"))

    return parser


def main():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file")
    pre_args, _ = pre_parser.parse_known_args()
    load_env_file(pre_args.env_file)

    args = build_arg_parser().parse_args()

    if args.from_review:
        if args.inventory_dir or args.scope_file or args.description or args.tables:
            sys.exit("--from-review cannot be combined with --inventory-dir/--scope-file/--description/--tables.")
        draft = load_review(args.from_review)
        sql = render_sql(draft)
        Path(args.output).write_text(sql)
        emit_review(draft, args.from_review)
        console.print(f"[green]Rendered semantic view SQL written to {Path(args.output).resolve()}[/green]")
        console.print(f"[green]Reconciled facts/dimensions/metrics/review_hints written back to {Path(args.from_review).resolve()}[/green]")
        print_summary(draft)
        return

    if not args.inventory_dir:
        sys.exit("--inventory-dir is required (or use --from-review to render from an existing review file).")

    account = args.account
    user = args.user
    role = args.role
    warehouse = args.warehouse
    auth_method = args.auth_method
    private_key_path = args.private_key_path

    if not account or not user:
        sys.exit("--account and --user are required (or set SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER).")

    inventory = load_inventory(args.inventory_dir)

    description = args.description
    explicit_tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    if args.scope_file:
        description, explicit_tables = load_scope_file(args.scope_file)

    shortlisted = shortlist_tables(inventory, description=description, explicit_tables=explicit_tables)

    conn = get_connection(
        account=account,
        user=user,
        role=role,
        warehouse=warehouse,
        auth_method=auth_method,
        private_key_path=private_key_path,
    )
    try:
        profiles = profile_columns(conn, shortlisted)
    finally:
        conn.close()

    draft = build_draft(shortlisted, profiles, args.view_name)
    sql = render_sql(draft)

    output_path = Path(args.output)
    review_path = Path(args.review_file) if args.review_file else output_path.with_name(f"{output_path.stem}_review.json")
    emit_review(draft, review_path)

    output_path.write_text(sql)
    console.print(f"[green]Draft semantic view SQL written to {output_path.resolve()}[/green]")
    console.print(f"[green]Editable review file written to {review_path.resolve()}[/green]")
    console.print(
        "[yellow]Review the JSON above, correct any roles/relationships/metrics, then re-run with "
        f"--from-review {review_path} to regenerate SQL deterministically.[/yellow]"
    )
    print_summary(draft)


if __name__ == "__main__":
    main()
