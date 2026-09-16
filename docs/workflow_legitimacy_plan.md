# Workflow Legitimacy Plan

## Goal

Make every model-generated workflow variant an executable, stateful process whose
steps correspond to real events, dates, and database changes.

The model will choose plausible business paths from constrained operations. The
pipeline will remain responsible for deterministic values, dates, records,
accounting behavior, and validation.

## 1. Define operation contracts

Create an operation registry in `main.py`. Each supported operation declares:

- Required workflow state and records.
- State and records it creates or updates.
- Whether it may repeat.
- A bounded timing range relative to the prior step.
- Valid parameters the model may select.
- Valid preceding and following operations.
- Its deterministic execution handler.

Example:

```python
OPERATIONS = {
    "receive_payment": {
        "requires": ("invoice",),
        "creates": ("payment",),
        "updates": ("invoice_balance",),
        "repeatable": True,
        "delay_days": (1, 30),
        "parameters": {"portion": ("fraction", "remaining")},
        "handler": receive_payment,
    },
}
```

## 2. Strengthen the model contract

Change `workflow_plan.jinja2` so each step is an object containing an operation
and optional constrained parameters:

```json
{
  "name": "invoice_paid_in_installments",
  "description": "The customer pays an invoice in two installments.",
  "weight": 0.09,
  "cadence": "weekly",
  "exception": "partial_payment",
  "steps": [
    {"operation": "issue_invoice"},
    {"operation": "receive_payment", "portion": 0.5},
    {"operation": "make_deposit"},
    {"operation": "receive_payment", "portion": "remaining"},
    {"operation": "make_deposit"}
  ]
}
```

The model may choose only bounded declarative parameters such as:

- Payment or return portion.
- Delay within an allowed range.
- Resolution type.
- Termination or return reason.
- Hold or rejection outcome.

It must not generate record IDs, absolute dates, monetary values, SQL, journal
lines, or arbitrary operation names.

## 3. Validate plans before execution

Add strict validation and normalization that:

- Rejects unsupported operations, exceptions, and parameters.
- Verifies every prerequisite is established by an earlier step.
- Rejects repeated operations unless their contract permits repetition.
- Ensures step order is causally valid.
- Ensures parameters fall within declared bounds.
- Ensures descriptions and exceptions agree with the path.
- Ensures rejected, held, and failed paths omit prohibited downstream actions.
- Ensures each path reaches a valid terminal state.
- Normalizes variant weights only after structural validation succeeds.

Invalid model output should fail with a useful workflow, variant, and step error;
it should not silently drop invalid operations.

## 4. Add a stateful workflow executor

Introduce a small per-instance state object:

```python
@dataclass
class WorkflowState:
    instance_id: int
    current_date: date
    records: dict[str, dict]
    balances: dict[str, int]
    values: dict[str, object]
```

Execute every workflow through one generic loop:

1. Read the next operation and parameters.
2. Check its guard and prerequisites.
3. Invoke its deterministic handler.
4. Advance the current date using the handler's actual date.
5. Save created records and state changes.
6. Persist the step result before continuing.

Repeated steps execute repeatedly. They must never be treated as descriptive
labels.

## 5. Standardize step results and effects

Every handler returns:

```python
@dataclass
class StepResult:
    date: date
    effects: list[RecordEffect]

@dataclass
class RecordEffect:
    table: str
    record_id: int
    operation: str
    before: dict | None = None
    after: dict | None = None
```

Persist each step's actual:

- Execution date.
- Completion, rejection, hold, skip, or failure status.
- Inserted, updated, or observed records.
- Before and after summaries.
- Upstream and downstream workflow references.

Derive workflow traces, event-ledger entries, and audit-trail entries from these
results rather than reconstructing them afterward.

## 6. Make exceptions executable policies

Replace exception labels that only annotate records with bounded policies that
change execution:

- `partial_payment`: split the remaining invoice balance across repeated payment
  operations.
- `delayed_fulfillment`: advance fulfillment by the configured delay.
- `payment_failure`: record a failed attempt without reducing the balance.
- `customer_return`: create authorization, credit, and refund records.
- `price_variance`: create and resolve a bill variance.
- `payment_hold`: delay payment until a release operation.
- `missing_receipt`: hold approval until a receipt is attached.
- `recognition_hold`: retain allocated consideration in deferred revenue.
- `reconciliation_difference`: require an adjustment before close.
- `control_exception`: stop consolidated report publication pending remediation.

Remove the independent random exception behavior once all policies are
executable. `exception_rate` should influence variant selection, not introduce
unrecorded exceptional behavior after selection.

## 7. Preserve workflow ownership and handoffs

Keep one owner for every transaction:

- Lead to cash owns product and service invoices and collections.
- Subscription to revenue owns subscription charges and revenue schedules.
- Procure to pay owns purchase orders, receipts, bills, and vendor payments.
- Expense to asset owns employee reimbursements and asset accounting.
- Treasury to close owns cash reconciliation and legal-entity close.
- Plan to consolidate owns intercompany elimination and consolidated reporting.

Shared primitives may create common records, but they must retain the owning
workflow instance and an optional upstream instance reference.

## 8. Improve the Workflows page

Use actual step results to display:

- Workflow distribution with model-generated descriptions.
- Step completion counts based on executed operations.
- Database records created or changed by each operation.
- Workflow traces with real per-step dates.
- Repeated events as separate rows.
- Failed, held, rejected, and skipped steps distinctly.
- Direct links or controls to inspect affected records on the Tables page.

Do not display a step as completed unless its handler ran successfully.

## 9. Add legitimacy verifiers

Add deterministic checks for:

- Dates are nondecreasing within each workflow instance.
- Every completed mutating step has at least one concrete record effect.
- Every effect references an existing table and record.
- Operation prerequisites existed before execution.
- Repeated payment and deposit steps created distinct records.
- Invoice installments sum to the paid amount and preserve amount due.
- Exception variants produce behavior distinct from their standard variant.
- Terminal state agrees with the final operation and exception.
- Every transactional row has exactly one owning workflow.
- Workflow, event-ledger, audit-trail, and accounting records agree.

## 10. Test incrementally

Create one small deterministic fixture per standard and exceptional variant.

For each fixture:

1. Validate the generated plan.
2. Execute one workflow instance.
3. Assert its ordered dates, state transitions, and record effects.
4. Run accounting and foreign-key verifiers.
5. Confirm the Workflows page can query the trace.

Then generate a small complete world covering all operations and exceptions.
Only after it passes should a new large version be generated.

## Completion criteria

The work is complete when:

- Model steps drive execution rather than annotate procedural generation.
- Every displayed step has its actual date, status, and record effects.
- Repeated operations produce repeated real events.
- Every allowed exception has deterministic behavior.
- All workflow, accounting, ownership, and foreign-key verifiers pass.
- A user can follow any workflow trace into the exact underlying records.
