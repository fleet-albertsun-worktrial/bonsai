---
name: Executable Workflow Audit
overview: Organize the simulation into Oracle NetSuite’s Finance, Operations, and Customers domains; execute six constrained end-to-end workflow families; persist provenance for every instance and step; and audit the resulting records and state changes.
todos:
  - id: workflow-contract
    content: Define constrained operation and exception contracts for six workflow families with plan validation
    status: pending
  - id: execute-domains
    content: Implement operation handlers for all six workflows and shared execution context
    status: pending
  - id: persist-provenance
    content: Persist workflow instances, step executions, and row-level effects
    status: pending
  - id: workflow-dashboard
    content: Build Streamlit workflow coverage, funnel, trace, and table-impact audit views
    status: pending
  - id: workflow-tests
    content: Add deterministic workflow verifiers and run complete end-to-end tests
    status: pending
isProject: false
---

# Executable Workflow Audit

## Scope and taxonomy
- Use Oracle NetSuite’s product overview as the top-level scope authority: **Finance**, **Operations**, and **Customers** are product domains, not workflows.
- Generate six end-to-end workflow families beneath those domains:
  - **Customers — `lead_to_cash`**: estimate/quote, approval, sales order, fulfillment, invoice, customer payment, deposit, return authorization, credit memo, and refund.
  - **Operations — `procure_to_pay`**: requisition, RFQ/vendor quote, contract, purchase order, receipt, three-way match, vendor bill, payment, prepayment, return, and vendor credit.
  - **Finance — `subscription_to_revenue`**: subscription activation and changes, usage, rating, prepaid drawdown, charges, invoicing, revenue allocation, deferred revenue, recognition, and reclassification.
  - **Finance — `expense_to_asset`**: employee expense submission/approval/reimbursement and fixed-asset capitalization, depreciation, and disposal.
  - **Finance — `treasury_to_close`**: cash activity, bank matching/reconciliation, transaction FX and revaluation, journals, account review, adjustments, period locks, and legal-entity reporting.
  - **Finance — `plan_to_consolidate`**: budgets, forecasts, scenarios, subsidiaries, intercompany activity, consolidated exchange rates, translation, elimination, consolidation, and consolidated reporting/control evidence.
- Treat users, roles, currencies, tax codes, dimensions, preferences, dashboards, searches, and other reference/configuration tables as shared master data rather than forcing them into a transaction workflow.
- Use Oracle’s detailed NetSuite documentation as the behavioral authority and the repository’s `envspec/workflows` files as executable specifications.

## 1. Define a constrained workflow contract
- In [main.py](/Users/fleettrial_candidate/worktrial/bonsai/main.py), replace `DOMAINS` and `WORKFLOW_PLAN_PROMPTS` with a six-entry `WORKFLOWS` registry. Each entry records its parent domain, source `envspec` specifications, planning prompt, supported operation IDs, allowed exceptions, prerequisites, ownership boundaries, and handlers.
- Rename the model output collection from `workflow_mix` to `variants`. Each plan describes 1–5 mutually exclusive paths through one workflow family, not multiple business domains.
- Replace free-form `steps` and substring-based `matches()` with exact operation and exception IDs. Validate model output, causal ordering, cadence, and normalized weights before persistence; fail early on unsupported plans.
- Update [workflow_plan.jinja2](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/workflow_plan.jinja2) to instruct:

  ```text
  Design 1–5 mutually exclusive variants of the specified end-to-end workflow.
  Each variant must describe one plausible path from its trigger to a terminal
  outcome. Use only the supported operations, exceptions, and prerequisites
  supplied below. Weights must total approximately 1. Cadence must be daily,
  weekly, monthly, quarterly, or annual. Use concise snake_case IDs and set
  exception to null for a standard path. Do not create entities, records, IDs,
  amounts, balances, totals, or journal entries.
  ```
- Supply focused prompts for `lead_to_cash`, `procure_to_pay`, `subscription_to_revenue`, `expense_to_asset`, `treasury_to_close`, and `plan_to_consolidate`, covering the steps listed in “Scope and taxonomy.”
- Keep explicit world counts authoritative; cadence distributes dates, while weight controls workflow selection.

## 2. Execute every workflow plan
- Introduce a small execution context in [main.py](/Users/fleettrial_candidate/worktrial/bonsai/main.py) for shared entities, created records, amounts, dates, and cross-step references. Each operation handler creates or updates concrete schema rows and returns its table/record effects.
- Implement handlers for all six workflow families, covering every supported standard and exceptional path in their source specifications.
- Extract shared primitives such as `request_approval()`, `post_journal()`, `issue_invoice()`, `receive_customer_payment()`, `pay_vendor_bill()`, and `record_audit_effect()` so handoffs reuse records rather than recreate them.
- Enforce ownership boundaries:
  - `lead_to_cash` owns product/service invoices and collections. `subscription_to_revenue` owns subscription charges and revenue schedules but calls shared invoice/collection primitives with `invoice_origin='subscription'`.
  - `procure_to_pay` owns asset purchase POs, receipts, bills, and payments. `expense_to_asset` begins capitalization from an eligible received purchase; employee reimbursement remains within `expense_to_asset`.
  - `treasury_to_close` owns transaction FX, bank reconciliation, entity close, and base statements. `plan_to_consolidate` owns consolidated rates, intercompany eliminations, and consolidated statements.
  - Every transaction has exactly one owning workflow and may store upstream/downstream workflow references.
- Execute in dependency order: master data → `lead_to_cash` and `procure_to_pay` → `subscription_to_revenue` → `expense_to_asset` → `treasury_to_close` → `plan_to_consolidate`.
- Make later workflows consume references produced by earlier workflows rather than merely attaching variant names.

## 3. Persist workflow provenance
- Add generated provenance tables to the SQLite schema (or equivalent schema-supported records): `workflow_instances` and `workflow_step_executions`, recording parent domain, workflow family, variant, cadence, exception, status, sequence, timestamps, source/target table, record ID, ownership/handoff references, and before/after summaries.
- Derive the event ledger and audit trail from these executions so every claimed step is traceable to a concrete row mutation; record skipped/failed steps explicitly rather than silently ignoring them.

## 4. Add the Streamlit workflow audit
- Extend [app.py](/Users/fleettrial_candidate/worktrial/bonsai/app.py) with a “Workflow audit” section containing:
  - Domain/workflow-family/variant/date/exception/status filters and coverage metrics: instances, completion rate, executed-step rate, unsupported/skipped count, exception rate, and affected rows.
  - A workflow mix comparison of planned weights versus observed execution counts.
  - A step funnel showing how many instances reached each operation and where they stopped or branched.
  - A selected-instance trace: ordered steps with status, date, affected table, record ID, amount, and before/after values, with links/controls to inspect the underlying rows.
  - A table-impact view showing which tables each workflow actually inserted or updated, plus zero-impact planned steps as audit failures.
- Use Streamlit-native charts/dataframes to avoid adding a visualization dependency.

## 5. Verify behavior and regressions
- Keep the existing verifiers and add four financial-consistency verifiers:
  - Receivables reconciliation: invoice total equals payments, credits, and amount due; returns/refunds cannot exceed the original sale.
  - Payables reconciliation: bill total equals payments, vendor credits, and amount due; bill variance equals actual minus expected PO amount.
  - Subscription and revenue reconciliation: invoices equal included charges; recognized plus deferred revenue equals allocated arrangement value; activity remains within subscription dates.
  - Cash reconciliation: deposits equal included customer payments; bank balance equals opening cash plus receipts minus vendor payments and expenses.
- Add collision/ownership verifiers: every transactional row has one owning workflow; shared primitives do not duplicate invoices, cash movements, or journals; asset capitalization references procurement when applicable; consolidated statements do not overwrite entity statements.
- Treat workflow provenance, step coverage, and plan-vs-observed differences as diagnostic audit information rather than additional hard financial verifiers.
- Add small deterministic fixtures covering every operation/exception and run an end-to-end generated world; confirm all handlers produce nonzero expected table effects and all accounting/foreign-key verifiers pass.
- Validate the Streamlit queries against the generated database and retain the table browser as a drill-down tool.
- Run the final large world:

  ```shell
  uv run main.py \
    --output_path results/large-v4 \
    --start_date 2021-01-01 \
    --end_date 2025-12-31 \
    --scenario_duration_days 1826 \
    --num_departments 8 \
    --num_employees 253 \
    --num_customers 10463 \
    --num_subscriptions 12042 \
    --num_sales_orders 110532 \
    --target_size_gb 0.01
  ```

- Launch `uv run app.py --output_filepath results/large-v4` and validate the workflow mix, step funnel, instance trace, table-impact view, and zero-impact/failed-step indicators against the generated `large-v4` data.
