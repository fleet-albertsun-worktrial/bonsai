import argparse
import base64
import html
import json
import sqlite3
import sys
from pathlib import Path

EMAIL_INBOX = None


def _email_inbox_component():
    global EMAIL_INBOX
    if EMAIL_INBOX is None:
        import streamlit.components.v1 as components

        EMAIL_INBOX = components.declare_component(
            "email_inbox",
            path=str(Path(__file__).parent / "components" / "email_inbox"),
        )
    return EMAIL_INBOX


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


def _load_emails(path):
    email_path = path / "email_gen" / "emails.jsonl"
    if not email_path.is_file():
        return []
    return [
        json.loads(line)
        for line in email_path.read_text().splitlines()
        if line.strip()
    ]


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

    if page_name == "Emails":
        st.markdown(
            """<style>
            .email-shell [data-testid="stVerticalBlock"] { gap: .55rem; }
            .email-title {font-size:1.35rem;font-weight:650;margin:0 0 .15rem}
            .email-subtle {color:#667085;font-size:.78rem}
            .message-card {border-bottom:1px solid #e4e7ec;padding:16px 4px;
                margin:0;background:#fff}
            .message-subject {font-size:1rem;font-weight:500;margin:0 0 12px}
            .message-head {display:flex;justify-content:space-between;gap:12px;
                margin-bottom:1px;font-size:.86rem;align-items:center}
            .sender-wrap {display:flex;align-items:center;gap:10px}
            .sender-avatar {width:32px;height:32px;border-radius:50%;
                display:inline-flex;align-items:center;justify-content:center;
                color:#fff;background:#5f6368;font-size:.78rem;font-weight:600}
            .message-date {color:#5f6368;font-size:.74rem;white-space:nowrap}
            .message-address {color:#5f6368;font-size:.72rem;margin:0 0 14px 42px}
            .message-body {margin-left:42px;color:#202124;line-height:1.55}
            .message-logo {display:block;max-width:180px;max-height:100px;
                object-fit:contain;margin:14px 0 0 42px}
            .property-grid {border:1px solid #e4e7ec;border-radius:8px;
                overflow:hidden;margin-bottom:12px}
            .property-row {display:grid;grid-template-columns:38% 62%;
                gap:8px;padding:7px 10px;border-bottom:1px solid #f0f1f3;
                font-size:.76rem}
            .property-row:last-child {border-bottom:0}
            .property-label {color:#667085}
            .property-value {color:#101828;overflow-wrap:anywhere}
            </style><div class="email-shell"></div>""",
            unsafe_allow_html=True,
        )
        st.markdown('<div class="email-title">Email archive</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<div class="email-subtle">Generated workflow communications</div>',
            unsafe_allow_html=True,
        )
        try:
            emails = _load_emails(path)
        except (OSError, json.JSONDecodeError) as error:
            st.error(f"Could not load generated emails: {error}")
            return
        if not emails:
            st.info(
                "No generated emails found. Run "
                f"`uv run generate_emails.py {path} --thread-limit 100`."
            )
            return
        search, domain_filter, workflow_filter = st.columns([2.2, 1, 1.2])
        query = search.text_input(
            "Search", placeholder="Search subject, sender, or content",
            label_visibility="collapsed",
        ).strip().lower()
        domains = sorted({email.get("domain", "") for email in emails})
        domain = domain_filter.selectbox(
            "Domain", ["All domains", *domains], label_visibility="collapsed"
        )
        workflows = sorted({email.get("workflow", "") for email in emails})
        workflow = workflow_filter.selectbox(
            "Workflow", ["All workflows", *workflows],
            label_visibility="collapsed",
        )
        filtered = [
            email for email in emails
            if (domain == "All domains" or email.get("domain") == domain)
            and (workflow == "All workflows" or email.get("workflow") == workflow)
            and (not query or query in " ".join((
                    email.get("subject", ""), email.get("sender", ""),
                    email.get("sender_name", ""), email.get("workflow", ""),
                    email.get("body", ""),
                )).lower())
        ]
        if not filtered:
            st.warning("No emails match that search.")
            return
        left, middle, right = st.columns([0.9, 1.8, 1.15], gap="large")
        threads = {}
        for email in filtered:
            threads.setdefault(email["thread_id"], []).append(email)
        for messages in threads.values():
            messages.sort(key=lambda item: item.get("sequence", 0))
        if st.session_state.get("selected_email_thread") not in threads:
            st.session_state.selected_email_thread = next(iter(threads))
        selected_id = st.session_state.selected_email_thread
        with left:
            st.markdown(
                f'<div class="email-title">Inbox '
                f'<span class="email-subtle">{len(threads)}</span></div>',
                unsafe_allow_html=True,
            )
            component_threads = []
            for thread_id, thread_messages in threads.items():
                first, last = thread_messages[0], thread_messages[-1]
                component_threads.append({
                    "id": thread_id,
                    "sender": first.get("sender_name") or first.get("sender", ""),
                    "date": str(last.get("date", "")),
                    "subject": first.get("subject", "(no subject)"),
                    "preview": " ".join(first.get("text", "").split())[:90],
                    "count": len(thread_messages),
                })
            clicked_id = _email_inbox_component()(
                threads=component_threads,
                selected=selected_id,
                default=selected_id,
                key="email-inbox-component",
            )
            if clicked_id in threads and clicked_id != selected_id:
                st.session_state.selected_email_thread = clicked_id
                selected_id = clicked_id
                st.rerun()
        messages = threads[selected_id]
        selected = messages[-1]
        with middle:
            subject = html.escape(messages[0].get("subject", "(no subject)"))
            participants = sorted({
                message.get("sender_name") or message.get("sender", "")
                for message in messages
            })
            st.markdown(
                f'<div class="email-title">{subject}</div>'
                f'<div class="email-subtle">{len(messages)} messages · '
                f'{html.escape(", ".join(participants))}</div>',
                unsafe_allow_html=True,
            )
            for index, message in enumerate(messages):
                body = message.get("body") or message.get("text", "")
                message_subject = html.escape(
                    message.get("subject", "(no subject)")
                )
                sender_name = html.escape(message.get("sender_name", ""))
                sender_email = html.escape(message.get("sender", ""))
                sent_date = html.escape(str(message.get("date", "")))
                recipients = html.escape(", ".join(message.get("to", [])))
                initials = "".join(
                    part[:1] for part in message.get("sender_name", "").split()[:2]
                ).upper() or "?"
                logo_html = ""
                logo_path = message.get("logo_path")
                if logo_path:
                    logo = path / "email_gen" / logo_path
                    if logo.is_file():
                        encoded_logo = base64.b64encode(logo.read_bytes()).decode()
                        logo_html = (
                            f'<img class="message-logo" '
                            f'src="data:image/png;base64,{encoded_logo}" '
                            f'alt="{html.escape(message.get("sender_name", ""))}">'
                        )
                if message.get("font_family") and message.get("font_size_px"):
                    font_family = html.escape(
                        str(message["font_family"]), quote=True
                    )
                    font_size = int(message["font_size_px"])
                    st.markdown(
                        '<div class="message-card">'
                        f'<div class="message-subject">{message_subject}</div>'
                        '<div class="message-head">'
                        '<div class="sender-wrap">'
                        f'<span class="sender-avatar">{initials}</span>'
                        f'<span><strong>{sender_name}</strong> &lt;{sender_email}&gt;'
                        '</span></div>'
                        f'<span class="message-date">{sent_date}</span>'
                        '</div>'
                        f'<div class="message-address">to {recipients}</div>'
                        f'<div class="message-body" style="font-family:{font_family};'
                        f'font-size:{font_size}px;white-space:pre-wrap">'
                        f"{html.escape(body)}</div>{logo_html}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="message-card">'
                        f'<div class="message-subject">{message_subject}</div>'
                        '<div class="message-head"><div class="sender-wrap">'
                        f'<span class="sender-avatar">{initials}</span>'
                        f'<span><strong>{sender_name}</strong> &lt;{sender_email}&gt;'
                        '</span></div>'
                        f'<span class="message-date">{sent_date}</span></div>'
                        f'<div class="message-address">to {recipients}</div>'
                        f'<div class="message-body" style="white-space:pre-wrap">'
                        f'{html.escape(body)}</div>{logo_html}</div>',
                        unsafe_allow_html=True,
                    )
        with right:
            st.markdown('<div class="email-title">Details</div>',
                        unsafe_allow_html=True)
            sections = {
                "Thread": {
                    "ID": selected.get("thread_id"),
                    "Messages": len(messages),
                    "Participants": ", ".join(participants),
                },
                "Workflow": {
                    "Domain": selected.get("domain"),
                    "Workflow": selected.get("workflow"),
                    "Variant": selected.get("variant"),
                    "Exception": selected.get("exception") or "None",
                    "Instance": selected.get("workflow_instance_id"),
                    "Source": (
                        f"{selected.get('source_table')} "
                        f"#{selected.get('source_id')}"
                    ),
                },
                "Generation": {
                    "Type": selected.get("generation_type"),
                    "Topic": selected.get("topic") or "Direct",
                    "Tone": selected.get("tone"),
                    "Personality": selected.get("mbti"),
                    "Font": selected.get("font_variant"),
                    "Size": (
                        f"{selected.get('font_size_px')}px"
                        if selected.get("font_size_px") else None
                    ),
                },
            }
            for heading, properties in sections.items():
                st.markdown(f"**{heading}**")
                rows = "".join(
                    '<div class="property-row">'
                    f'<div class="property-label">{html.escape(str(label))}</div>'
                    f'<div class="property-value">{html.escape(str(value or "—"))}</div>'
                    '</div>'
                    for label, value in properties.items()
                )
                st.markdown(
                    f'<div class="property-grid">{rows}</div>',
                    unsafe_allow_html=True,
                )
            with st.expander("Workflow evidence"):
                st.json(selected.get("steps", []))
            mbox_path = selected.get("mbox_path")
            if mbox_path:
                mbox = path / "email_gen" / mbox_path
                if mbox.is_file():
                    st.download_button(
                        "Download thread (.mbox)", mbox.read_bytes(),
                        file_name=mbox.name, mime="application/mbox",
                        key=f"download-mbox-{selected['thread_id']}",
                    )
            for message in messages:
                eml_path = path / "email_gen" / message["eml_path"]
                if eml_path.is_file():
                    st.download_button(
                        f"Download message {message.get('sequence', '')}",
                        eml_path.read_bytes(), file_name=eml_path.name,
                        mime="message/rfc822",
                        key=f"download-{message['message_id']}",
                    )
            archive = path / "email_gen" / "emails.zip"
            if archive.is_file():
                st.download_button(
                    "Download all emails", archive.read_bytes(),
                    file_name="emails.zip", mime="application/zip",
                )
        return

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


def _emails_page():
    _run_page("Emails")


def _navigation():
    import streamlit as st

    navigation = st.navigation([
        st.Page(_home_page, title="Home", icon=":material/home:", default=True),
        st.Page(_workflows_page, title="Workflows", icon=":material/account_tree:"),
        st.Page(_tables_page, title="Tables", icon=":material/table:"),
        st.Page(_emails_page, title="Emails", icon=":material/mail:"),
    ])
    navigation.run()


if __name__ == "__main__":
    if any(name.startswith("streamlit") for name in sys.modules):
        _navigation()
    else:
        _launch()
