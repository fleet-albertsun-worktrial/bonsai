import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_filepath", default="results")
    return parser.parse_args()


def _launch():
    from streamlit.web import cli

    args = _args()
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), "--",
                "--output_filepath", args.output_filepath]
    raise SystemExit(cli.main())


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _connect(database):
    return sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)


def _load(path):
    statistics = json.loads((path / "statistics.json").read_text())
    with _connect(path / "output.sqlite") as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
    return statistics, tables


def _coerce(value, declared_type):
    if not value:
        return None
    try:
        if "INT" in declared_type.upper():
            return int(value)
        if any(name in declared_type.upper() for name in ("REAL", "FLOA", "DOUB", "NUM")):
            return float(value)
    except ValueError:
        return value
    return value


def _app():
    import streamlit as st

    args = _args()
    st.set_page_config(page_title="Bonsai Results", page_icon="🪴", layout="wide")
    st.title("Bonsai Results")
    path = Path(st.sidebar.text_input("Results directory", args.output_filepath)).expanduser()
    required = [path / "statistics.json", path / "output.sqlite"]
    missing = [file.name for file in required if not file.is_file()]
    if missing:
        st.error(f"Missing from {path}: {', '.join(missing)}")
        st.stop()

    key = str(path.resolve())
    if st.session_state.get("result_path") != key:
        try:
            st.session_state.result_path = key
            st.session_state.statistics, st.session_state.tables = _load(path)
        except (OSError, json.JSONDecodeError, sqlite3.Error) as error:
            st.error(f"Could not load results: {error}")
            st.stop()

    statistics, tables = st.session_state.statistics, st.session_state.tables
    timings = statistics.get("generation_timings") or statistics.get("timings", {})
    row_counts = statistics.get("row_counts", {})
    verifiers = statistics.get("verifiers", [])
    descriptions = {
        "knowledge_base_entities": "Knowledge-base companies and offerings exist.",
        "foreign_keys": "All foreign-key references resolve.",
        "balanced_journals": "Debits equal credits for every journal entry.",
        "document_totals": "Invoice and bill lines reconcile to document totals.",
        "payment_limits": "Payments and applications do not exceed balances.",
        "causal_dates": "Orders, invoices, and payments follow causal date order.",
        "dimensions_and_periods": "Subsidiaries, currencies, and posting periods agree.",
        "quantity_reconciliation": "Ordered, fulfilled, received, and billed quantities reconcile.",
        "revenue_plan_totals": "Revenue-plan lines sum to their plan totals.",
    }
    verifier_rows = [
        {"name": item["name"], "description": descriptions.get(item["name"], ""),
         **{key: value for key, value in item.items() if key != "name"}}
        for item in verifiers
    ]
    elapsed = timings.get("total_seconds", sum(
        value for value in timings.values() if isinstance(value, (int, float))
    ))
    size = statistics.get("database_size_bytes", required[1].stat().st_size)
    passed = sum(bool(item.get("passed")) for item in verifiers)
    metrics = (
        ("Database size", f"{size / 1024**3:.2f} GB"),
        ("Elapsed", f"{elapsed:.2f} s"),
        ("Tables", f"{len(tables):,}"),
        ("Rows", f"{sum(row_counts.values()):,}"),
        ("Model", statistics.get("model", "Unknown")),
        ("Verifiers", f"{passed}/{len(verifiers)} passed"),
    )
    for column, (label, value) in zip(st.columns(len(metrics)), metrics):
        column.metric(label, value)

    with st.expander("Stage timings"):
        st.dataframe([{"stage": key, "seconds": value} for key, value in timings.items()],
                     use_container_width=True, hide_index=True)
    with st.expander("Generation parameters"):
        st.json(statistics.get("parameters", {}))
    with st.expander("Verifier results"):
        st.dataframe(verifier_rows, use_container_width=True, hide_index=True)
    with st.expander("Table row counts"):
        st.dataframe([{"table": table, "rows": row_counts.get(table, 0)} for table in tables],
                     use_container_width=True, hide_index=True)

    st.header("Table browser")
    table = st.selectbox(
        "Table", tables,
        format_func=lambda name: f"{name} ({row_counts.get(name, 0):,})",
    )
    with _connect(required[1]) as db:
        schema = db.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    names, types = [row[1] for row in schema], {row[1]: row[2] for row in schema}
    with st.expander("Schema"):
        st.dataframe([{"column": row[1], "type": row[2], "nullable": not row[3],
                       "default": row[4], "primary_key": bool(row[5])} for row in schema],
                     use_container_width=True, hide_index=True)

    selected = st.multiselect("Columns", names, default=names)
    a, b, c, d = st.columns(4)
    filter_column = a.selectbox("Filter column", ["None", *names])
    filter_value = b.text_input("Filter value")
    sort_column = c.selectbox("Sort column", names)
    direction = d.selectbox("Direction", ["ASC", "DESC"])
    page_size = st.selectbox("Rows per page", [25, 50, 100, 250], index=1)

    where, parameters = "", []
    if filter_column != "None" and filter_value:
        value = _coerce(filter_value, types[filter_column])
        if isinstance(value, str):
            where, parameters = f" WHERE CAST({_quote(filter_column)} AS TEXT) LIKE ?", [f"%{value}%"]
        else:
            where, parameters = f" WHERE {_quote(filter_column)} = ?", [value]
    with _connect(required[1]) as db:
        total = db.execute(f"SELECT COUNT(*) FROM {_quote(table)}{where}", parameters).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=pages, value=1)
    if selected:
        columns = ", ".join(map(_quote, selected))
        query = (f"SELECT {columns} FROM {_quote(table)}{where} "
                 f"ORDER BY {_quote(sort_column)} {direction} LIMIT ? OFFSET ?")
        with _connect(required[1]) as db:
            rows = db.execute(query, [*parameters, page_size, (page - 1) * page_size]).fetchall()
        st.caption(f"{total:,} matching rows · page {page:,} of {pages:,}")
        st.dataframe([dict(zip(selected, row)) for row in rows],
                     use_container_width=True, hide_index=True)
    else:
        st.info("Select at least one column.")


if __name__ == "__main__":
    if any(name.startswith("streamlit") for name in sys.modules):
        _app()
    else:
        _launch()
