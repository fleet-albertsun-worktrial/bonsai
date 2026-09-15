# Bonsai pipeline implementation

## 1. Make the skeleton runnable without changing its contract
- Complete [`main.py`](/Users/fleettrial_candidate/worktrial/bonsai/main.py), retaining every existing docstring/comment and all existing function names; repair the missing colons, invalid smart quotes/defaults, incomplete calls, and add the already-invoked `_run_verifiers` helper.
- Keep `generate()` backward-compatible while accepting an optional schema path defaulting to [`theseus-stratum-deployed-bundle/schema.sql`](/Users/fleettrial_candidate/worktrial/bonsai/theseus-stratum-deployed-bundle/schema.sql), satisfying the schema-as-input requirement.
- Validate dates/counts/rates up front; treat `start_date`/`end_date` as authoritative and require `scenario_duration_days` to match. Add only essential reproducibility/scale settings: `model` (default GPT-5.6 Sol / `gpt-5.6-sol`), a fixed seed, and target database size (default 1 GB, capped at 2 GB).
- Expose every `WorldParameters` field as an underscore-style CLI flag by iterating the dataclass, plus `--output_path` and `--schema_path`. Dates use `YYYY-MM-DD`. Examples:
  - `uv run main.py`
  - `uv run main.py --model gpt-5.6-sol --end_date 2025-12-31`
  - Flags: `--world_description`, `--start_date`, `--end_date`, `--num_departments`, `--num_employees`, `--num_customers`, `--num_subscriptions`, `--num_sales_orders`, `--scenario_duration_days`, `--cross_domain_link_rate`, `--exception_rate`, `--model`, `--random_seed`, `--target_size_gb`
- Add a minimal [`pyproject.toml`](/Users/fleettrial_candidate/worktrial/bonsai/pyproject.toml) for `faker`, `jinja2`, `openai`, and `python-dotenv`, so the documented entry point is exactly `uv run main.py`.

## 2. Build deterministic knowledge and domain plans
- `_extract_knowledge_base()` will load `.env` and make call 1 with [`prompt_templates/knowledge_base.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/knowledge_base.jinja2). It returns a compact world contract covering the company, organization, customer/vendor segments, offerings, currencies, accounting policies, seasonality, and risk profile. Every planning request uses `world_parameters.model`, which defaults to GPT-5.6 Sol and can be overridden through `--model`.
- Python and Faker then create the complete authoritative `entity_registry.json` with stable IDs. Calls 2–9 receive the same immutable knowledge base and registry and run concurrently:
  1. [`order_to_cash.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/order_to_cash.jinja2): estimates, sales orders, fulfillment, returns, credits, invoicing, collection, and deposits.
  2. [`procure_to_pay.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/procure_to_pay.jinja2): requisitions, RFQs, contracts, purchase orders, receipts, bills, payments, variances, and vendor returns.
  3. [`subscription_lifecycle.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/subscription_lifecycle.jinja2): plans, subscriptions, amendments, renewals, usage, rating, prepaid balances, and charges.
  4. [`revenue_and_journal.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/revenue_and_journal.jinja2): ASC 606 arrangements/plans, recognition, postings, reversals, and accruals.
  5. [`expenses_and_assets.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/expenses_and_assets.jinja2): employee expenses, reimbursements, fixed assets, depreciation, and disposal.
  6. [`treasury_and_reconciliation.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/treasury_and_reconciliation.jinja2): bank activity, transfers, settlements, exchange rates, and reconciliation.
  7. [`planning_reporting_close.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/planning_reporting_close.jinja2): budgets, forecasts, scenarios, reports, reconciliations, and month-end close.
  8. [`consolidation_governance.jinja2`](/Users/fleettrial_candidate/worktrial/bonsai/prompt_templates/consolidation_governance.jinja2): subsidiaries, intercompany activity, eliminations, approvals, controls, compliance, and audit history.
- Each prompt receives its relevant schema slice and envspec workflows, and writes only a compact recipe to `intermediate_states/domain_plans/<domain>.json`; it never writes final database rows. This covers the 13 supplied workflow families without one overloaded prompt. A fresh run makes nine logical calls; the eight domain calls are concurrent, and an unchanged rerun makes zero.
- Faker and compact data tables generate names, addresses, dates, and high-volume records. Financial calculations use integer cents until SQLite serialization, and shared JSON, ID, date, and money helpers keep LOC low.

## 3. Merge the plans into one realistic event ledger
- Parallel planning is safe because calls 2–9 only read immutable inputs and write disjoint files. They interact semantically after a merge barrier; they never mutate shared entities, balances, or transaction rows.
- Enforce single-writer ownership: the registry alone creates master entities; operational domains describe source events; Python alone creates concrete cross-domain transactions; the journal projection alone creates postings; treasury derives bank activity; reporting derives statements; governance overlays approvals and audit entries.
- `_generate_records()` validates every domain recipe’s schema, registry hash, references, statuses, currencies, dates, and weights, then merges recipes in fixed order. It generates each business fact once in `event_ledger.jsonl` and derives dependency-ordered per-table JSONL files for the seven groups already listed in the function comments.
- Preserve realistic causal chains across domains: order → fulfillment → invoice → payment → bank settlement → journal → report; purchase order → receipt → bill → payment → journal; and subscription → usage → charge → invoice → revenue plan → recognition journal.
- Shared segments, products, prices, policies, seasonality, and account roles make independently planned workflows describe one company. `cross_domain_link_rate` controls optional links, while `exception_rate` creates coherent exceptions such as partial payment, delayed fulfillment, bill variance, revenue hold, or failed control—not broken arithmetic or orphaned rows.
- Populate reference/configuration tables through schema-aware defaults; leave unsupported specialist transactions empty rather than invent inconsistent activity. Reach the requested size with plausible usage history, audit changes, transaction lines, and document/receipt content—not zero blobs or arbitrary padding.

## 4. Keep the pipeline idempotent with direct conditions
- Compute one `run_hash` from the schema, canonical parameters, seed, model, templates, and source version. If it differs from `intermediate_states/run_hash.txt`, clear only generated checkpoints/database artifacts, recreate the state directory, and save the new hash; retain the append-only log.
- Keep orchestration explicit in `generate()` rather than adding a stage wrapper:
  ```python
  if not knowledge_base.exists() or not entity_registry.exists():
      _extract_knowledge_base(...)
  if not records_complete.exists():
      _generate_records(...)
  if not output_database.exists():
      _compile(...)
  if not output_verifiers.exists():
      _create_verifiers(...)
  _run_verifiers(...)
  ```
- Inside `_generate_records()`, collect missing domain-plan files and call only those concurrently; then create the merged ledger and table JSONL files only when their completion markers are absent.
- Write each JSON/JSONL artifact to a temporary path, validate it, and atomically rename it. Create `records.complete` only after every plan, ledger, and table file succeeds. Build and verify `output.tmp.sqlite` before replacing `output.sqlite`.
- Derive local Faker/random seeds, IDs, timestamps, and merge/insertion order from `run_hash`, so concurrency completion order cannot alter the data. Statistics are overwritten and timings may differ; `pipeline.log` intentionally remains append-only.

## 5. Compile efficiently while preserving the supplied schema
- `_compile()` will create a temporary database, execute the supplied table DDL while skipping the invalid dump-only `sqlite_stat1` declaration, introspect foreign keys, and stream JSONL inserts in topological order using batched `executemany` calls.
- Build the dump’s indexes after bulk loading, then enable foreign keys, run `PRAGMA foreign_key_check` and `quick_check`, and atomically replace `results/output.sqlite` only on success.
- Keep soft links such as source transaction and journal IDs consistent from the event ledger even where the schema has no FK constraint.

## 6. Create and run readable verifiers
- Add [`verifiers.py`](/Users/fleettrial_candidate/worktrial/bonsai/verifiers.py) with a small registry/decorator that tags every check as `knowledge_base` or `rules`. Keep this file at the repo root; do not copy it into `results/`.
- Implement the documented checks: KB entities/relationships exist; every journal balances; line/header totals reconcile; applications do not exceed payments or balances; dates are causal and fall in posting periods; subsidiary/currency/period agree across chains; ordered/fulfilled/received/billed quantities reconcile; revenue-plan lines sum to plan totals.
- `_run_verifiers()` will execute all checks, record individual outcomes, append failures to the log, and fail the process rather than silently produce an invalid database.

## 7. Produce artifacts and verify the full run
- Record append-mode logs and timings directly around the calls. Write `results/output.sqlite`, `statistics.json` (parameters, model, timings, row counts, verifier results, final bytes), `pipeline.log`, and `intermediate_states/`; never log secrets.
- Run the implementation using exactly `uv run main.py`.
- Confirm exit status zero, 1–2 GB output at the configured target, all declared tables/indexes preserved, empty `foreign_key_check`/successful `quick_check`, all verifiers passing, and a second run reusing all valid checkpoints with zero OpenAI calls and no duplicate rows.
