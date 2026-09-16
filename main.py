import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import shutil
import sqlite3
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from faker import Faker
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import AsyncOpenAI

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / 'theseus-stratum-deployed-bundle' / 'schema.sql'
TEMPLATES = ROOT / 'prompt_templates'
JINJA = Environment(
    loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined, autoescape=False
)
PIPELINE_VERSION = '4'
CADENCES = {'daily', 'weekly', 'monthly', 'quarterly', 'annual'}
WORKFLOW_OUTPUT = {'variants': [{'name': '...', 'description': 'One concise sentence explaining this variant.', 'weight': 0.0, 'cadence': 'daily|weekly|monthly|quarterly|annual', 'steps': [{'operation': '...', 'parameters': {}}], 'exception': None}]}
WORKFLOWS = {
    'lead_to_cash': {
        'domain': 'Customers',
        'specs': ('order-to-cash',),
        'prompt': 'Design realistic lead-to-cash variants for product and service sales, from estimate through collection and returns.',
        'steps': ('create_estimate', 'approve_order', 'create_sales_order', 'fulfill_order', 'issue_invoice', 'receive_payment', 'make_deposit', 'authorize_return', 'issue_credit_memo', 'issue_refund'),
        'exceptions': ('delayed_fulfillment', 'partial_payment', 'payment_failure', 'customer_return'),
    },
    'procure_to_pay': {
        'domain': 'Operations',
        'specs': ('procure-to-pay',),
        'prompt': 'Design realistic procure-to-pay variants from sourcing through receipt, matching, settlement, and vendor returns.',
        'steps': ('create_requisition', 'request_quotes', 'select_vendor', 'create_purchase_order', 'receive_items', 'match_vendor_bill', 'approve_bill', 'pay_vendor', 'authorize_vendor_return', 'apply_vendor_credit'),
        'exceptions': ('price_variance', 'quantity_mismatch', 'payment_hold', 'vendor_return'),
    },
    'subscription_to_revenue': {
        'domain': 'Finance',
        'specs': ('subscription-lifecycle', 'revenue-recognition'),
        'prompt': 'Design realistic subscription-to-revenue variants spanning subscription changes, usage billing, and ASC 606 recognition.',
        'steps': ('create_subscription', 'record_change_order', 'record_usage', 'run_rating', 'create_charge', 'issue_invoice', 'create_revenue_arrangement', 'allocate_revenue', 'recognize_revenue', 'reclassify_deferred_revenue'),
        'exceptions': ('autopay_failure', 'usage_overage', 'recognition_hold', 'early_termination'),
    },
    'expense_to_asset': {
        'domain': 'Finance',
        'specs': ('expense-management', 'fixed-asset-lifecycle'),
        'prompt': 'Design realistic expense-to-asset variants for employee reimbursements and fixed-asset capitalization, depreciation, and disposal.',
        'steps': ('submit_expense', 'attach_receipt', 'approve_expense', 'reimburse_employee', 'identify_capital_purchase', 'capitalize_asset', 'place_in_service', 'run_depreciation', 'dispose_asset'),
        'exceptions': ('missing_receipt', 'expense_rejected', 'capitalization_review', 'early_disposal'),
    },
    'treasury_to_close': {
        'domain': 'Finance',
        'specs': ('bank-reconciliation', 'exchange-rate-management', 'journal-entry', 'period-close', 'financial-reporting'),
        'prompt': 'Design realistic treasury-to-close variants spanning cash, reconciliation, FX, journals, close, and legal-entity reporting.',
        'steps': ('import_bank_activity', 'match_bank_transactions', 'reconcile_bank_account', 'maintain_exchange_rates', 'revalue_balances', 'post_adjusting_journal', 'review_accounts', 'lock_subledgers', 'close_period', 'publish_entity_reports'),
        'exceptions': ('unmatched_bank_item', 'reconciliation_difference', 'late_adjustment', 'close_reopened'),
    },
    'plan_to_consolidate': {
        'domain': 'Finance',
        'specs': ('budget-management', 'financial-reporting', 'consolidation'),
        'prompt': 'Design realistic plan-to-consolidate variants spanning budgets, forecasts, scenarios, intercompany activity, translation, elimination, and controls.',
        'steps': ('create_budget', 'create_forecast', 'compare_scenario', 'record_intercompany_activity', 'calculate_consolidated_rates', 'translate_subsidiary_balances', 'eliminate_intercompany', 'run_consolidation', 'publish_consolidated_reports', 'test_controls'),
        'exceptions': ('forecast_revision', 'translation_difference', 'elimination_difference', 'control_exception'),
    },
}

@dataclass
class WorldParameters:
    """Configure the scope and complexity of a synthetic business world.

    Attributes:
        world_description: Narrative describing the business and its financial
            activity.
        start_date: First calendar date included in the simulation.
        end_date: Last calendar date included in the simulation.
        num_departments: Number of organizational departments to generate.
        num_employees: Number of employees distributed across the departments.
        num_customers: Number of customers participating in commercial activity.
        num_subscriptions: Number of recurring subscriptions to generate.
        num_sales_orders: Number of sales orders created during the scenario.
        scenario_duration_days: Number of simulated calendar days.
        cross_domain_link_rate: Fraction of eligible records linked to workflows
            in other domains.
        exception_rate: Fraction of eligible workflows assigned an exceptional
            outcome.
        model: OpenAI model used for planning calls.
        random_seed: Seed used for deterministic procedural generation.
        target_size_gb: Approximate SQLite size, up to 2 GB.
    """
    world_description: str = 'Northstar Office Systems is a 100-person company that sells office equipment and subscription-based maintenance services to 500 customers. Each month, its finance team reconciles sales, subscriptions, vendor bills, employee expenses, payments, and occasional accounting discrepancies.'
    start_date: date = date(2025, 1, 1)
    end_date: date = date(2025, 12, 31)
    num_departments: int = 5
    num_employees: int = 100
    num_customers: int = 500
    num_subscriptions: int = 250
    num_sales_orders: int = 1000
    scenario_duration_days: int = 365
    cross_domain_link_rate: float = 0.6
    exception_rate: float = 0.05
    model: str = 'gpt-5.6-sol'
    random_seed: int = 42
    target_size_gb: float = 1.0

def _json(path, value=None):
    if value is None:
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    temporary.replace(path)

def _json_from_text(text):
    text = text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    return json.loads(text[text.index('{'):text.rindex('}') + 1])

def _schema_columns(schema_path):
    sql = schema_path.read_text()
    return {match.group(1): set(re.findall('^\\s*`([^`]+)`', match.group(2), re.MULTILINE)) for match in re.finditer('CREATE TABLE `(\\w+)` \\((.*?)\\);', sql, re.DOTALL)}

def _schema_context(schema_path):
    sql = schema_path.read_text()
    tables = re.findall('CREATE TABLE `(\\w+)`', sql)
    return f"{len(tables)} tables: {', '.join(tables)}; {sql.count('FOREIGN KEY')} foreign keys"

def _render(template, **context):
    return JINJA.get_template(template).render(**context)

async def _ask_json(client, model, prompt):
    response = await client.responses.create(model=model, input=prompt + '\nUse at most five recipes and five realism rules; keep the JSON under 2,000 words.', max_output_tokens=6000, text={'format': {'type': 'json_object'}})
    return _json_from_text(response.output_text)

def _default_knowledge_base():
    return {'schema_version': '1.0', 'company': {'id': 'northstar', 'name': 'Northstar Office Systems', 'industry': 'office equipment and maintenance', 'country': 'US', 'base_currency': 'USD', 'accounting_standard': 'US_GAAP'}, 'departments': [{'id': x.lower().replace(' ', '_'), 'name': x, 'headcount_weight': 0.2} for x in ('Sales', 'Operations', 'Finance', 'Customer Success', 'Engineering')], 'employee_roles': [{'id': 'account_executive', 'title': 'Account Executive', 'department_id': 'sales', 'headcount_weight': 0.25, 'salary_min_cents': 6500000, 'salary_max_cents': 9500000}, {'id': 'accountant', 'title': 'Staff Accountant', 'department_id': 'finance', 'headcount_weight': 0.15, 'salary_min_cents': 7000000, 'salary_max_cents': 10000000}, {'id': 'operations_specialist', 'title': 'Operations Specialist', 'department_id': 'operations', 'headcount_weight': 0.6, 'salary_min_cents': 5500000, 'salary_max_cents': 8500000}], 'customer_segments': [{'id': 'small_business', 'name': 'Small Business', 'weight': 0.7, 'payment_term_days': 30, 'credit_limit_min_cents': 250000, 'credit_limit_max_cents': 2500000}, {'id': 'mid_market', 'name': 'Mid-Market', 'weight': 0.3, 'payment_term_days': 45, 'credit_limit_min_cents': 2500000, 'credit_limit_max_cents': 15000000}], 'vendor_categories': [{'id': 'equipment', 'name': 'Equipment suppliers', 'expense_account_role': 'inventory', 'weight': 0.65}, {'id': 'services', 'name': 'Business services', 'expense_account_role': 'operating_expense', 'weight': 0.35}], 'offerings': [{'id': 'ergonomic_chair', 'name': 'Ergonomic Office Chair', 'kind': 'inventory', 'unit': 'each', 'unit_price_cents': 44900, 'unit_cost_cents': 26000, 'revenue_policy_id': 'point_in_time', 'order_quantity_min': 1, 'order_quantity_max': 8}, {'id': 'standing_desk', 'name': 'Standing Desk', 'kind': 'inventory', 'unit': 'each', 'unit_price_cents': 89900, 'unit_cost_cents': 54000, 'revenue_policy_id': 'point_in_time', 'order_quantity_min': 1, 'order_quantity_max': 5}, {'id': 'maintenance', 'name': 'Managed Maintenance', 'kind': 'subscription', 'unit': 'month', 'unit_price_cents': 19900, 'unit_cost_cents': 4000, 'revenue_policy_id': 'ratable', 'order_quantity_min': 1, 'order_quantity_max': 1}], 'accounting_policies': {'fiscal_year_start_month': 1, 'monthly_close_day': 5, 'default_payment_term_days': 30, 'tax_rate_basis_points': 825}, 'activity_profile': {'seasonality': 'Sales peak in Q4 and soften in midsummer.', 'sales_mix': [{'value': 'one_time', 'weight': 0.7}, {'value': 'subscription', 'weight': 0.3}], 'payment_methods': [{'value': 'ach', 'weight': 0.7}, {'value': 'card', 'weight': 0.3}]}}

def _normalize_knowledge_base(value):
    default = _default_knowledge_base()
    if not isinstance(value, dict):
        return default
    default['company'].update(value.get('company') or {})
    for key in ('departments', 'employee_roles', 'customer_segments', 'vendor_categories', 'offerings'):
        if value.get(key):
            default[key] = value[key]
    for key in ('accounting_policies', 'activity_profile'):
        default[key].update(value.get(key) or {})
    default['schema_version'] = '1.0'
    return default

def _fingerprint(world_parameters, schema_path):
    digest = hashlib.sha256()
    digest.update(json.dumps(asdict(world_parameters), sort_keys=True, default=str).encode())
    digest.update(schema_path.read_bytes())
    digest.update(PIPELINE_VERSION.encode())
    for path in sorted(TEMPLATES.glob('*.jinja2')):
        digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()

def _validate(world_parameters):
    if world_parameters.end_date < world_parameters.start_date:
        raise ValueError('end_date must be on or after start_date')
    duration = (world_parameters.end_date - world_parameters.start_date).days + 1
    if duration != world_parameters.scenario_duration_days:
        raise ValueError(f'scenario_duration_days must equal inclusive date range ({duration})')
    counts = (world_parameters.num_departments, world_parameters.num_employees, world_parameters.num_customers, world_parameters.num_subscriptions, world_parameters.num_sales_orders)
    if any(value < 1 for value in counts):
        raise ValueError('all generation counts must be positive')
    if not 0 <= world_parameters.cross_domain_link_rate <= 1:
        raise ValueError('cross_domain_link_rate must be between 0 and 1')
    if not 0 <= world_parameters.exception_rate <= 1:
        raise ValueError('exception_rate must be between 0 and 1')
    if not 0 < world_parameters.target_size_gb <= 2:
        raise ValueError('target_size_gb must be greater than 0 and at most 2')

def _extract_knowledge_base(world_parameters, state_dir, schema_path):
    knowledge_path, registry_path = (state_dir / 'knowledge_base.json', state_dir / 'entity_registry.json')
    if not knowledge_path.exists():
        prompt = _render('knowledge_base.jinja2', world_description=world_parameters.world_description, parameters_json=json.dumps(asdict(world_parameters), default=str), schema_context=_schema_context(schema_path))
        knowledge = asyncio.run(_ask_json(AsyncOpenAI(), world_parameters.model, prompt))
        _json(knowledge_path, _normalize_knowledge_base(knowledge))
    if registry_path.exists():
        return
    knowledge, rng = (_json(knowledge_path), random.Random(world_parameters.random_seed))
    company_domain = (
        re.sub(r'[^a-z0-9]+', '', knowledge['company']['name'].lower()) + '.com'
    )
    fake = Faker()
    fake.seed_instance(world_parameters.random_seed)
    source_departments = knowledge['departments'] or _default_knowledge_base()['departments']
    departments = [{'id': index, 'key': source_departments[index % len(source_departments)]['id'], 'name': source_departments[index % len(source_departments)]['name']} for index in range(1, world_parameters.num_departments + 1)]
    roles = knowledge['employee_roles']
    employees = []
    for index in range(1, world_parameters.num_employees + 1):
        role, department = (roles[(index - 1) % len(roles)], departments[(index - 1) % len(departments)])
        first, last = (fake.first_name(), fake.last_name())
        employees.append({'id': index, 'employee_id': f'EMP-{index:04d}', 'first_name': first, 'last_name': last, 'email': f'{first}.{last}.{index}@{company_domain}'.lower(), 'phone': fake.phone_number(), 'title': role['title'], 'department_id': department['id'], 'hire_date': (world_parameters.start_date - timedelta(days=rng.randint(30, 2500))).isoformat()})
    segments = knowledge['customer_segments']
    customers = []
    for index in range(1, world_parameters.num_customers + 1):
        segment = segments[(index - 1) % len(segments)]
        customers.append({'id': index, 'customer_id': f'CUST-{index:05d}', 'company_name': fake.company(), 'email': fake.company_email(), 'phone': fake.phone_number(), 'address': fake.address().replace('\n', ', '), 'segment': segment['id'], 'payment_terms': f"Net {segment.get('payment_term_days', 30)}", 'credit_limit_cents': rng.randint(int(segment.get('credit_limit_min_cents', 250000)), int(segment.get('credit_limit_max_cents', 2500000)))})
    vendor_count = max(12, min(100, world_parameters.num_customers // 5))
    categories = knowledge['vendor_categories']
    vendors = [{'id': index, 'vendor_id': f'VEND-{index:04d}', 'company_name': fake.company(), 'email': fake.company_email(), 'phone': fake.phone_number(), 'address': fake.address().replace('\n', ', '), 'category': categories[(index - 1) % len(categories)]['id']} for index in range(1, vendor_count + 1)]
    offerings = []
    for index, source in enumerate(knowledge['offerings'], 1):
        offerings.append({'id': index, 'key': source.get('id', f'offering_{index}'), 'name': source.get('name', f'Offering {index}'), 'kind': source.get('kind', 'inventory'), 'unit': source.get('unit', 'each'), 'price_cents': max(100, int(source.get('unit_price_cents', 10000))), 'cost_cents': max(0, int(source.get('unit_cost_cents', 4000)))})
    registry = {'company': knowledge['company'], 'departments': departments, 'employees': employees, 'customers': customers, 'vendors': vendors, 'offerings': offerings}
    _json(registry_path, registry)

def _workflow_context(workflow_name):
    root = ROOT / 'theseus-stratum-deployed-bundle' / 'envspec' / 'workflows'
    return '\n\n'.join((root / f'{name}.yml').read_text() for name in WORKFLOWS[workflow_name]['specs'] if (root / f'{name}.yml').exists())

def _normalize_workflow_plan(workflow_name, value):
    config = WORKFLOWS[workflow_name]
    allowed_steps, allowed_exceptions = set(config['steps']), set(config['exceptions'])
    raw_variants = value.get('variants', value.get('workflow_mix', [])) if isinstance(value, dict) else []
    variants = []
    for index, raw in enumerate(raw_variants[:5], 1):
        if not isinstance(raw, dict):
            continue
        steps = []
        for source in raw.get('steps', []):
            operation = source if isinstance(source, str) else source.get('operation')
            parameters = {} if isinstance(source, str) else source.get('parameters', {})
            if operation in allowed_steps and isinstance(parameters, dict):
                steps.append({'operation': operation, 'parameters': parameters})
        if not steps:
            continue
        exception = raw.get('exception')
        if exception not in allowed_exceptions:
            exception = None
        cadence = raw.get('cadence')
        if cadence not in CADENCES:
            cadence = 'monthly'
        variants.append({
            'name': re.sub(r'[^a-z0-9_]+', '_', str(raw.get('name') or f'{workflow_name}_{index}').lower()).strip('_'),
            'description': str(raw.get('description') or '').strip(),
            'weight': max(0.0, float(raw.get('weight', 0))),
            'cadence': cadence,
            'steps': steps,
            'exception': exception,
        })
    if not variants:
        variants = [{'name': f'{workflow_name}_standard', 'description': f'Standard {workflow_name.replace("_", " ")} workflow.', 'weight': 1.0, 'cadence': 'monthly', 'steps': [{'operation': step, 'parameters': {}} for step in config['steps']], 'exception': None}]
    total = sum(variant['weight'] for variant in variants)
    for variant in variants:
        variant['weight'] = round((variant['weight'] / total) if total else (1 / len(variants)), 6)
    return {'domain': config['domain'], 'workflow': workflow_name, 'variants': variants}

async def _generate_workflow_plans(world_parameters, state_dir, schema_path, missing):
    knowledge, registry = (_json(state_dir / 'knowledge_base.json'), _json(state_dir / 'entity_registry.json'))
    shared = json.dumps({'knowledge_base': knowledge, 'entity_counts': {key: len(value) for key, value in registry.items() if isinstance(value, list)}}, separators=(',', ':'))
    client, semaphore = (AsyncOpenAI(), asyncio.Semaphore(4))

    async def generate_workflow(workflow_name):
        async with semaphore:
            config = WORKFLOWS[workflow_name]
            plan = await _ask_json(client, world_parameters.model, _render(
                'workflow_plan.jinja2',
                workflow_prompt=config['prompt'],
                parent_domain=config['domain'],
                allowed_steps=', '.join(config['steps']),
                allowed_exceptions=', '.join(config['exceptions']),
                json_output=json.dumps(WORKFLOW_OUTPUT),
                shared_context=shared,
                schema_context=_schema_context(schema_path),
                workflow_context=_workflow_context(workflow_name),
            ))
        _json(state_dir / 'workflow_plans' / f'{workflow_name}.json', _normalize_workflow_plan(workflow_name, plan))
    results = await asyncio.gather(*(generate_workflow(workflow_name) for workflow_name in missing), return_exceptions=True)
    errors = [error for error in results if isinstance(error, Exception)]
    if errors:
        raise RuntimeError('; '.join(str(error) for error in errors))


def _run_generation_phase(phase):
    phase()

def _setup_master(phase): _run_generation_phase(phase)
def _lead_to_cash(phase): _run_generation_phase(phase)
def _procure_to_pay(phase): _run_generation_phase(phase)
def _subscription_to_revenue(phase): _run_generation_phase(phase)
def _expense_to_asset(phase): _run_generation_phase(phase)
def _treasury_to_close(phase): _run_generation_phase(phase)
def _plan_to_consolidate(phase): _run_generation_phase(phase)

def _atomic_jsonl(path, values):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as output:
        for value in values:
            output.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
    temporary.replace(path)

def _write_generated_records(state_dir, schema_path, rows, events, workflow_instances, step_executions):
    columns, tables_dir = _schema_columns(schema_path), state_dir / 'tables'
    shutil.rmtree(tables_dir, ignore_errors=True)
    tables_dir.mkdir(parents=True)
    for table, records in sorted(rows.items()):
        allowed = columns[table]
        keys = ['id'] + sorted(set().union(*(record.keys() for record in records)) & allowed - {'id'})
        values = ({key: record.get(key) for key in keys}
                  for record in sorted(records, key=lambda row: row['id']))
        _atomic_jsonl(tables_dir / f'{table}.jsonl', values)
    _atomic_jsonl(state_dir / 'event_ledger.jsonl', events)
    _atomic_jsonl(state_dir / 'workflow_instances.jsonl', workflow_instances)
    _atomic_jsonl(state_dir / 'workflow_step_executions.jsonl', step_executions)

def _generate_records(world_parameters, state_dir, schema_path):
    plan_dir = state_dir / 'workflow_plans'
    missing = [name for name in WORKFLOWS if not (plan_dir / f'{name}.json').exists()]
    if missing:
        asyncio.run(_generate_workflow_plans(world_parameters, state_dir, schema_path, missing))
    knowledge, registry = (_json(state_dir / 'knowledge_base.json'), _json(state_dir / 'entity_registry.json'))
    plans = {name: _json(plan_dir / f'{name}.json') for name in WORKFLOWS}
    rng, rows, ids, events = (random.Random(world_parameters.random_seed), defaultdict(list), defaultdict(int), [])
    workflow_instances, step_executions = [], []
    created = f'{world_parameters.start_date.isoformat()} 09:00:00'

    def add(table, **values):
        ids[table] += 1
        row = {'id': ids[table], 'created_at': created, 'updated_at': created, **values}
        rows[table].append(row)
        return row

    def money(cents):
        return round(cents / 100, 2)

    def workflow(workflow_name, index):
        variants = plans[workflow_name].get('variants') or [{'name': f'{workflow_name}_standard', 'steps': [], 'exception': None}]
        weights = [max(0, float(item.get('weight', 0))) for item in variants]
        if not any(weights):
            return variants[index % len(variants)]
        return rng.choices(variants, weights=weights, k=1)[0]

    def recipe(workflow_name, index):
        return workflow(workflow_name, index).get('name', f'{workflow_name}_standard')

    def matches(flow, *terms):
        values = {
            step if isinstance(step, str) else step.get('operation')
            for step in flow.get('steps', [])
        }
        values.add(flow.get('exception'))
        return any(term in values for term in terms)

    def trace(workflow_name, flow, instance_key, when, source_table, source_id, amount_cents=0, effects=None, upstream_instance_id=None, timeline=None):
        instance_id = len(workflow_instances) + 1
        steps = flow.get('steps', [])
        effects = effects or []
        timeline = timeline or []
        workflow_instances.append({
            'id': instance_id,
            'instance_key': instance_key,
            'domain': WORKFLOWS[workflow_name]['domain'],
            'workflow': workflow_name,
            'variant': flow.get('name', f'{workflow_name}_standard'),
            'description': flow.get('description', ''),
            'cadence': flow.get('cadence'),
            'exception': flow.get('exception'),
            'status': 'completed',
            'date': when.isoformat(),
            'source_table': source_table,
            'source_id': source_id,
            'amount_cents': amount_cents,
            'upstream_instance_id': upstream_instance_id,
        })
        last_date = when
        for sequence, step_spec in enumerate(steps, 1):
            step = step_spec if isinstance(step_spec, str) else step_spec.get('operation')
            parameters = {} if isinstance(step_spec, str) else step_spec.get('parameters', {})
            effect = effects[min(sequence - 1, len(effects) - 1)] if effects else {}
            event = timeline[min(sequence - 1, len(timeline) - 1)] if timeline else {}
            step_date = event.get('date', effect.get('date', last_date))
            if isinstance(step_date, str):
                step_date = date.fromisoformat(step_date)
            step_date = max(last_date, step_date)
            last_date = step_date
            step_executions.append({
                'id': len(step_executions) + 1,
                'workflow_instance_id': instance_id,
                'sequence': sequence,
                'step': step,
                'status': effect.get('status', 'completed'),
                'date': step_date.isoformat(),
                'affected_table': effect.get('table', source_table if sequence == len(steps) else None),
                'record_id': effect.get('id', source_id if sequence == len(steps) else None),
                'operation': effect.get('operation', 'update' if effect else 'observe'),
                'parameters': json.dumps(parameters, sort_keys=True),
                'before_summary': json.dumps(effect.get('before')) if effect.get('before') is not None else None,
                'after_summary': json.dumps(effect.get('after')) if effect.get('after') is not None else None,
            })
        return instance_id

    def workflow_date(flow, index, days_before_end=0):
        interval = {'daily': 1, 'weekly': 7, 'monthly': 30, 'quarterly': 91, 'annual': 365}.get(flow.get('cadence'))
        if not interval:
            return random_date(days_before_end)
        latest = max(start, end - timedelta(days=days_before_end))
        span = (latest - start).days + 1
        return start + timedelta(days=(index * interval) % span)
    start, end = (world_parameters.start_date, world_parameters.end_date)

    def random_date(days_before_end=0):
        latest = max(start, end - timedelta(days=days_before_end))
        return start + timedelta(days=rng.randint(0, (latest - start).days))

    def clamp(value):
        return min(max(value, start), end)
    periods, accounts, bank, opening_cash, customer_rows, vendor_rows, item_rows, inventory = (None,) * 8
    period_for = journal = receive_payment = None
    cash_change = 0
    def setup_master():
        nonlocal periods, accounts, bank, opening_cash, customer_rows, vendor_rows, item_rows, inventory
        nonlocal period_for, journal, receive_payment
        company = knowledge['company']
        add('subsidiaries', subsidiary_id='NORTHSTAR', name=company['name'], legal_name=company['name'] + ', Inc.', country=company.get('country', 'US'), state='CA', base_currency=company.get('base_currency', 'USD'), fiscal_calendar='Calendar Year', accounting_standard='GAAP')
        add('currencies', currency_id='USD', name='US Dollar', symbol='$', iso_code='USD', exchange_rate=1, is_base_currency=1, decimal_precision=2, format_sample='$1,234.56')
        for key, value in {'base_currency': 'USD', 'fiscal_year_start_month': '1', 'monthly_close_day': str(knowledge['accounting_policies'].get('monthly_close_day', 5))}.items():
            add('company_preferences', key=key, value=value, description=f'Bonsai policy: {key}')
        for department in registry['departments']:
            add('departments', department_id=department['key'], name=department['name'], subsidiary_id=1)
        add('locations', location_id='HQ', name='San Francisco Headquarters', address='100 Market Street, San Francisco, CA 94105', subsidiary_id=1, make_inventory_available=1)
        for key, name in (('PRODUCT', 'Product'), ('SERVICE', 'Services'), ('CORPORATE', 'Corporate')):
            add('classes', class_id=key, name=name, subsidiary_id=1)
        cursor, periods = (date(start.year, start.month, 1), [])
        while cursor <= end:
            next_month = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            period_start, period_end = (max(start, cursor), min(end, next_month - timedelta(days=1)))
            periods.append(add('accounting_periods', period_name=period_start.strftime('%b %Y'), start_date=period_start.isoformat(), end_date=period_end.isoformat(), fiscal_year=period_start.year, quarter=(period_start.month - 1) // 3 + 1, is_year_end=int(period_start.month == 12), is_quarter_end=int(period_start.month % 3 == 0), subsidiary_id=1, ar_locked=int(period_end < end.replace(day=1)), ap_locked=int(period_end < end.replace(day=1)), all_locked=int(period_end < end.replace(day=1)), status='closed' if period_end < end.replace(day=1) else 'open'))
            cursor = next_month

        def period_for(value):
            return next(period for period in periods if period['start_date'] <= value.isoformat() <= period['end_date'])
        add('accounting_books', book_id='PRIMARY', name='Northstar Primary Book', description='US GAAP accrual accounting', accounting_standard='US_GAAP', base_currency='USD', is_primary=1, subsidiary_id=1)
        account_specs = (('cash', '1000', 'Operating Cash', 'asset', 'bank'), ('ar', '1100', 'Accounts Receivable', 'asset', 'accounts_receivable'), ('inventory', '1200', 'Inventory', 'asset', 'inventory'), ('fixed_assets', '1500', 'Fixed Assets', 'asset', 'fixed_asset'), ('accum_depr', '1590', 'Accumulated Depreciation', 'asset', 'contra_asset'), ('ap', '2000', 'Accounts Payable', 'liability', 'accounts_payable'), ('deferred_revenue', '2200', 'Deferred Revenue', 'liability', 'deferred_revenue'), ('revenue', '4000', 'Product Revenue', 'income', 'sales'), ('subscription_revenue', '4100', 'Subscription Revenue', 'income', 'subscription'), ('cogs', '5000', 'Cost of Goods Sold', 'expense', 'cogs'), ('operating_expense', '6100', 'Operating Expense', 'expense', 'operating'), ('depreciation', '6200', 'Depreciation Expense', 'expense', 'depreciation'), ('sales_tax', '2100', 'Sales Tax Payable', 'liability', 'tax'))
        accounts = {}
        for role, number, name, account_type, subtype in account_specs:
            accounts[role] = add('accounts', account_number=number, name=name, type=account_type, subtype=subtype, currency='USD', subsidiary_id=1)
        add('payment_terms', term_id='NET30', name='Net 30', days_until_due=30)
        add('tax_codes', name='CA Sales Tax', description='Representative blended sales tax', rate=8.25, tax_type='sales', country='US', state='CA', tax_account_id=accounts['sales_tax']['id'], subsidiary_id=1, effective_date=start.isoformat())
        add('revenue_rules', rule_name='Point in Time', recognition_method='point_in_time', revenue_account_id=accounts['revenue']['id'], recognition_trigger='fulfillment', is_default=1, subsidiary_id=1)
        add('revenue_rules', rule_name='Monthly Ratable', recognition_method='straight_line', revenue_account_id=accounts['subscription_revenue']['id'], deferred_revenue_account_id=accounts['deferred_revenue']['id'], recognition_trigger='period', is_default=0, subsidiary_id=1)
        opening_cash = 500000000
        bank = add('bank_accounts', name='Operating Account', account_number='****4821', routing_number='121000248', bank_name='Northstar Commercial Bank', currency='USD', current_balance=money(opening_cash), available_balance=money(opening_cash), gl_account_id=accounts['cash']['id'], subsidiary_id=1, last_reconciled_date=start.isoformat())
        for employee in registry['employees']:
            add('employees', employee_id=employee['employee_id'], first_name=employee['first_name'], last_name=employee['last_name'], email=employee['email'], phone=employee['phone'], title=employee['title'], department_id=employee['department_id'], subsidiary_id=1, location_id=1, hire_date=employee['hire_date'], expense_limit=5000, approval_limit=25000, status='active')
            add('users', user_id=f"USER-{employee['id']:04d}", email=employee['email'], first_name=employee['first_name'], last_name=employee['last_name'], employee_id=employee['id'], default_subsidiary_id=1, department_id=employee['department_id'], location_id=1, status='active')
        customer_rows = {}
        for customer in registry['customers']:
            customer_rows[customer['id']] = add('customers', customer_id=customer['customer_id'], company_name=customer['company_name'], email=customer['email'], phone=customer['phone'], billing_address=customer['address'], shipping_address=customer['address'], payment_terms=customer['payment_terms'], credit_limit=money(customer['credit_limit_cents']), balance=0, currency='USD', subsidiary_id=1, sales_rep_id=(customer['id'] - 1) % len(registry['employees']) + 1, category=customer['segment'], status='active')
        vendor_rows = {}
        for vendor in registry['vendors']:
            vendor_rows[vendor['id']] = add('vendors', vendor_id=vendor['vendor_id'], company_name=vendor['company_name'], legal_name=vendor['company_name'], email=vendor['email'], phone=vendor['phone'], address=vendor['address'], payment_terms='Net 30', payment_method='ACH', currency='USD', subsidiary_id=1, expense_account_id=accounts['operating_expense']['id'], category=vendor['category'], balance=0, status='active')
        item_rows = {}
        for offering in registry['offerings']:
            is_subscription = offering['kind'] == 'subscription'
            item_rows[offering['id']] = add('items', item_id=offering['key'].upper(), name=offering['name'], display_name=offering['name'], description=f"{offering['kind'].title()} offering", item_type='service' if is_subscription else 'inventory', base_unit=offering['unit'], income_account_id=accounts['subscription_revenue' if is_subscription else 'revenue']['id'], expense_account_id=accounts['operating_expense']['id'], asset_account_id=accounts['inventory']['id'], cogs_account_id=accounts['cogs']['id'], base_price=money(offering['price_cents']), cost=money(offering['cost_cents']), subsidiary_id=1, quantity_on_hand=5000 if not is_subscription else 0)

        def journal(when, memo, entries, source_type, source_id):
            entry = add('journal_entries', entry_number=f"JE-{ids['journal_entries'] + 1:07d}", date=when.isoformat(), posting_period_id=period_for(when)['id'], memo=memo, currency='USD', exchange_rate=1, subsidiary_id=1, created_from=source_type, status='posted', created_by_id=1, posted_by_id=1, posted_date=when.isoformat(), source_transaction_type=source_type, source_transaction_id=source_id)
            for line_number, (account, debit, credit, links) in enumerate(entries, 1):
                if debit or credit:
                    add('journal_entry_lines', journal_entry_id=entry['id'], line_number=line_number, account_id=account, debit=money(debit), credit=money(credit), memo=memo, subsidiary_id=1, **links)
            return entry
        def receive_payment(invoice, customer_id, when, amount_cents):
            nonlocal cash_change
            if amount_cents <= 0:
                return
            payment = add('payments', payment_number=f"PAY-{ids['payments'] + 1:07d}", customer_id=customer_id, date=when.isoformat(), payment_method=rng.choice(('ACH', 'card', 'wire')), reference_number=f"REF-{ids['payments'] + 1:07d}", amount=money(amount_cents), currency='USD', exchange_rate=1, ar_account_id=accounts['ar']['id'], deposit_account_id=accounts['cash']['id'], subsidiary_id=1, bank_account_id=bank['id'], status='deposited')
            entry = journal(when, f"Receipt for {invoice['invoice_number']}", [(accounts['cash']['id'], amount_cents, 0, {'payment_id': payment['id']}), (accounts['ar']['id'], 0, amount_cents, {'payment_id': payment['id'], 'invoice_id': invoice['id']})], 'payment', payment['id'])
            payment['journal_entry_id'] = entry['id']
            deposit = add('deposits', deposit_date=when.isoformat(), bank_account_id=bank['id'], total=money(amount_cents), memo=payment['payment_number'], subsidiary_id=1, status='deposited')
            payment['deposit_id'] = deposit['id']
            add('payment_applications', payment_id=payment['id'], invoice_id=invoice['id'], amount=money(amount_cents))
            invoice['amount_paid'] = round(invoice.get('amount_paid', 0) + money(amount_cents), 2)
            invoice['amount_due'] = round(invoice['total'] - invoice['amount_paid'], 2)
            cash_change += amount_cents
            return payment, deposit
    def lead_to_cash():
        nonlocal cash_change, inventory
        tax_bps = int(knowledge['accounting_policies'].get('tax_rate_basis_points', 825))
        inventory = [item for item in registry['offerings'] if item['kind'] != 'subscription'] or registry['offerings']
        for index in range(world_parameters.num_sales_orders):
            flow = workflow('lead_to_cash', index)
            flow_name = flow.get('name', 'lead_to_cash_standard')
            customer = registry['customers'][index % len(registry['customers'])]
            offering = inventory[index % len(inventory)]
            item, order_date = (item_rows[offering['id']], workflow_date(flow, index, 40))
            subtotal = offering['price_cents'] * rng.randint(1, 5)
            quantity = max(1, round(subtotal / offering['price_cents']))
            tax = round(subtotal * tax_bps / 10000)
            total = subtotal + tax
            estimate = add('estimates', estimate_number=f'EST-{index + 1:07d}', customer_id=customer['id'], date=order_date.isoformat(), valid_until=clamp(order_date + timedelta(days=30)).isoformat(), title=f'Proposal for {item["name"]}', sales_rep_id=index % len(registry['employees']) + 1, probability=90, expected_close_date=clamp(order_date + timedelta(days=5)).isoformat(), subsidiary_id=1, memo=flow_name, subtotal=money(subtotal), discount_total=0, tax_total=money(tax), total=money(total), document_data=json.dumps({'workflow': flow_name}), document_file_name=f'EST-{index + 1:07d}.json', document_content_type='application/json', status='accepted', created_by_id=1)
            add('estimate_lines', estimate_id=estimate['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity, rate=money(offering['price_cents']), amount=money(subtotal), tax_code_id=1, department_id=1, class_id=1, location_id=1)
            order = add('sales_orders', order_number=f'SO-{index + 1:07d}', customer_id=customer['id'], date=order_date.isoformat(), ship_date=clamp(order_date + timedelta(days=2)).isoformat(), expected_close_date=clamp(order_date + timedelta(days=5)).isoformat(), payment_term_id=1, currency='USD', exchange_rate=1, subsidiary_id=1, department_id=1, class_id=1, location_id=1, billing_address=customer['address'], shipping_address=customer['address'], ship_method='Ground', sales_rep_id=index % len(registry['employees']) + 1, memo=flow_name, subtotal=money(subtotal), tax_total=money(tax), total=money(total), amount_fulfilled=money(total), amount_billed=money(total), amount_remaining=0, status='closed', created_by_id=1)
            estimate['linked_sales_order_id'] = order['id']
            line = add('sales_order_lines', sales_order_id=order['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity, quantity_fulfilled=quantity, quantity_billed=quantity, rate=money(offering['price_cents']), amount=money(subtotal), tax_code_id=1, tax_rate=tax_bps / 100, gross_amount=money(total), department_id=1, class_id=1, location_id=1, is_closed=1)
            fulfillment_date = clamp(order_date + timedelta(days=rng.randint(8, 20) if matches(flow, 'delay', 'backorder') else rng.randint(1, 4)))
            add('item_fulfillments', fulfillment_number=f'FUL-{index + 1:07d}', sales_order_id=order['id'], date=fulfillment_date.isoformat(), ship_date=fulfillment_date.isoformat(), ship_method='Ground', carrier='UPS', tracking_number=f'1Z{index + 1:016d}', ship_to_address=customer['address'], location_id=1, subsidiary_id=1, status='shipped', created_by_id=1)
            invoice_date = clamp(fulfillment_date + timedelta(days=1))
            invoice = add('invoices', invoice_number=f"INV-{ids['invoices'] + 1:07d}", customer_id=customer['id'], date=invoice_date.isoformat(), due_date=(invoice_date + timedelta(days=30)).isoformat(), posting_period_id=period_for(invoice_date)['id'], terms='Net 30', currency='USD', exchange_rate=1, subtotal=money(subtotal), tax_total=money(tax), shipping_cost=0, total=money(total), amount_due=money(total), amount_paid=0, memo=flow_name, billing_address=customer['address'], shipping_address=customer['address'], sales_rep_id=order['sales_rep_id'], subsidiary_id=1, department_id=1, class_id=1, location_id=1, sales_order_id=order['id'], document_data=json.dumps({'order': order['order_number'], 'workflow_steps': flow.get('steps', []), 'workflow_exception': flow.get('exception')}), document_file_name=f"{order['order_number']}.json", document_content_type='application/json', status='open')
            add('invoice_lines', invoice_id=invoice['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity, units=offering['unit'], rate=money(offering['price_cents']), amount=money(subtotal), tax_code_id=1, tax_rate=tax_bps / 100, gross_amount=money(total), department_id=1, class_id=1, location_id=1, sales_order_line_id=line['id'])
            entry = journal(invoice_date, f"Invoice {invoice['invoice_number']}", [(accounts['ar']['id'], total, 0, {'invoice_id': invoice['id'], 'customer_id': customer['id']}), (accounts['revenue']['id'], 0, subtotal, {'invoice_id': invoice['id'], 'customer_id': customer['id']}), (accounts['sales_tax']['id'], 0, tax, {'invoice_id': invoice['id'], 'customer_id': customer['id']})], 'invoice', invoice['id'])
            invoice['journal_entry_id'] = entry['id']
            payment_dates, payment_records = [], []
            payment_steps = sum(
                (step if isinstance(step, str) else step.get('operation')) == 'receive_payment'
                for step in flow.get('steps', [])
            )
            if flow.get('exception') == 'payment_failure':
                paid = 0
            elif payment_steps > 1:
                first = total // payment_steps
                installments = [first] * (payment_steps - 1) + [total - first * (payment_steps - 1)]
                prior_date = invoice_date
                for installment in installments:
                    prior_date = clamp(prior_date + timedelta(days=rng.randint(5, 15)))
                    result = receive_payment(invoice, customer['id'], prior_date, installment)
                    if result:
                        payment_records.append(result)
                        payment_dates.append(prior_date)
                paid = total
            else:
                paid = total // 2 if flow.get('exception') == 'partial_payment' else total
                payment_date = clamp(invoice_date + timedelta(days=rng.randint(3, 20)))
                result = receive_payment(invoice, customer['id'], payment_date, paid)
                if result:
                    payment_records.append(result)
                    payment_dates.append(payment_date)
            customer_rows[customer['id']]['balance'] += money(total - paid)
            events.append({'id': f'sale:{index + 1}', 'type': 'sale', 'date': order_date.isoformat(), 'source_table': 'sales_orders', 'source_id': order['id'], 'amount_cents': total, 'recipe': flow_name, 'cadence': flow.get('cadence'), 'steps': flow.get('steps', []), 'exception': flow.get('exception')})
            timeline, effects, payment_index = [], [], 0
            for step_spec in flow.get('steps', []):
                operation = step_spec if isinstance(step_spec, str) else step_spec.get('operation')
                if operation == 'create_estimate':
                    step_date, effect = order_date, {'table': 'estimates', 'id': estimate['id'], 'operation': 'insert'}
                elif operation in ('create_sales_order', 'approve_order'):
                    step_date, effect = order_date, {'table': 'sales_orders', 'id': order['id'], 'operation': 'insert' if operation == 'create_sales_order' else 'update'}
                elif operation == 'fulfill_order':
                    step_date, effect = fulfillment_date, {'table': 'item_fulfillments', 'id': index + 1, 'operation': 'insert'}
                elif operation == 'issue_invoice':
                    step_date, effect = invoice_date, {'table': 'invoices', 'id': invoice['id'], 'operation': 'insert'}
                elif operation in ('receive_payment', 'make_deposit') and payment_records:
                    pair = payment_records[min(payment_index, len(payment_records) - 1)]
                    step_date = payment_dates[min(payment_index, len(payment_dates) - 1)]
                    record = pair[0] if operation == 'receive_payment' else pair[1]
                    effect = {'table': 'payments' if operation == 'receive_payment' else 'deposits', 'id': record['id'], 'operation': 'insert'}
                    if operation == 'make_deposit':
                        payment_index += 1
                else:
                    step_date, effect = invoice_date, {'status': 'completed'}
                timeline.append({'date': step_date})
                effects.append(effect)
            trace('lead_to_cash', flow, f'sale:{index + 1}', order_date, 'sales_orders', order['id'], total, effects, timeline=timeline)
    def procure_to_pay():
        nonlocal cash_change
        purchase_count = max(24, world_parameters.num_sales_orders // 4)
        for index in range(purchase_count):
            flow = workflow('procure_to_pay', index)
            flow_name = flow.get('name', 'procure_to_pay_standard')
            vendor = registry['vendors'][index % len(registry['vendors'])]
            offering = inventory[index % len(inventory)]
            item, purchase_date = (item_rows[offering['id']], workflow_date(flow, index, 35))
            quantity = rng.randint(2, 20)
            expected = offering['cost_cents'] * quantity
            requisition = add('requisitions', requisition_number=f'REQ-{index + 1:07d}', requestor_id=index % len(registry['employees']) + 1, date=purchase_date.isoformat(), needed_by_date=clamp(purchase_date + timedelta(days=14)).isoformat(), department_id=2, subsidiary_id=1, memo=flow_name, estimated_total=money(expected), approved_by_id=1, approved_date=purchase_date.isoformat(), status='approved', created_by_id=1)
            rfq = add('requests_for_quote', rfq_number=f'RFQ-{index + 1:07d}', name=f'Source {item["name"]}', open_date=purchase_date.isoformat(), close_date=clamp(purchase_date + timedelta(days=3)).isoformat(), terms='Net 30', special_instructions=flow_name, subsidiary_id=1, status='closed', created_by_id=1)
            add('request_for_quote_lines', rfq_id=rfq['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity)
            variance = round(expected * rng.choice((0.01, 0.02, 0.03))) if matches(flow, 'variance', 'mismatch', 'overbill') or rng.random() < world_parameters.exception_rate else 0
            actual = expected + variance
            order = add('purchase_orders', po_number=f'PO-{index + 1:07d}', vendor_id=vendor['id'], date=purchase_date.isoformat(), expected_date=clamp(purchase_date + timedelta(days=7)).isoformat(), subsidiary_id=1, total=money(expected), memo=flow_name, approval_status='approved', ship_to_location_id=1, ship_to_address='100 Market Street, San Francisco, CA 94105', payment_term_id=1, status='fully_billed')
            add('requisition_lines', requisition_id=requisition['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity, units=offering['unit'], estimated_rate=money(offering['cost_cents']), estimated_amount=money(expected), preferred_vendor_id=vendor['id'], is_ordered=1, linked_po_id=order['id'])
            add('purchase_order_lines', purchase_order_id=order['id'], line_number=1, item_id=item['id'], description=item['name'], quantity=quantity, quantity_received=quantity, quantity_billed=quantity, rate=money(offering['cost_cents']), amount=money(expected), department_id=2, class_id=1, location_id=1)
            receipt_date = clamp(purchase_date + timedelta(days=7))
            receipt = add('item_receipts', receipt_number=f'IR-{index + 1:07d}', purchase_order_id=order['id'], vendor_id=vendor['id'], date=receipt_date.isoformat(), location_id=1, subsidiary_id=1, memo=order['po_number'], status='received', created_by_id=1)
            add('item_receipt_lines', item_receipt_id=receipt['id'], line_number=1, item_id=item['id'], description=item['name'], quantity_ordered=quantity, quantity_to_receive=quantity, location='HQ')
            bill_date = clamp(receipt_date + timedelta(days=1))
            bill = add('bills', bill_number=f'BILL-{index + 1:07d}', vendor_bill_number=f'VB-{index + 1:07d}', vendor_id=vendor['id'], date=bill_date.isoformat(), due_date=(bill_date + timedelta(days=30)).isoformat(), posting_period_id=period_for(bill_date)['id'], terms='Net 30', currency='USD', exchange_rate=1, subtotal=money(actual), tax_total=0, total=money(actual), amount_due=money(actual), amount_paid=0, memo=flow_name, subsidiary_id=1, department_id=2, class_id=1, location_id=1, approval_status='approved', purchase_order_id=order['id'], status='open')
            add('bill_lines', bill_id=bill['id'], line_number=1, item_id=item['id'], expense_account_id=accounts['inventory']['id'], description=item['name'], quantity=quantity, units=offering['unit'], rate=money(actual // quantity), amount=money(actual), department_id=2, class_id=1, location_id=1)
            entry = journal(bill_date, f"Bill {bill['bill_number']}", [(accounts['inventory']['id'], actual, 0, {'bill_id': bill['id'], 'vendor_id': vendor['id']}), (accounts['ap']['id'], 0, actual, {'bill_id': bill['id'], 'vendor_id': vendor['id']})], 'bill', bill['id'])
            bill['journal_entry_id'] = entry['id']
            if variance:
                add('bill_variances', bill_id=bill['id'], purchase_order_id=order['id'], item_receipt_id=receipt['id'], variance_type='price', expected_amount=money(expected), actual_amount=money(actual), variance_amount=money(variance), tolerance_percent=5, within_tolerance=int(variance <= expected * 0.05), reviewed_by_id=1, review_date=bill_date.isoformat(), status='approved')
            paid = actual // 2 if matches(flow, 'partial_payment', 'payment_hold') or rng.random() < world_parameters.exception_rate else actual
            payment_date = clamp(bill_date + timedelta(days=rng.randint(5, 25)))
            payment = add('bill_payments', payment_number=f'BPAY-{index + 1:07d}', vendor_id=vendor['id'], date=payment_date.isoformat(), payment_method='ACH', reference_number=f'ACH-{index + 1:07d}', amount=money(paid), currency='USD', exchange_rate=1, memo=bill['bill_number'], ap_account_id=accounts['ap']['id'], bank_account_id=bank['id'], subsidiary_id=1, status='paid')
            payment_entry = journal(payment_date, f"Payment {payment['payment_number']}", [(accounts['ap']['id'], paid, 0, {'bill_id': bill['id'], 'bill_payment_id': payment['id']}), (accounts['cash']['id'], 0, paid, {'bill_id': bill['id'], 'bill_payment_id': payment['id']})], 'bill_payment', payment['id'])
            payment['journal_entry_id'] = payment_entry['id']
            add('bill_payment_applications', bill_payment_id=payment['id'], bill_id=bill['id'], amount=money(paid))
            bill['amount_paid'], bill['amount_due'] = (money(paid), money(actual - paid))
            vendor_rows[vendor['id']]['balance'] += money(actual - paid)
            cash_change -= paid
            events.append({'id': f'purchase:{index + 1}', 'type': 'purchase', 'date': purchase_date.isoformat(), 'source_table': 'purchase_orders', 'source_id': order['id'], 'amount_cents': actual, 'recipe': flow_name, 'cadence': flow.get('cadence'), 'steps': flow.get('steps', []), 'exception': flow.get('exception')})
            trace('procure_to_pay', flow, f'purchase:{index + 1}', purchase_date, 'purchase_orders', order['id'], actual, [
                {'table': 'purchase_orders', 'id': order['id'], 'operation': 'insert', 'after': {'total': order['total']}},
                {'table': 'item_receipts', 'id': receipt['id'], 'operation': 'insert', 'after': {'status': receipt['status']}},
                {'table': 'bills', 'id': bill['id'], 'operation': 'insert', 'after': {'total': bill['total'], 'amount_due': bill['amount_due']}},
                {'table': 'bill_payments', 'id': payment['id'], 'operation': 'insert', 'after': {'amount': payment['amount']}},
            ])
    def subscription_to_revenue():
        subscription_offerings = [x for x in registry['offerings'] if x['kind'] == 'subscription'] or [registry['offerings'][-1]]
        offering = subscription_offerings[0]
        subscription_department = next((department for department in registry['departments'] if department['key'] == 'customer_success'), registry['departments'][0])
        plan = add('subscription_plans', plan_id='MAINTENANCE', name='Managed Maintenance Plan', description=offering['name'], billing_mode='advance', subscription_type='usage_and_recurring', default_term=12, auto_renewal=1, proration_type='daily', currency='USD', initial_term=12, renewal_term=12, usage_period='monthly', status='active')
        add('subscription_plan_lines', subscription_plan_id=plan['id'], line_number=1, item_id=item_rows[offering['id']]['id'], subscription_line_type='recurring', quantity=1, include_in_renewal=1, is_required=1, proration_option='prorate')
        rating_runs = {period['id']: add('rating_runs', run_date=period['end_date'], subscription_filter='active', records_processed=world_parameters.num_subscriptions, charges_created=world_parameters.num_subscriptions, total_amount=money(offering['price_cents'] * world_parameters.num_subscriptions), status='completed', created_by_id=1, completed_date=period['end_date']) for period in periods}
        for index in range(world_parameters.num_subscriptions):
            flow = workflow('subscription_to_revenue', index)
            flow_name = flow.get('name', 'subscription_to_revenue_standard')
            customer = registry['customers'][index % len(registry['customers'])]
            subscription_start = workflow_date(flow, index)
            active_periods = [period for period in periods if period['end_date'] >= subscription_start.isoformat()]
            mrr, total = (offering['price_cents'], offering['price_cents'] * len(active_periods))
            subscription = add('subscriptions', subscription_number=f'SUB-{index + 1:06d}', customer_id=customer['id'], subscription_plan_id=plan['id'], start_date=subscription_start.isoformat(), end_date=active_periods[-1]['end_date'], term_months=len(active_periods), billing_frequency='monthly', next_billing_date=active_periods[0]['end_date'], auto_renew=1, renewal_term_months=12, mrr=money(mrr), arr=money(mrr * 12), tcv=money(total), currency='USD', subsidiary_id=1, sales_rep_id=index % len(registry['employees']) + 1, status='active')
            subscription_line = add('subscription_lines', subscription_id=subscription['id'], line_number=1, item_id=item_rows[offering['id']]['id'], quantity=1, rate=money(mrr), recurring_amount=money(mrr), billing_frequency='monthly', start_date=subscription_start.isoformat(), end_date=active_periods[-1]['end_date'], usage_multiplier=1, status='active')
            arrangement = add('revenue_arrangements', arrangement_number=f'RA-{index + 1:06d}', customer_id=customer['id'], arrangement_date=subscription_start.isoformat(), total_arrangement_value=money(total), allocated_amount=money(total), recognized_amount=money(total), deferred_amount=0, accounting_standard='ASC_606', fair_value_method='standalone_selling_price', subsidiary_id=1, status='active')
            element = add('revenue_elements', arrangement_id=arrangement['id'], item_id=item_rows[offering['id']]['id'], description=offering['name'], quantity=1, standalone_selling_price=money(total), allocated_amount=money(total), recognized_amount=money(total), deferred_amount=0, recognition_rule_id=2, start_date=subscription_start.isoformat(), end_date=active_periods[-1]['end_date'], satisfaction_method='over_time', status='complete')
            revenue_plan = add('revenue_plans', plan_number=f'RP-{index + 1:06d}', source_transaction='subscription', revenue_element_id=element['id'], total_amount=money(total), recognized_amount=money(total), deferred_amount=0, start_date=subscription_start.isoformat(), end_date=active_periods[-1]['end_date'], recognition_method='straight_line', posting_period_id=active_periods[0]['id'], status='complete')
            for month_index, period in enumerate(active_periods):
                bill_date = date.fromisoformat(max(period['start_date'], subscription_start.isoformat()))
                quantity = rng.randint(80, 400)
                usage = add('usage_records', subscription_id=subscription['id'], subscription_line_id=subscription_line['id'], usage_date=bill_date.isoformat(), quantity=quantity, unit='service_units', rated_amount=money(mrr), item_id=item_rows[offering['id']]['id'], memo=flow_name, external_id=f"USAGE-{index + 1:06d}-{period['id']:03d}", status='rated', rating_run_id=rating_runs[period['id']]['id'])
                invoice = add('invoices', invoice_number=f"INV-{ids['invoices'] + 1:07d}", customer_id=customer['id'], date=bill_date.isoformat(), due_date=(bill_date + timedelta(days=30)).isoformat(), posting_period_id=period['id'], terms='Net 30', currency='USD', exchange_rate=1, subtotal=money(mrr), tax_total=0, shipping_cost=0, total=money(mrr), amount_due=money(mrr), amount_paid=0, memo=subscription['subscription_number'], billing_address=customer['address'], shipping_address=customer['address'], sales_rep_id=subscription['sales_rep_id'], subsidiary_id=1, department_id=subscription_department['id'], class_id=2, location_id=1, status='open')
                add('invoice_lines', invoice_id=invoice['id'], line_number=1, item_id=item_rows[offering['id']]['id'], description=offering['name'], quantity=1, units='month', rate=money(mrr), amount=money(mrr), department_id=subscription_department['id'], class_id=2, location_id=1, revenue_recognition_rule_id=2, rev_rec_start_date=period['start_date'], rev_rec_end_date=period['end_date'])
                add('charges', subscription_id=subscription['id'], subscription_line_id=subscription_line['id'], rating_run_id=rating_runs[period['id']]['id'], charge_date=bill_date.isoformat(), charge_type='recurring_and_usage', quantity=quantity, rate=money(mrr / quantity), amount=money(mrr), usage_record_id=usage['id'], invoice_id=invoice['id'], status='invoiced')
                invoice_entry = journal(bill_date, f"Subscription invoice {invoice['invoice_number']}", [(accounts['ar']['id'], mrr, 0, {'invoice_id': invoice['id'], 'customer_id': customer['id']}), (accounts['deferred_revenue']['id'], 0, mrr, {'invoice_id': invoice['id'], 'customer_id': customer['id']})], 'subscription_invoice', invoice['id'])
                invoice['journal_entry_id'] = invoice_entry['id']
                paid = mrr // 2 if matches(flow, 'partial_payment', 'payment_failure', 'autopay_failure') or rng.random() < world_parameters.exception_rate else mrr
                receive_payment(invoice, customer['id'], clamp(bill_date + timedelta(days=10)), paid)
                customer_rows[customer['id']]['balance'] += money(mrr - paid)
                recognition_date = date.fromisoformat(period['end_date'])
                recognition_entry = journal(recognition_date, f"Revenue recognition {subscription['subscription_number']}", [(accounts['deferred_revenue']['id'], mrr, 0, {}), (accounts['subscription_revenue']['id'], 0, mrr, {})], 'revenue_recognition', revenue_plan['id'])
                add('revenue_plan_lines', plan_id=revenue_plan['id'], period_id=period['id'], recognition_date=recognition_date.isoformat(), amount=money(mrr), percent=round(100 / len(active_periods), 6), journal_entry_id=recognition_entry['id'], posted_date=recognition_date.isoformat(), notes=flow_name, status='posted')
                add('revenue_recognition_journals', journal_number=f"RRJ-{ids['revenue_recognition_journals'] + 1:07d}", revenue_plan_id=revenue_plan['id'], period_id=period['id'], amount=money(mrr), deferred_revenue_account_id=accounts['deferred_revenue']['id'], recognized_revenue_account_id=accounts['subscription_revenue']['id'], journal_entry_id=recognition_entry['id'], status='posted', posted_date=recognition_date.isoformat())
                events.append({'id': f"subscription:{index + 1}:{period['id']}", 'type': 'subscription_billing', 'date': bill_date.isoformat(), 'source_table': 'subscriptions', 'source_id': subscription['id'], 'amount_cents': mrr, 'recipe': flow_name, 'cadence': flow.get('cadence'), 'steps': flow.get('steps', []), 'exception': flow.get('exception')})
                trace('subscription_to_revenue', flow, f"subscription:{index + 1}:{period['id']}", bill_date, 'subscriptions', subscription['id'], mrr)
    def expense_to_asset():
        nonlocal cash_change
        expense_count = max(24, world_parameters.num_employees)
        for index in range(expense_count):
            flow = workflow('expense_to_asset', index)
            flow_name = flow.get('name', 'expense_to_asset_standard')
            employee = registry['employees'][index % len(registry['employees'])]
            expense_date, amount = (workflow_date(flow, index), rng.randint(4000, 60000))
            report = add('expense_reports', report_number=f'EXP-{index + 1:06d}', employee_id=employee['id'], report_date=expense_date.isoformat(), submit_date=expense_date.isoformat(), total=money(amount), subsidiary_id=1, department_id=employee['department_id'], memo=flow_name, reimbursement_amount=money(amount), status='paid')
            add('expense_report_lines', expense_report_id=report['id'], line_number=1, expense_date=expense_date.isoformat(), category=rng.choice(('Travel', 'Meals', 'Software')), amount=money(amount), currency='USD', exchange_rate=1, description=flow_name, receipt_attached=int(not matches(flow, 'missing_receipt')), receipt_data=json.dumps({'merchant': 'Business Merchant', 'amount_cents': amount}), receipt_file_name=f'receipt-{index + 1}.json', receipt_content_type='application/json', department_id=employee['department_id'], class_id=3, location_id=1, account_id=accounts['operating_expense']['id'])
            journal(expense_date, f"Expense report {report['report_number']}", [(accounts['operating_expense']['id'], amount, 0, {}), (accounts['cash']['id'], 0, amount, {})], 'expense_report', report['id'])
            cash_change -= amount
            events.append({'id': f'expense:{index + 1}', 'type': 'employee_expense', 'date': expense_date.isoformat(), 'source_table': 'expense_reports', 'source_id': report['id'], 'amount_cents': amount, 'recipe': flow_name, 'cadence': flow.get('cadence'), 'steps': flow.get('steps', []), 'exception': flow.get('exception')})
            trace('expense_to_asset', flow, f'expense:{index + 1}', expense_date, 'expense_reports', report['id'], amount)
        for index in range(min(12, world_parameters.num_employees)):
            cost = rng.randint(150000, 1500000)
            monthly = (cost - cost // 10) // 36
            flow = workflow('expense_to_asset', index)
            asset = add('fixed_assets', asset_number=f'FA-{index + 1:04d}', name=f'Office Asset {index + 1}', description=flow.get('name'), asset_type='office_equipment', acquisition_date=start.isoformat(), placed_in_service_date=start.isoformat(), original_cost=money(cost), residual_value=money(cost // 10), useful_life_months=36, depreciation_method='straight_line', accumulated_depreciation=money(monthly * len(periods)), current_book_value=money(max(cost // 10, cost - monthly * len(periods))), location_id=1, custodian_id=index + 1, subsidiary_id=1, asset_account_id=accounts['fixed_assets']['id'], depreciation_account_id=accounts['depreciation']['id'], accumulated_depr_account_id=accounts['accum_depr']['id'], status='active')
            for period_index, period in enumerate(periods, 1):
                amount = min(monthly, max(0, cost - cost // 10 - monthly * (period_index - 1)))
                add('depreciation_schedules', fixed_asset_id=asset['id'], period_id=period['id'], depreciation_amount=money(amount), accumulated_depreciation=money(amount * period_index), book_value=money(cost - amount * period_index), is_posted=1)
                journal(date.fromisoformat(period['end_date']), f"Depreciation {asset['asset_number']}", [(accounts['depreciation']['id'], amount, 0, {}), (accounts['accum_depr']['id'], 0, amount, {})], 'depreciation', asset['id'])
            trace('expense_to_asset', flow, f'asset:{index + 1}', start, 'fixed_assets', asset['id'], cost)
    def plan_to_consolidate():
        governance_flow = workflow('plan_to_consolidate', 0)
        for department in registry['departments']:
            annual = rng.randint(8000000, 24000000)
            flow = workflow('plan_to_consolidate', department['id'])
            budget = add('budgets', budget_name=f"{department['name']} Operating Budget", fiscal_year=start.year, version=1, budget_type='operating', subsidiary_id=1, department_id=department['id'], total_amount=money(annual), created_by_id=1, approved_by_id=1, approval_date=start.isoformat(), notes=flow.get('name'), status='approved')
            forecast = add('forecasts', forecast_name=f"{department['name']} Rolling Forecast", fiscal_year=start.year, period_start=start.isoformat(), period_end=end.isoformat(), version=1, forecast_type='rolling', subsidiary_id=1, created_by_id=1, finalized_by_id=1, finalized_date=end.isoformat(), notes=flow.get('name'), status='final')
            monthly, remainder = divmod(annual, len(periods))
            pieces = [monthly] * (len(periods) - 1) + [monthly + remainder]
            for period, amount in zip(periods, pieces):
                add('budget_lines', budget_id=budget['id'], account_id=accounts['operating_expense']['id'], period_id=period['id'], amount=money(amount), category='operating', department_id=department['id'], class_id=3, location_id=1, notes='Monthly allocation')
                add('forecast_lines', forecast_id=forecast['id'], account_id=accounts['operating_expense']['id'], period_id=period['id'], amount=money(round(amount * 1.03)), confidence_level='medium', driver='headcount_and_inflation', growth_rate=3, notes='Rolling forecast')
            trace('plan_to_consolidate', flow, f"budget:{department['id']}", start, 'budgets', budget['id'], annual)
        for period in periods:
            consolidation = add('consolidation_processes', consolidation_id=f'CONS-{period["id"]:04d}', name=f'{period["period_name"]} Consolidation', period_id=period['id'], parent_subsidiary_id=1, consolidation_type='standard', exchange_rate_date=period['end_date'], exchange_rate_type='period_end', completed_date=period['end_date'], posted_date=period['end_date'], created_by_id=1, approved_by_id=1, status='complete')
            add('consolidation_subsidiaries', consolidation_id=consolidation['id'], subsidiary_id=1, ownership_percent=100, consolidation_method='full', minority_interest_percent=0)
            for account in accounts.values():
                add('consolidation_translations', consolidation_id=consolidation['id'], subsidiary_id=1, account_id=account['id'], account_type=account['type'], local_currency='USD', local_amount=account.get('current_balance', 0), exchange_rate=1, rate_type='identity', translated_amount=account.get('current_balance', 0), target_currency='USD')
        for index, (name, risk) in enumerate((('Journal approval', 'financial_reporting'), ('Vendor master review', 'procurement'), ('Revenue schedule review', 'revenue'), ('Bank reconciliation', 'cash')), 1):
            control = add('compliance_controls', control_id=f'CTRL-{index:03d}', name=name, description=governance_flow.get('name'), control_type='preventive' if index < 3 else 'detective', risk_area=risk, process=risk, owner_id=1, frequency='monthly', automation_level='semi_automated', evidence_type='system_report', last_test_date=end.isoformat(), test_result='effective', status='active')
            add('compliance_tests', test_id=f'TEST-{index:03d}', control_id=control['id'], test_date=end.isoformat(), tester_id=1, test_procedure=f'Inspect {name.lower()}', sample_size=25, exceptions_found=0, conclusion='Operating effectively', evidence='Generated transaction sample', status='complete')
        trace('plan_to_consolidate', governance_flow, 'controls:annual', end, 'compliance_controls', 1)
    def treasury_to_close():
        flow = workflow('treasury_to_close', 0)
        for report_type, report_name in (('income_statement', 'Income Statement'), ('balance_sheet', 'Balance Sheet'), ('cash_flow', 'Statement of Cash Flows')):
            report = add('financial_reports', report_id=report_type.upper(), name=report_name, description=flow.get('name'), report_type=report_type, layout='standard', columns='["current","prior"]', rows='["account","amount"]', filters='{"subsidiary_id":1}', is_public=1, owner_id=1, subsidiary_id=1, accounting_book_id=1, date_range_type='period')
            for period in periods:
                add('report_snapshots', report_id=report['id'], snapshot_date=period['end_date'], as_of_date=period['end_date'], period_id=period['id'], created_by_id=1, data=json.dumps({'report': report_type, 'period': period['period_name'], 'status': 'final'}), notes='Generated from the event ledger')
            trace('treasury_to_close', flow, f'report:{report_type}', end, 'financial_reports', report['id'])
        bank['current_balance'] = bank['available_balance'] = money(opening_cash + cash_change)
        for period in periods:
            add('reconciliations', bank_account_id=bank['id'], statement_date=period['end_date'], statement_ending_balance=bank['current_balance'], cleared_balance=bank['current_balance'], uncleared_balance=0, difference=0, reconciled_by_id=1, reconciled_date=period['end_date'], status='reconciled')
        for event in events:
            add('audit_trail_entries', entry_id=f"AUD-{ids['audit_trail_entries'] + 1:08d}", timestamp=f"{event['date']} 17:00:00", user_id=(event['source_id'] - 1) % len(registry['employees']) + 1, action='create', record_type=event['source_table'], record_id=event['source_id'], old_value='{}', new_value=json.dumps(event, sort_keys=True), field_name='record', ip_address=f"10.0.{event['source_id'] % 255}.{event['source_id'] * 7 % 255}", session_id=f"session-{event['source_id']:08d}", subsidiary_id=1)
    def finalize():
        _write_generated_records(state_dir, schema_path, rows, events, workflow_instances, step_executions)
        _json(state_dir / 'volume_plan.json', {'target_size_gb': world_parameters.target_size_gb, 'strategy': 'full-record audit snapshots'})
        marker = state_dir / 'records.complete'
        marker.write_text('complete\n')
    _setup_master(setup_master)
    _lead_to_cash(lead_to_cash)
    _procure_to_pay(procure_to_pay)
    _subscription_to_revenue(subscription_to_revenue)
    _expense_to_asset(expense_to_asset)
    _treasury_to_close(treasury_to_close)
    _plan_to_consolidate(plan_to_consolidate)
    finalize()

CHECKS = []

def verifier(tag):

    def register(fn):
        CHECKS.append((tag, fn.__name__, fn))
        return fn
    return register

def violations(db, sql, parameters=()):
    return [row[0] for row in db.execute(sql, parameters).fetchall()]

@verifier('knowledge_base')
def knowledge_base_entities(db, knowledge_base):
    kb = json.loads(Path(knowledge_base).read_text())
    missing = []
    if not db.execute('SELECT 1 FROM subsidiaries WHERE name = ?', (kb['company']['name'],)).fetchone():
        missing.append(f"company:{kb['company']['name']}")
    item_names = {row[0] for row in db.execute('SELECT name FROM items')}
    missing += [f"offering:{x['name']}" for x in kb['offerings'] if x['name'] not in item_names]
    return missing

@verifier('rules')
def foreign_keys(db, _):
    return [f'{table}:{rowid}->{parent}' for table, rowid, parent, _ in db.execute('PRAGMA foreign_key_check')]

@verifier('rules')
def balanced_journals(db, _):
    return violations(db, '\n        SELECT je.id FROM journal_entries je JOIN journal_entry_lines jl\n          ON jl.journal_entry_id = je.id GROUP BY je.id\n        HAVING ABS(SUM(jl.debit) - SUM(jl.credit)) > .005\n    ')

@verifier('rules')
def document_totals(db, _):
    bad = violations(db, '\n        SELECT i.id FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id\n        GROUP BY i.id HAVING ABS(SUM(l.amount)-i.subtotal)>.005\n          OR ABS(i.subtotal+i.tax_total+i.shipping_cost-i.total)>.005\n    ')
    return [f'invoice:{x}' for x in bad] + [f'bill:{x}' for x in violations(db, '\n        SELECT b.id FROM bills b JOIN bill_lines l ON l.bill_id=b.id\n        GROUP BY b.id HAVING ABS(SUM(l.amount)-b.subtotal)>.005\n          OR ABS(b.subtotal+b.tax_total-b.total)>.005\n    ')]

@verifier('rules')
def payment_limits(db, _):
    bad = violations(db, '\n        SELECT p.id FROM payments p LEFT JOIN payment_applications a ON a.payment_id=p.id\n        GROUP BY p.id HAVING COALESCE(SUM(a.amount),0)-p.amount>.005\n    ')
    bad += violations(db, '\n        SELECT i.id FROM invoices i\n        WHERE i.amount_paid-i.total>.005 OR ABS(i.amount_paid+i.amount_due-i.total)>.005\n    ')
    return bad

@verifier('rules')
def causal_dates(db, _):
    return violations(db, '\n        SELECT i.id FROM invoices i JOIN sales_orders s ON s.id=i.sales_order_id\n        WHERE i.date<s.date\n        UNION ALL\n        SELECT p.id FROM payments p JOIN payment_applications a ON a.payment_id=p.id\n          JOIN invoices i ON i.id=a.invoice_id WHERE p.date<i.date\n    ')

@verifier('rules')
def dimensions_and_periods(db, _):
    return violations(db, '\n        SELECT i.id FROM invoices i JOIN customers c ON c.id=i.customer_id\n        LEFT JOIN accounting_periods ap ON ap.id=i.posting_period_id\n        WHERE i.subsidiary_id<>c.subsidiary_id\n           OR (ap.id IS NOT NULL AND (i.date<ap.start_date OR i.date>ap.end_date))\n        UNION ALL\n        SELECT p.id FROM payments p JOIN customers c ON c.id=p.customer_id\n        WHERE p.subsidiary_id<>c.subsidiary_id OR p.currency<>c.currency\n    ')

@verifier('rules')
def quantity_reconciliation(db, _):
    return [f'sales:{x}' for x in violations(db, '\n        SELECT id FROM sales_order_lines\n        WHERE quantity_fulfilled-quantity>.0001 OR quantity_billed-quantity_fulfilled>.0001\n    ')] + [f'purchase:{x}' for x in violations(db, '\n        SELECT id FROM purchase_order_lines\n        WHERE quantity_received-quantity>.0001 OR quantity_billed-quantity_received>.0001\n    ')]

@verifier('rules')
def revenue_plan_totals(db, _):
    return violations(db, '\n        SELECT p.id FROM revenue_plans p JOIN revenue_plan_lines l ON l.plan_id=p.id\n        GROUP BY p.id HAVING ABS(SUM(l.amount)-p.total_amount)>.005\n    ')

@verifier('workflows')
def workflow_provenance(db, _):
    missing = violations(db, '''
        SELECT wi.id FROM workflow_instances wi
        LEFT JOIN workflow_step_executions ws ON ws.workflow_instance_id=wi.id
        GROUP BY wi.id HAVING COUNT(ws.id)=0
    ''')
    unknown = violations(db, '''
        SELECT id FROM workflow_instances
        WHERE workflow NOT IN (
          'lead_to_cash','procure_to_pay','subscription_to_revenue',
          'expense_to_asset','treasury_to_close','plan_to_consolidate'
        )
    ''')
    return [f'no_steps:{value}' for value in missing] + [f'unknown:{value}' for value in unknown]

@verifier('rules')
def receivables_reconciliation(db, _):
    return violations(db, '''
        SELECT i.id FROM invoices i
        LEFT JOIN payment_applications p ON p.invoice_id=i.id
        GROUP BY i.id
        HAVING ABS(COALESCE(SUM(p.amount),0)-i.amount_paid)>.005
           OR ABS(i.total-i.amount_paid-i.amount_due)>.005
    ''')

@verifier('rules')
def payables_reconciliation(db, _):
    return violations(db, '''
        SELECT b.id FROM bills b
        LEFT JOIN bill_payment_applications p ON p.bill_id=b.id
        GROUP BY b.id
        HAVING ABS(COALESCE(SUM(p.amount),0)-b.amount_paid)>.005
           OR ABS(b.total-b.amount_paid-b.amount_due)>.005
    ''')

@verifier('rules')
def cash_reconciliation(db, _):
    return violations(db, '''
        SELECT d.id FROM deposits d
        LEFT JOIN payments p ON p.deposit_id=d.id
        GROUP BY d.id HAVING ABS(COALESCE(SUM(p.amount),0)-d.total)>.005
    ''')

@verifier('workflows')
def workflow_effects(db, _):
    return violations(db, '''
        SELECT wi.id FROM workflow_instances wi
        LEFT JOIN workflow_step_executions ws
          ON ws.workflow_instance_id=wi.id AND ws.affected_table IS NOT NULL
        GROUP BY wi.id HAVING COUNT(ws.id)=0
    ''')

@verifier('workflows')
def workflow_dates(db, _):
    return violations(db, '''
        SELECT current.id FROM workflow_step_executions current
        JOIN workflow_step_executions prior
          ON prior.workflow_instance_id=current.workflow_instance_id
         AND prior.sequence=current.sequence-1
        WHERE current.date < prior.date
    ''')

@verifier('workflows')
def repeated_payments_are_distinct(db, _):
    return violations(db, '''
        SELECT workflow_instance_id
        FROM workflow_step_executions
        WHERE step='receive_payment'
        GROUP BY workflow_instance_id
        HAVING COUNT(*)>1
           AND COUNT(DISTINCT record_id)<COUNT(*)
    ''')

def run_all(database, knowledge_base):
    with sqlite3.connect(database) as db:
        results = []
        for tag, name, check in CHECKS:
            try:
                bad = check(db, knowledge_base)
                results.append({'tag': tag, 'name': name, 'passed': not bad, 'violations': bad[:20]})
            except Exception as error:
                results.append({'tag': tag, 'name': name, 'passed': False, 'violations': [str(error)]})
    return results

def _foreign_key_order(db, tables):
    dependencies = {
        table: {row[2] for row in db.execute(f'PRAGMA foreign_key_list("{table}")')
                if row[2] in tables}
        for table in tables
    }
    pending, order = set(tables), []
    while pending:
        ready = sorted(table for table in pending if not dependencies[table] & pending)
        ready = ready or sorted(pending)
        order.extend(ready)
        pending.difference_update(ready)
    return order

def _compile(output_path, state_dir, schema_path, world_parameters):
    destination, temporary = (output_path / 'output.sqlite', output_path / 'output.tmp.sqlite')
    temporary.unlink(missing_ok=True)
    schema = schema_path.read_text()
    marker = 'CREATE TABLE sqlite_stat1(tbl,idx,stat);'
    table_sql, index_sql = schema.split(marker, 1)
    db = sqlite3.connect(temporary)
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('PRAGMA foreign_keys=OFF')
    db.executescript(table_sql)
    db.executescript('''
        CREATE TABLE workflow_instances (
            id INTEGER PRIMARY KEY,
            instance_key TEXT NOT NULL UNIQUE,
            domain TEXT NOT NULL,
            workflow TEXT NOT NULL,
            variant TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            cadence TEXT,
            exception TEXT,
            status TEXT NOT NULL,
            date TEXT NOT NULL,
            source_table TEXT,
            source_id INTEGER,
            amount_cents INTEGER DEFAULT 0,
            upstream_instance_id INTEGER,
            FOREIGN KEY (upstream_instance_id) REFERENCES workflow_instances(id)
        );
        CREATE TABLE workflow_step_executions (
            id INTEGER PRIMARY KEY,
            workflow_instance_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            step TEXT NOT NULL,
            status TEXT NOT NULL,
            date TEXT NOT NULL,
            affected_table TEXT,
            record_id INTEGER,
            operation TEXT NOT NULL DEFAULT 'observe',
            parameters TEXT NOT NULL DEFAULT '{}',
            before_summary TEXT,
            after_summary TEXT,
            FOREIGN KEY (workflow_instance_id) REFERENCES workflow_instances(id)
        );
    ''')
    files = {path.stem: path for path in (state_dir / 'tables').glob('*.jsonl')}
    for table in _foreign_key_order(db, files):
        with files[table].open() as source:
            first = source.readline()
            if not first:
                continue
            row = json.loads(first)
            columns = list(row)
            quoted_columns = ','.join(f'"{column}"' for column in columns)
            sql = f'''INSERT INTO "{table}" ({quoted_columns}) VALUES ({','.join('?' for _ in columns)})'''
            batch = [[row[column] for column in columns]]
            for line in source:
                row = json.loads(line)
                batch.append([row[column] for column in columns])
                if len(batch) == 2000:
                    db.executemany(sql, batch)
                    batch.clear()
            if batch:
                db.executemany(sql, batch)
    for name in ('workflow_instances', 'workflow_step_executions'):
        path = state_dir / f'{name}.jsonl'
        if not path.exists():
            continue
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
        if not records:
            continue
        columns = list(records[0])
        quoted_columns = ','.join(f'"{column}"' for column in columns)
        placeholders = ','.join('?' for _ in columns)
        db.executemany(
            f'INSERT INTO "{name}" ({quoted_columns}) VALUES ({placeholders})',
            [[record.get(column) for column in columns] for record in records],
        )
    db.commit()
    target = int(world_parameters.target_size_gb * 1024 ** 3)
    audit_id = db.execute('SELECT COALESCE(MAX(id), 0) FROM audit_trail_entries').fetchone()[0]
    snapshot = json.dumps({'record_snapshot': 'Operational context, approvals, line details, and source metadata. ' * 65, 'generator': 'Bonsai deterministic audit history'}, separators=(',', ':'))
    while db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] < target:
        size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
        count = min(5000, max(1, (target - size) // (len(snapshot) * 2 + 400)))
        values = []
        for _ in range(count):
            audit_id += 1
            event_date = world_parameters.start_date + timedelta(days=(audit_id - 1) % world_parameters.scenario_duration_days)
            values.append((audit_id, f'AUD-{audit_id:08d}', f'{event_date} 17:00:00', (audit_id - 1) % world_parameters.num_employees + 1, 'update', ('invoice', 'bill', 'subscription', 'journal_entry')[audit_id % 4], audit_id % max(1, world_parameters.num_sales_orders), snapshot, snapshot[:-2] + f',"revision":{audit_id}}}', 'record_snapshot', f'10.0.{audit_id % 255}.{audit_id * 7 % 255}', f'session-{audit_id:08d}', 1))
        db.executemany('\n            INSERT INTO audit_trail_entries\n            (id,entry_id,timestamp,user_id,action,record_type,record_id,old_value,new_value,\n             field_name,ip_address,session_id,subsidiary_id)\n            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)\n        ', values)
        db.commit()
    db.executescript(index_sql)
    db.commit()
    db.execute('PRAGMA foreign_keys=ON')
    problems = db.execute('PRAGMA foreign_key_check').fetchmany(20)
    integrity = db.execute('PRAGMA quick_check').fetchone()[0]
    if problems or integrity != 'ok':
        db.close()
        raise RuntimeError(f'database integrity failed: foreign_keys={problems}, quick_check={integrity}')
    table_names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    row_counts = {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in table_names}
    db.close()
    temporary.replace(destination)
    return row_counts

def _run_verifiers(output_path, state_dir):
    results = run_all(output_path / 'output.sqlite', state_dir / 'knowledge_base.json')
    _json(output_path / 'verifier_results.json', results)
    return results

def generate(output_path: str='results/', world_parameters: WorldParameters | None=None, schema_path: str | Path=SCHEMA_PATH):
    world_parameters = world_parameters or WorldParameters()
    load_dotenv(ROOT / '.env')
    output_path, schema_path = (Path(output_path), Path(schema_path))
    output_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True, handlers=[logging.FileHandler(output_path / 'pipeline.log', mode='a'), logging.StreamHandler()])
    logger, timings, pipeline_started = (logging.getLogger('bonsai'), {}, time.perf_counter())
    _validate(world_parameters)
    run_hash, state_dir = (_fingerprint(world_parameters, schema_path), output_path / 'intermediate_states')
    saved_hash = (state_dir / 'run_hash.txt').read_text().strip() if (state_dir / 'run_hash.txt').exists() else ''
    if saved_hash != run_hash:
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True)
        for name in ('output.sqlite', 'output.tmp.sqlite', 'verifier_results.json', 'statistics.json'):
            (output_path / name).unlink(missing_ok=True)
        (state_dir / 'run_hash.txt').write_text(run_hash + '\n')
    if not (state_dir / 'knowledge_base.json').exists() or not (state_dir / 'entity_registry.json').exists():
        started = time.perf_counter()
        _extract_knowledge_base(world_parameters, state_dir, schema_path)
        timings['extract_knowledge_base_seconds'] = round(time.perf_counter() - started, 3)
    else:
        timings['extract_knowledge_base_seconds'] = 0
    if not (state_dir / 'records.complete').exists():
        started = time.perf_counter()
        _generate_records(world_parameters, state_dir, schema_path)
        timings['generate_records_seconds'] = round(time.perf_counter() - started, 3)
    else:
        timings['generate_records_seconds'] = 0
    row_counts = None
    if not (output_path / 'output.sqlite').exists():
        started = time.perf_counter()
        row_counts = _compile(output_path, state_dir, schema_path, world_parameters)
        timings['compile_seconds'] = round(time.perf_counter() - started, 3)
    else:
        timings['compile_seconds'] = 0
    started = time.perf_counter()
    verifier_results = _run_verifiers(output_path, state_dir)
    timings['verify_seconds'] = round(time.perf_counter() - started, 3)
    timings['total_seconds'] = round(time.perf_counter() - pipeline_started, 3)
    if row_counts is None:
        with sqlite3.connect(output_path / 'output.sqlite') as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            row_counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    prior_statistics = _json(output_path / 'statistics.json') if (output_path / 'statistics.json').exists() else {}
    generation_timings = prior_statistics.get('generation_timings', {})
    generation_timings.update({key: value for key, value in timings.items() if value and key != 'total_seconds'})
    generation_timings['total_seconds'] = round(sum((value for key, value in generation_timings.items() if key != 'total_seconds')), 3)
    statistics = {'run_hash': run_hash, 'parameters': asdict(world_parameters), 'model': world_parameters.model, 'timings': timings, 'generation_timings': generation_timings, 'row_counts': row_counts, 'database_size_bytes': (output_path / 'output.sqlite').stat().st_size, 'verifiers': verifier_results}
    _json(output_path / 'statistics.json', statistics)
    failed = [result['name'] for result in verifier_results if not result['passed']]
    logger.info('Generated %s bytes with %s rows', statistics['database_size_bytes'], sum(row_counts.values()))
    if failed:
        logger.error('Verifier failures: %s', ', '.join(failed))
        raise RuntimeError(f"verifiers failed: {', '.join(failed)}")

def _parse_args():
    defaults = WorldParameters()
    parser = argparse.ArgumentParser(description='Generate a Bonsai SQLite world')
    parser.add_argument('--output_path', default='results/')
    parser.add_argument('--schema_path', default=str(SCHEMA_PATH))
    for field in fields(defaults):
        value = getattr(defaults, field.name)
        parser.add_argument(f'--{field.name}', default=value, type=date.fromisoformat if isinstance(value, date) else type(value), help=f'WorldParameters.{field.name} (default: {value})')
    values = vars(parser.parse_args())
    output_path, schema_path = (values.pop('output_path'), values.pop('schema_path'))
    return (output_path, schema_path, WorldParameters(**values))
if __name__ == '__main__':
    output_path, schema_path, world_parameters = _parse_args()
    generate(output_path=output_path, world_parameters=world_parameters, schema_path=schema_path)
