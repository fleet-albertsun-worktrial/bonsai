"""Interpretable, read-only checks for a generated Bonsai database."""

import json
import sqlite3
from pathlib import Path

CHECKS = []


def verifier(tag):
    def register(fn):
        CHECKS.append((tag, fn.__name__, fn))
        return fn
    return register


def violations(db, sql, parameters=()):
    return [row[0] for row in db.execute(sql, parameters).fetchall()]


@verifier("knowledge_base")
def knowledge_base_entities(db, knowledge_base):
    kb = json.loads(Path(knowledge_base).read_text())
    missing = []
    if not db.execute("SELECT 1 FROM subsidiaries WHERE name = ?", (kb["company"]["name"],)).fetchone():
        missing.append(f'company:{kb["company"]["name"]}')
    item_names = {row[0] for row in db.execute("SELECT name FROM items")}
    missing += [f"offering:{x['name']}" for x in kb["offerings"] if x["name"] not in item_names]
    return missing


@verifier("rules")
def foreign_keys(db, _):
    return [f"{table}:{rowid}->{parent}" for table, rowid, parent, _ in db.execute("PRAGMA foreign_key_check")]


@verifier("rules")
def balanced_journals(db, _):
    return violations(db, """
        SELECT je.id FROM journal_entries je JOIN journal_entry_lines jl
          ON jl.journal_entry_id = je.id GROUP BY je.id
        HAVING ABS(SUM(jl.debit) - SUM(jl.credit)) > .005
    """)


@verifier("rules")
def document_totals(db, _):
    bad = violations(db, """
        SELECT i.id FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id
        GROUP BY i.id HAVING ABS(SUM(l.amount)-i.subtotal)>.005
          OR ABS(i.subtotal+i.tax_total+i.shipping_cost-i.total)>.005
    """)
    return [f"invoice:{x}" for x in bad] + [f"bill:{x}" for x in violations(db, """
        SELECT b.id FROM bills b JOIN bill_lines l ON l.bill_id=b.id
        GROUP BY b.id HAVING ABS(SUM(l.amount)-b.subtotal)>.005
          OR ABS(b.subtotal+b.tax_total-b.total)>.005
    """)]


@verifier("rules")
def payment_limits(db, _):
    bad = violations(db, """
        SELECT p.id FROM payments p LEFT JOIN payment_applications a ON a.payment_id=p.id
        GROUP BY p.id HAVING COALESCE(SUM(a.amount),0)-p.amount>.005
    """)
    bad += violations(db, """
        SELECT i.id FROM invoices i
        WHERE i.amount_paid-i.total>.005 OR ABS(i.amount_paid+i.amount_due-i.total)>.005
    """)
    return bad


@verifier("rules")
def causal_dates(db, _):
    return violations(db, """
        SELECT i.id FROM invoices i JOIN sales_orders s ON s.id=i.sales_order_id
        WHERE i.date<s.date
        UNION ALL
        SELECT p.id FROM payments p JOIN payment_applications a ON a.payment_id=p.id
          JOIN invoices i ON i.id=a.invoice_id WHERE p.date<i.date
    """)


@verifier("rules")
def dimensions_and_periods(db, _):
    return violations(db, """
        SELECT i.id FROM invoices i JOIN customers c ON c.id=i.customer_id
        LEFT JOIN accounting_periods ap ON ap.id=i.posting_period_id
        WHERE i.subsidiary_id<>c.subsidiary_id
           OR (ap.id IS NOT NULL AND (i.date<ap.start_date OR i.date>ap.end_date))
        UNION ALL
        SELECT p.id FROM payments p JOIN customers c ON c.id=p.customer_id
        WHERE p.subsidiary_id<>c.subsidiary_id OR p.currency<>c.currency
    """)


@verifier("rules")
def quantity_reconciliation(db, _):
    return [f"sales:{x}" for x in violations(db, """
        SELECT id FROM sales_order_lines
        WHERE quantity_fulfilled-quantity>.0001 OR quantity_billed-quantity_fulfilled>.0001
    """)] + [f"purchase:{x}" for x in violations(db, """
        SELECT id FROM purchase_order_lines
        WHERE quantity_received-quantity>.0001 OR quantity_billed-quantity_received>.0001
    """)]


@verifier("rules")
def revenue_plan_totals(db, _):
    return violations(db, """
        SELECT p.id FROM revenue_plans p JOIN revenue_plan_lines l ON l.plan_id=p.id
        GROUP BY p.id HAVING ABS(SUM(l.amount)-p.total_amount)>.005
    """)


def run_all(database, knowledge_base):
    with sqlite3.connect(database) as db:
        results = []
        for tag, name, check in CHECKS:
            try:
                bad = check(db, knowledge_base)
                results.append({"tag": tag, "name": name, "passed": not bad, "violations": bad[:20]})
            except Exception as error:
                results.append({"tag": tag, "name": name, "passed": False, "violations": [str(error)]})
    return results
