# Take a sqlite file and generate emails inspired by the Corporate Bench approach https://arxiv.org/html/2608.27391v1
#

# CLI args for taking in sqlite database path, model, outputpath
# (by default just put it in the same results folder)

import argparse
import asyncio
import json
import random
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default as email_policy
from email.utils import format_datetime, formataddr
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import AsyncOpenAI

ROOT = Path(__file__).parent
JINJA = Environment(
    loader=FileSystemLoader(ROOT / "prompt_templates"),
    undefined=StrictUndefined,
    autoescape=False,
)

# GENERATE EMAIL
# Create evidence string mappings templates for certain actions in transactions
# PUT THIS IN A JINJA FILE IN PROMPT_TEMPLATES CALLED email_thread_generation.jinja2
eml_gen_template_prompt = """Generate an email thread containing the following characteristics for the workflow:

Thread plan: {thread_plan}

Output as a json:
{{"emails": [{{"sender": "", "subject": "awefawef", "text": "hi"}}], ..., ]}}
"""

TOPICS = {
    # List of work topics, ranging from None (straight to the point, 0.5 of the time)
    # to 0.25 water cooler topics to 0.25 work related topic about deadlines /
    # timelines, etc.. so this should be a dictionary with 3 lists with keys called
    # "None", "water_cooler", "work_topic". the create_thread_workloads fn should
    # sample from this with the weights i specified.
    "None": [None],
    "water_cooler": [
        "weekend plans", "the weather", "coffee in the break room",
        "a local sports result", "an upcoming holiday",
    ],
    "work_topic": [
        "month-end timing", "an approval deadline", "quarterly planning",
        "a customer follow-up", "the team backlog",
    ],
}
TONE = ["concise", "friendly", "formal", "warm", "direct", "cautious", "upbeat"]
MBTI = [
    "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ",
]

# 10 different variations of signature templates
SIGNATURE_TEMPLATES = [
    "{name}", "{name}\n{title}", "Best,\n{name}", "Thanks,\n{name}",
    "{name}\n{title}\n{phone}", "{name} | {title}", "Regards,\n{name}\n{company}",
    "{name}\n{company}\n{email}\n{website}",
    "{name}\n{title}\n{company}\n{phone}\n{website}",
    "{name}\n“{quote}”\n{website}",
]
QUOTES = [
    "Make it simple, but significant.", "Progress is built one step at a time.",
    "Clarity creates confidence.", "Good work compounds.",
    "Details make the difference.", "Plan the work, then work the plan.",
    "Consistency earns trust.", "Small improvements add up.",
    "Focus on what moves the work forward.", "Measure twice, communicate once.",
]
RUN_CONTEXT = {}


def _connect(results_folder_path):
    database = Path(results_folder_path).expanduser().resolve() / "output.sqlite"
    if not database.is_file():
        raise FileNotFoundError(f"Missing {database}")
    return sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)


def _json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _topic(rng):
    category = rng.choices(
        ["None", "water_cooler", "work_topic"], [0.5, 0.25, 0.25]
    )[0]
    return category, rng.choice(TOPICS[category])


def _directory(results_folder_path):
    path = Path(results_folder_path).expanduser().resolve() / "email_gen"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _people(db):
    people = {}
    company_row = db.execute(
        "SELECT name FROM subsidiaries ORDER BY id LIMIT 1"
    ).fetchone()
    company_name = company_row[0] if company_row else "Company"
    for row in db.execute(
        """SELECT e.id, e.first_name, e.last_name, e.email, e.phone, e.title,
                  COALESCE(d.name, 'Northstar Office Systems')
           FROM employees e LEFT JOIN departments d ON d.id=e.department_id
           WHERE e.email IS NOT NULL"""
    ):
        people[row[3]] = {
            "entity_type": "employee", "entity_id": row[0],
            "name": f"{row[1]} {row[2]}", "email": row[3],
            "phone": row[4] or "", "title": row[5] or "", "department": row[6],
            "company": company_name,
        }
    for table, kind in (("customers", "customer"), ("vendors", "vendor")):
        for row in db.execute(
            f"SELECT id, company_name, email, phone FROM {table} WHERE email IS NOT NULL"
        ):
            people[row[2]] = {
                "entity_type": kind, "entity_id": row[0], "name": row[1],
                "email": row[2], "phone": row[3] or "", "title": kind.title(),
                "department": "", "company": row[1],
            }
    return people


def _participants(db, source_table, source_id, people, index):
    employees = [
        person for person in people.values() if person["entity_type"] == "employee"
    ]
    external = None
    if source_table in {"sales_orders", "estimates", "invoices"}:
        row = db.execute(
            f"SELECT customer_id FROM {source_table} WHERE id=?", (source_id,)
        ).fetchone()
        customer_id = row[0] if row else None
        external = next(
            (person for person in people.values()
             if person["entity_type"] == "customer"
             and person["entity_id"] == customer_id),
            None,
        )
    elif source_table in {"purchase_orders", "bills", "bill_payments"}:
        row = db.execute(
            f"SELECT vendor_id FROM {source_table} WHERE id=?", (source_id,)
        ).fetchone()
        vendor_id = row[0] if row else None
        external = next(
            (person for person in people.values()
             if person["entity_type"] == "vendor"
             and person["entity_id"] == vendor_id),
            None,
        )
    first = employees[index % len(employees)]
    second = external or employees[(index * 7 + 1) % len(employees)]
    if first["email"] == second["email"]:
        second = employees[(index + 1) % len(employees)]
    return [first, second]


# CREATE THREAD WORKLOADS
def create_thread_workload(results_folder_path, thread_limit=None, seed=42):
    # create a thread plan
    # thread plan is a list of plans for each email. it contains an evidence
    # string, topic (draw from TOPICS), tone (draw from TONE), mbti (draw from MBTI)
    rng = random.Random(seed)
    with _connect(results_folder_path) as db:
        people = _people(db)
        rows = db.execute(
            """SELECT id, instance_key, domain, workflow, variant, description,
                      exception, status, date, source_table, source_id, amount_cents
               FROM workflow_instances
               WHERE source_table IS NOT NULL AND source_id IS NOT NULL
               ORDER BY id LIMIT ?""",
            (int(thread_limit) if thread_limit else -1,),
        ).fetchall()
        workloads = []
        for index, row in enumerate(rows):
            steps = [
                dict(zip(
                    ("sequence", "step", "status", "date", "affected_table",
                     "record_id", "operation", "before", "after"),
                    step,
                ))
                for step in db.execute(
                    """SELECT sequence, step, status, date, affected_table, record_id,
                              operation, before_summary, after_summary
                       FROM workflow_step_executions
                       WHERE workflow_instance_id=? ORDER BY sequence""",
                    (row[0],),
                )
            ]
            participants = _participants(db, row[9], row[10], people, index)
            category, topic = _topic(rng)
            workloads.append({
                "thread_id": f"thread-{row[0]:08d}",
                "workflow_instance_id": row[0], "instance_key": row[1],
                "domain": row[2], "workflow": row[3], "variant": row[4],
                "description": row[5], "exception": row[6], "status": row[7],
                "date": row[8], "source_table": row[9], "source_id": row[10],
                "amount_cents": row[11], "steps": steps,
                "participants": participants, "topic_category": category,
                "topic": topic, "tone": rng.choice(TONE), "mbti": rng.choice(MBTI),
                "message_count": rng.choices([1, 2, 3, 4], [0.12, 0.48, 0.3, 0.1])[0],
                "evidence": (
                    f"{row[3]} / {row[4]} for {row[9]} #{row[10]}; "
                    f"status {row[7]}; steps: "
                    + ", ".join(
                        f"{step['date']} {step['step']} ({step['status']})"
                        for step in steps
                    )
                ),
            })
    return workloads


def _parse_json(text):
    value = (
        text.strip().removeprefix("```json").removeprefix("```")
        .removesuffix("```").strip()
    )
    return json.loads(value[value.index("{"):value.rindex("}") + 1])


def _usage(response):
    usage = getattr(response, "usage", None)
    if not usage:
        return {}
    result = {
        name: int(getattr(usage, name, 0) or 0)
        for name in ("input_tokens", "output_tokens", "total_tokens")
    }
    result["cached_input_tokens"] = int(
        getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    )
    result["reasoning_tokens"] = int(
        getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0)
        or 0
    )
    return result


def _validate_thread(value, plan):
    emails = value.get("emails") if isinstance(value, dict) else None
    if not isinstance(emails, list) or not emails:
        raise ValueError("response must contain a non-empty emails list")
    if len(emails) > 6:
        raise ValueError("thread exceeds six messages")
    allowed = {person["email"] for person in plan["participants"]}
    normalized = []
    for index, email in enumerate(emails, 1):
        recipients = email.get("to")
        if isinstance(recipients, str):
            recipients = [recipients]
        if (
            email.get("sender") not in allowed
            or not recipients
            or any(item not in allowed for item in recipients)
        ):
            raise ValueError("message uses a participant absent from the world")
        if not email.get("subject") or not email.get("text"):
            raise ValueError("message requires subject and text")
        normalized.append({
            "sequence": index, "sender": email["sender"], "to": recipients,
            "cc": email.get("cc") or [], "subject": str(email["subject"])[:240],
            "text": str(email["text"]).strip(),
            "date": email.get("date") or plan["date"],
        })
    return normalized


async def _generate_one(client, semaphore, model, plan, retries=3):
    prompt = JINJA.get_template("email_thread_generation.jinja2").render(
        thread_plan=plan
    )
    async with semaphore:
        for attempt in range(retries):
            try:
                response = await client.responses.create(
                    model=model, input=prompt, max_output_tokens=3000,
                    text={"format": {"type": "json_object"}},
                )
                return (
                    _validate_thread(_parse_json(response.output_text), plan),
                    _usage(response),
                )
            except Exception:
                if attempt + 1 == retries:
                    raise
                await asyncio.sleep(2 ** attempt)


async def _generate_all(workload, model, concurrency):
    client = AsyncOpenAI()
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [_generate_one(client, semaphore, model, plan) for plan in workload]
    results = [None] * len(tasks)
    usage = {
        "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        "reasoning_tokens": 0, "total_tokens": 0,
    }
    async def indexed(index, task):
        return index, await task

    indexed_tasks = [
        asyncio.create_task(indexed(index, task))
        for index, task in enumerate(tasks)
    ]
    for completed, task in enumerate(asyncio.as_completed(indexed_tasks), 1):
        index, (messages, item_usage) = await task
        results[index] = (messages, item_usage)
        for key in usage:
            usage[key] += item_usage.get(key, 0)
        print(f"\rGenerated {completed}/{len(tasks)} threads", end="", flush=True)
    print()
    return results, usage


def generate_emails(workload, model=None, concurrency=10):
    # concurrently in large batches (50? 100?) and generate using openai 5.6 luna
    # show tmux progress
    # outputs a list of email json objects
    if not model:
        raise ValueError("A generation model is required")
    generated, usage = asyncio.run(_generate_all(workload, model, concurrency))
    RUN_CONTEXT["usage"] = usage
    return [
        {"plan": plan, "emails": messages}
        for plan, (messages, _) in zip(workload, generated)
    ]


def create_personel_email_characteristics(results_folder_path, seed=42):
    # create a json file with a key to the source email address and a value to the template they use
    # draw from SIGNATURE_TEMPLATES (some are just the name, some have the name and a rendered signature, some have name phone number, some have quotes (draw from a quote bank of like 100 possible motivational quotes))
    # now each person wi
    # this outputs a signature json_path
    output = _directory(results_folder_path) / "personnel_email_characteristics.json"
    with _connect(results_folder_path) as db:
        people = _people(db)
    characteristics = {}
    for email, person in sorted(people.items()):
        rng = random.Random(f"{seed}:{email}")
        characteristics[email] = {
            **person, "tone": rng.choice(TONE), "mbti": rng.choice(MBTI),
            "signature_template": rng.randrange(len(SIGNATURE_TEMPLATES)),
            "quote": rng.choice(QUOTES),
            "website": (
                "https://"
                + "-".join(
                    part.lower()
                    for part in person["name"].replace("'", "").split()
                    if part
                )
                + ".com"
            ),
        }
    _json_write(output, characteristics)
    return output


def _message_date(value, sequence):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.replace(hour=9 + min(sequence, 7), minute=sequence * 7 % 60)
        + timedelta(days=sequence - 1)
    )


def _signature(characteristic):
    return SIGNATURE_TEMPLATES[characteristic["signature_template"]].format(
        **characteristic
    )


def render(
    email_json_objects, signature_json_path, results_folder_path=None, model=None,
    started_at=None, elapsed_seconds=None, pricing=None,
):
    # takes in a list of email json objects
    # at certain probability, add the following realism components to the email for each individual message:
    # generate an image (use my openai) for the logo and place it in. save this jpg in the resulting file as well.
    results = Path(results_folder_path or Path(signature_json_path).parent.parent)
    output = _directory(results)
    eml_dir = output / "emails"
    eml_dir.mkdir(parents=True, exist_ok=True)
    expected_paths = set()
    characteristics = json.loads(Path(signature_json_path).read_text())
    flattened = []
    for thread in email_json_objects:
        plan, messages = thread["plan"], thread["emails"]
        references, previous = [], None
        for item in messages:
            characteristic = characteristics[item["sender"]]
            message_id = f"{plan['thread_id']}-{item['sequence']:02d}@bonsai.local"
            body = item["text"].rstrip() + "\n\n" + _signature(characteristic)
            message = EmailMessage(policy=email_policy)
            message["Message-ID"] = f"<{message_id}>"
            message["Date"] = format_datetime(
                _message_date(item["date"], item["sequence"])
            )
            message["From"] = formataddr((characteristic["name"], item["sender"]))
            message["To"] = ", ".join(item["to"])
            if item["cc"]:
                message["Cc"] = ", ".join(item["cc"])
            message["Subject"] = item["subject"]
            if previous:
                message["In-Reply-To"] = f"<{previous}>"
                message["References"] = " ".join(f"<{ref}>" for ref in references)
            message.set_content(body)
            eml_path = eml_dir / f"{message_id.replace('@', '_at_')}.eml"
            eml_path.write_bytes(message.as_bytes())
            expected_paths.add(eml_path)
            flattened.append({
                **item, "message_id": message_id, "thread_id": plan["thread_id"],
                "body": body, "eml_path": str(eml_path.relative_to(output)),
                "sender_name": characteristic["name"],
                "workflow_instance_id": plan["workflow_instance_id"],
                "workflow": plan["workflow"], "variant": plan["variant"],
                "domain": plan["domain"], "exception": plan["exception"],
                "source_table": plan["source_table"], "source_id": plan["source_id"],
                "topic_category": plan["topic_category"], "topic": plan["topic"],
                "tone": plan["tone"], "mbti": plan["mbti"], "steps": plan["steps"],
                "generation_type": "workflow_evidence", "in_reply_to": previous,
            })
            previous = message_id
            references.append(message_id)
    for stale_path in eml_dir.glob("*.eml"):
        if stale_path not in expected_paths:
            stale_path.unlink()
    jsonl = output / "emails.jsonl"
    temporary = jsonl.with_suffix(".jsonl.tmp")
    with temporary.open("w") as target:
        for record in flattened:
            target.write(json.dumps(record, sort_keys=True) + "\n")
    temporary.replace(jsonl)
    archive = output / "emails.zip"
    temporary_archive = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(eml_dir.glob("*.eml")):
            bundle.write(path, path.relative_to(output))
    temporary_archive.replace(archive)
    usage = RUN_CONTEXT.get("usage", {})
    rates = pricing or {}
    billable_input = max(
        0, usage.get("input_tokens", 0) - usage.get("cached_input_tokens", 0)
    )
    cost = (
        billable_input / 1_000_000 * rates.get("input_per_million", 0)
        + usage.get("cached_input_tokens", 0)
        / 1_000_000 * rates.get("cached_input_per_million", 0)
        + usage.get("output_tokens", 0)
        / 1_000_000 * rates.get("output_per_million", 0)
    ) if rates else None
    _json_write(output / "generation_statistics.json", {
        "model": model, "threads": len(email_json_objects),
        "messages": len(flattened), "usage": usage,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "pricing": rates or None, "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_seconds,
    })
    return flattened


def _args():
    parser = argparse.ArgumentParser(
        description="Generate realistic workflow-grounded emails"
    )
    parser.add_argument(
        "results_folder", help="World results folder containing output.sqlite"
    )
    parser.add_argument("--model")
    parser.add_argument("--thread-limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--input-price", type=float)
    parser.add_argument("--cached-input-price", type=float)
    parser.add_argument("--output-price", type=float)
    return parser.parse_args()


def main():
    load_dotenv()
    args = _args()
    results = Path(args.results_folder).expanduser().resolve()
    statistics_path = results / "statistics.json"
    statistics = (
        json.loads(statistics_path.read_text()) if statistics_path.is_file() else {}
    )
    model = args.model or statistics.get("model")
    if model == "terra":
        model = "gpt-5.6-terra"
    if not model:
        raise SystemExit("Pass --model or provide model in statistics.json")
    pricing = (
        {
            "input_per_million": 2.0,
            "cached_input_per_million": 0.2,
            "output_per_million": 12.0,
            "source": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
        }
        if model == "gpt-5.6-terra" else None
    )
    if args.input_price is not None and args.output_price is not None:
        pricing = {
            "input_per_million": args.input_price,
            "cached_input_per_million": args.cached_input_price or 0.0,
            "output_per_million": args.output_price,
            "source": "CLI override",
        }
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    workload = create_thread_workload(results, args.thread_limit, args.seed)
    signatures = create_personel_email_characteristics(results, args.seed)
    generated = generate_emails(workload, model, args.concurrency)
    elapsed = time.perf_counter() - started
    rendered = render(
        generated, signatures, results, model, started_at, elapsed, pricing
    )
    print(
        f"Created {len(generated)} threads and {len(rendered)} messages "
        f"in {elapsed:.2f}s"
    )
    print(f"Artifacts: {results / 'email_gen'}")


if __name__ == "__main__":
    main()
