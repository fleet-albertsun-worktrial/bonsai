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
    path = Path(args.output_filepath).expanduser()
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
        "workflow_provenance": "Every instance belongs to a supported workflow and has executed steps.",
        "receivables_reconciliation": "Invoice payments and open balances reconcile.",
        "payables_reconciliation": "Vendor-bill payments and open balances reconcile.",
        "cash_reconciliation": "Deposits reconcile to included customer payments.",
        "workflow_effects": "Every workflow instance records at least one row effect.",
        "workflow_dates": "Workflow step dates never move backward.",
        "repeated_payments_are_distinct": "Repeated payment steps create distinct payment records.",
    }
    verifier_rows = [
        {
            "Name": item["name"],
            "Description": descriptions.get(item["name"], ""),
            "Verifier type": item.get("tag", ""),
            "Passed": item.get("passed", False),
        }
        for item in verifiers
    ]
    elapsed = timings.get("total_seconds", sum(
        value for value in timings.values() if isinstance(value, (int, float))
    ))
    size = statistics.get("database_size_bytes", required[1].stat().st_size)
    passed = sum(bool(item.get("passed")) for item in verifiers)
    metrics = (
        ("Database size", f"{size / 1000**3:.2f} GB"),
        ("Elapsed", f"{elapsed:.2f} s"),
        ("Tables", f"{len(tables):,}"),
        ("Rows generated", f"{sum(row_counts.values()):,}"),
        ("Generation model", statistics.get("model", "Unknown")),
        ("Consistency verifier results", f"{passed}/{len(verifiers)} passed"),
    )

    page_name = st.session_state.get("bonsai_page", "Home")

    if page_name == "Home":
        st.title("Home")
        for column, (label, value) in zip(st.columns(len(metrics)), metrics):
            column.metric(label, value)
        st.subheader("Generation parameters")
        parameters = statistics.get("parameters", {})
        st.write(parameters.get("world_description", ""))
        st.dataframe(
            [
                {
                    "parameter": key,
                    "value": (
                        json.dumps(value)
                        if isinstance(value, (dict, list))
                        else str(value)
                    ),
                }
                for key, value in parameters.items()
                if key != "world_description"
            ],
            width="stretch",
            hide_index=True,
        )
        st.subheader("Verifier results")
        st.dataframe(verifier_rows, width="stretch", hide_index=True)
        st.subheader("Time breakdown")
        st.dataframe(
            [{"stage": key, "seconds": value} for key, value in timings.items()],
            width="stretch", hide_index=True,
        )
        return

    if page_name == "Workflows":
        st.title("Workflows")
        st.subheader("Filters")
        st.write(
            "Narrow the audit to a domain and workflow family. Filtering to one "
            "workflow makes its ordered steps and variant distribution meaningful."
        )
        with _connect(required[1]) as db:
            domains = [row[0] for row in db.execute(
                "SELECT DISTINCT domain FROM workflow_instances ORDER BY domain"
            )]
        domain = st.selectbox("Domain", ["All", *domains])
        condition, params = ("", []) if domain == "All" else (" WHERE domain=?", [domain])
        with _connect(required[1]) as db:
            workflows = [row[0] for row in db.execute(
                f"SELECT DISTINCT workflow FROM workflow_instances{condition} ORDER BY workflow",
                params,
            )]
        workflow = st.selectbox("Workflow family", ["All", *workflows])
        clauses, params = [], []
        if domain != "All":
            clauses.append("domain=?")
            params.append(domain)
        if workflow != "All":
            clauses.append("workflow=?")
            params.append(workflow)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with _connect(required[1]) as db:
            summary = db.execute(
                f"""SELECT COUNT(*), SUM(status='completed'),
                           SUM(exception IS NOT NULL), COALESCE(SUM(amount_cents),0)
                    FROM workflow_instances{where}""",
                params,
            ).fetchone()
            mix = db.execute(
                f"""SELECT domain, workflow, variant, description, COUNT(*) instances,
                           ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER
                             (PARTITION BY workflow),2) observed_percent
                    FROM workflow_instances{where}
                    GROUP BY domain, workflow, variant, description
                    ORDER BY domain, workflow, instances DESC""",
                params,
            ).fetchall()
        for column, (label, value) in zip(st.columns(3), (
            ("Number of workflows", f"{summary[0]:,}"),
            ("Exceptions", f"{summary[2] or 0:,}"),
            ("Amount", f"${summary[3] / 100:,.2f}"),
        )):
            column.metric(label, value)
        st.caption(
            "Exceptions are workflows that followed a nonstandard path, such as a "
            "payment failure, delayed fulfillment, reconciliation difference, missing "
            "receipt, or forecast revision. Amount is represented transaction volume."
        )
        st.subheader("Workflow distribution")
        st.write(
            "See each generated workflow variant (generated by prompting the model) "
            "and how many were generated."
        )
        st.dataframe([
            dict(zip(("domain", "workflow", "variant", "description", "instances", "observed_percent"), row))
            for row in mix
        ], width="stretch", hide_index=True)
        with _connect(required[1]) as db:
            funnel = db.execute(
                f"""SELECT ws.step, ws.sequence, COUNT(*) steps_completed
                    FROM workflow_step_executions ws
                    JOIN workflow_instances wi ON wi.id=ws.workflow_instance_id
                    {where}
                    GROUP BY ws.step, ws.sequence ORDER BY ws.sequence, ws.step""",
                params,
            ).fetchall()
            impact = db.execute(
                f"""SELECT wi.workflow, ws.affected_table, ws.operation,
                           COUNT(*) effects
                    FROM workflow_step_executions ws
                    JOIN workflow_instances wi ON wi.id=ws.workflow_instance_id
                    {where}
                    GROUP BY wi.workflow, ws.affected_table, ws.operation
                    ORDER BY wi.workflow, effects DESC""",
                params,
            ).fetchall()
        left, right = st.columns(2)
        left.subheader("Workflow steps")
        left.write(
            "Each workflow variant above is an ordered series of steps. This table "
            "combines all selected workflows and shows each step's position, how many "
            "workflows included it, and how many did not complete it. Filter to one "
            "workflow family to see its sequence clearly."
        )
        left.dataframe([
            dict(zip(("step", "sequence", "steps completed"), row))
            for row in funnel
        ], width="stretch", hide_index=True)
        right.subheader("Database records created or changed")
        right.write(
            "Shows where the selected workflows stored their results in the database. "
            "Table is the kind of record, such as invoices or payments. Operation says "
            "whether records were inserted, updated, or only observed. Effects is the "
            "number of recorded step results. A blank table means that step has no "
            "specific database record attached."
        )
        right.dataframe([
            dict(zip(("workflow", "table", "operation", "effects"), row))
            for row in impact
        ], width="stretch", hide_index=True)
        with _connect(required[1]) as db:
            instances = db.execute(
                f"""SELECT id, instance_key, workflow, variant, date, exception, status
                    FROM workflow_instances{where} ORDER BY date DESC, id DESC LIMIT 500""",
                params,
            ).fetchall()
        if instances:
            st.subheader("Workflow trace")
            st.write(
                "Choose a generated workflow to follow its steps and resulting "
                "database records."
            )
            labels = {
                row[0]: f"{row[3]} · {row[1]} · {row[2]} · {row[4]}"
                for row in instances
            }
            selected_instance = st.selectbox(
                "Workflow instance", list(labels), format_func=labels.get
            )
            with _connect(required[1]) as db:
                steps = db.execute(
                    """SELECT sequence, step, status, date, affected_table, record_id,
                              operation, before_summary, after_summary
                       FROM workflow_step_executions
                       WHERE workflow_instance_id=? ORDER BY sequence""",
                    (selected_instance,),
                ).fetchall()
            trace_rows = [
                dict(zip(("sequence", "step", "status", "date", "affected_table",
                          "record_id", "operation", "before", "after"), row))
                for row in steps
            ]
            st.table(trace_rows)

    if page_name != "Tables":
        return

    st.title("Tables")
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
                     width="stretch", hide_index=True)

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
                     width="stretch", hide_index=True)
    else:
        st.info("Select at least one column.")


def _run_page(name):
    import streamlit as st

    st.session_state.bonsai_page = name
    _app()


def _home_page():
    _run_page("Home")


def _workflows_page():
    _run_page("Workflows")


def _tables_page():
    _run_page("Tables")


def _navigation():
    import streamlit as st

    navigation = st.navigation([
        st.Page(_home_page, title="Home", icon=":material/home:", default=True),
        st.Page(_workflows_page, title="Workflows", icon=":material/account_tree:"),
        st.Page(_tables_page, title="Tables", icon=":material/table:"),
    ])
    navigation.run()


if __name__ == "__main__":
    if any(name.startswith("streamlit") for name in sys.modules):
        _navigation()
    else:
        _launch()
