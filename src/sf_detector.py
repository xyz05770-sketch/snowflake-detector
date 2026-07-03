"""Snowflake landscape detector.

Given a set of databases (and optionally schemas), connects to Snowflake and
inventories the objects inside them (tables, views, stages, tasks, etc.),
printing a summary to the console and exporting full detail to disk.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import snowflake.connector
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from snowflake.connector.errors import ProgrammingError

console = Console()

# (display_name, SHOW template) — every template is schema-level, taking {database}.{schema}.
OBJECT_TYPES = [
    ("TABLES", "SHOW TABLES IN SCHEMA {database}.{schema}"),
    ("VIEWS", "SHOW VIEWS IN SCHEMA {database}.{schema}"),
    # One row per column of every table/view in the schema, incl. data type.
    # Not exported as its own file — merged into TABLES/VIEWS entries.
    ("COLUMNS", "SHOW COLUMNS IN SCHEMA {database}.{schema}"),
    ("MATERIALIZED VIEWS", "SHOW MATERIALIZED VIEWS IN SCHEMA {database}.{schema}"),
    ("EXTERNAL TABLES", "SHOW EXTERNAL TABLES IN SCHEMA {database}.{schema}"),
    ("DYNAMIC TABLES", "SHOW DYNAMIC TABLES IN SCHEMA {database}.{schema}"),
    ("SEQUENCES", "SHOW SEQUENCES IN SCHEMA {database}.{schema}"),
    ("FILE FORMATS", "SHOW FILE FORMATS IN SCHEMA {database}.{schema}"),
    ("STAGES", "SHOW STAGES IN SCHEMA {database}.{schema}"),
    ("PIPES", "SHOW PIPES IN SCHEMA {database}.{schema}"),
    ("STREAMS", "SHOW STREAMS IN SCHEMA {database}.{schema}"),
    ("TASKS", "SHOW TASKS IN SCHEMA {database}.{schema}"),
    ("FUNCTIONS", "SHOW USER FUNCTIONS IN SCHEMA {database}.{schema}"),
    ("PROCEDURES", "SHOW PROCEDURES IN SCHEMA {database}.{schema}"),
]


def load_env_file(explicit_path=None):
    """Load connection settings from a .secrets/.env file, if any.

    Precedence: --env-file > .secrets > .env. Real shell-exported env vars
    always win over file contents (override=False).
    """
    if explicit_path:
        load_dotenv(explicit_path, override=False)
        return
    for candidate in (".secrets", ".env"):
        if Path(candidate).exists():
            load_dotenv(candidate, override=False)
            return


def get_connection(account, user, role, warehouse, auth_method, private_key_path=None):
    kwargs = dict(account=account, user=user)
    if role:
        kwargs["role"] = role
    if warehouse:
        kwargs["warehouse"] = warehouse

    if auth_method == "externalbrowser":
        kwargs["authenticator"] = "externalbrowser"
    elif auth_method == "password":
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        if not password:
            sys.exit("--auth-method password requires the SNOWFLAKE_PASSWORD env var to be set.")
        kwargs["password"] = password
    elif auth_method == "keypair":
        path = private_key_path or os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        if not path:
            sys.exit("--auth-method keypair requires --private-key-path or SNOWFLAKE_PRIVATE_KEY_PATH.")
        kwargs["private_key"] = load_private_key(path)
    else:
        sys.exit(f"Unknown auth method: {auth_method}")

    return snowflake.connector.connect(**kwargs)


def load_private_key(path):
    from cryptography.hazmat.primitives import serialization

    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(
            f.read(),
            password=passphrase.encode() if passphrase else None,
        )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def parse_scope(args):
    """Return a normalized {database: [schema, ...] or None} dict."""
    if args.input:
        with open(args.input) as f:
            spec = json.load(f)
        databases = spec.get("databases", [])
        schemas_map = spec.get("schemas", {})
        if not databases:
            sys.exit(f"{args.input} must include a non-empty 'databases' list.")
        return {db: schemas_map.get(db) or None for db in databases}

    if not args.databases:
        sys.exit("Provide --input <file.json> or --databases.")

    databases = [d.strip() for d in args.databases.split(",") if d.strip()]
    scope = {db: None for db in databases}

    if args.schemas:
        for raw in args.schemas.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if "." in raw:
                db, schema = raw.split(".", 1)
                if db not in scope:
                    sys.exit(f"Schema '{raw}' references database '{db}' not in --databases.")
                scope[db] = (scope[db] or []) + [schema]
            else:
                for db in databases:
                    scope[db] = (scope[db] or []) + [raw]

    return scope


def resolve_targets(conn, scope):
    """Expand {database: [schema,...] or None} into a list of (database, schema) pairs."""
    targets = []
    cur = conn.cursor()
    for database, schemas in scope.items():
        if schemas:
            targets.extend((database, schema) for schema in schemas)
            continue
        cur.execute(f"SHOW SCHEMAS IN DATABASE {database}")
        columns = [c[0].lower() for c in cur.description]
        for row in cur.fetchall():
            row_dict = dict(zip(columns, row))
            name = row_dict.get("name")
            if name and name.upper() != "INFORMATION_SCHEMA":
                targets.append((database, name))
    cur.close()
    return targets


def parse_data_type(raw):
    """Turn SHOW COLUMNS' JSON data_type (e.g. {"type":"TEXT","length":16777216})
    into a compact string like TEXT(16777216)."""
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    t = d.get("type", raw)
    if "length" in d:
        return f"{t}({d['length']})"
    if "precision" in d and "scale" in d:
        return f"{t}({d['precision']},{d['scale']})"
    return t


def scan(conn, targets, object_types):
    rows_by_type = {name: [] for name, _ in object_types}
    errors = []
    cur = conn.cursor()

    for database, schema in targets:
        for display_name, template in object_types:
            sql = template.format(database=database, schema=schema)
            try:
                cur.execute(sql)
                columns = [c[0].lower() for c in cur.description]
                for row in cur.fetchall():
                    row_dict = dict(zip(columns, row))
                    row_dict["database"] = database
                    row_dict["schema"] = schema
                    if display_name == "COLUMNS" and "data_type" in row_dict:
                        row_dict["data_type"] = parse_data_type(row_dict["data_type"])
                    rows_by_type[display_name].append(row_dict)
            except ProgrammingError as e:
                errors.append((display_name, database, schema, str(e)))

    cur.close()
    results = {name: pd.DataFrame(rows) for name, rows in rows_by_type.items() if rows}
    return results, errors


def build_outputs(results, targets):
    """Turn raw per-object-type scan results into minimal, nested export records."""
    databases_schemas = {}
    for database, schema in targets:
        databases_schemas.setdefault(database, []).append(schema)
    outputs = {
        "databases_schemas": [
            {"database": db, "schemas": sorted(set(schemas))}
            for db, schemas in databases_schemas.items()
        ]
    }

    columns_by_table = {}
    columns_df = results.get("COLUMNS")
    if columns_df is not None:
        for row in columns_df.to_dict("records"):
            key = (row["database"], row["schema"], row.get("table_name"))
            columns_by_table.setdefault(key, []).append(
                {"column_name": row.get("column_name"), "data_type": row.get("data_type")}
            )

    for object_type in ("TABLES", "VIEWS"):
        df = results.get(object_type)
        if df is None:
            continue
        outputs[object_type.lower()] = [
            {
                "database": row["database"],
                "schema": row["schema"],
                "name": row.get("name"),
                "columns": columns_by_table.get((row["database"], row["schema"], row.get("name")), []),
            }
            for row in df.to_dict("records")
        ]

    for object_type, df in results.items():
        if object_type in ("TABLES", "VIEWS", "COLUMNS"):
            continue
        outputs[object_type.lower().replace(" ", "_")] = [
            {"database": row["database"], "schema": row["schema"], "name": row.get("name")}
            for row in df.to_dict("records")
        ]

    return outputs


def print_summary(results, errors):
    table = Table(title="Snowflake Object Inventory")
    table.add_column("Object Type")
    table.add_column("Count", justify="right")

    total = 0
    for name, df in sorted(results.items()):
        table.add_row(name, str(len(df)))
        total += len(df)
    table.add_row("TOTAL", str(total), style="bold")
    console.print(table)

    if errors:
        warn = Table(title="Skipped (errors)")
        warn.add_column("Object Type")
        warn.add_column("Database")
        warn.add_column("Schema")
        warn.add_column("Error")
        for display_name, database, schema, err in errors:
            warn.add_row(display_name, database, schema or "-", err.splitlines()[0])
        console.print(warn)


def export(outputs, output_dir, fmt):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for name, records in outputs.items():
        if not records:
            continue
        path = out / name
        if fmt == "json":
            with open(f"{path}.json", "w") as f:
                json.dump(records, f, indent=2, default=str)
        else:
            df = pd.DataFrame(records)
            if "columns" in df.columns:
                df["columns"] = df["columns"].apply(json.dumps)
            if fmt == "csv":
                df.to_csv(f"{path}.csv", index=False)
            elif fmt == "xlsx":
                df.to_excel(f"{path}.xlsx", index=False)

    console.print(f"[green]Exported results to {out.resolve()}[/green]")


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Inventory objects across Snowflake databases/schemas.")
    parser.add_argument(
        "--env-file",
        help="Path to a .env/.secrets file with connection settings (default: auto-detect .secrets then .env in the CWD).",
    )
    parser.add_argument("--input", help="Path to a JSON file describing databases/schemas (see input.example.json).")
    parser.add_argument("--databases", help="Comma-separated list of databases.")
    parser.add_argument("--schemas", help="Comma-separated list of schemas (bare name or DB.SCHEMA).")
    parser.add_argument("--object-types", help="Comma-separated subset of object types to scan (default: all).")

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

    parser.add_argument("--output-dir", default="./snowflake_inventory_output")
    parser.add_argument("--format", choices=["json", "csv", "xlsx"], default="json")

    return parser


def main():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file")
    pre_args, _ = pre_parser.parse_known_args()
    load_env_file(pre_args.env_file)

    args = build_arg_parser().parse_args()

    if not args.account or not args.user:
        sys.exit("--account and --user are required (or set SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER).")

    object_types = OBJECT_TYPES
    if args.object_types:
        wanted = {t.strip().upper() for t in args.object_types.split(",")}
        object_types = [ot for ot in OBJECT_TYPES if ot[0] in wanted]
        if not object_types:
            sys.exit(f"No matching object types for: {args.object_types}")

    scope = parse_scope(args)

    conn = get_connection(
        account=args.account,
        user=args.user,
        role=args.role,
        warehouse=args.warehouse,
        auth_method=args.auth_method,
        private_key_path=args.private_key_path,
    )
    try:
        targets = resolve_targets(conn, scope)
        if not targets:
            sys.exit("No (database, schema) targets resolved from the given scope.")
        results, errors = scan(conn, targets, object_types)
    finally:
        conn.close()

    print_summary(results, errors)
    outputs = build_outputs(results, targets)
    export(outputs, args.output_dir, args.format)


if __name__ == "__main__":
    main()
