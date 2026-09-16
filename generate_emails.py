# Take a sqlite file and generate emails inspired by the Corporate Bench approach https://arxiv.org/html/2608.27391v1
#

# CLI args for taking in sqlite database path, model, outputpath
# (by default just put it in the same results folder)

import argparse
import asyncio
import base64
import html
import json
import mailbox
import math
import random
import re
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
from openai import AsyncOpenAI, OpenAI

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
    {"text": "The secret of getting ahead is getting started.", "author": "Mark Twain"},
    {"text": "Well done is better than well said.", "author": "Benjamin Franklin"},
    {"text": "Quality means doing it right when no one is looking.", "author": "Henry Ford"},
    {"text": "The way to get started is to quit talking and begin doing.", "author": "Walt Disney"},
    {"text": "Success is where preparation and opportunity meet.", "author": "Bobby Unser"},
    {"text": "If you can dream it, you can do it.", "author": "Walt Disney"},
    {"text": "It always seems impossible until it’s done.", "author": "Nelson Mandela"},
    {"text": "The only way to do great work is to love what you do.", "author": "Steve Jobs"},
    {"text": "You miss 100% of the shots you don’t take.", "author": "Wayne Gretzky"},
    {"text": "Some people want it to happen, some wish it would happen, others make it happen.", "author": "Michael Jordan"},
]
FONT_VARIANTS = [
    {"name": "georgia", "family": "Georgia, 'Times New Roman', serif"},
    {"name": "arial", "family": "Arial, Helvetica, sans-serif"},
    {"name": "verdana", "family": "Verdana, Geneva, sans-serif"},
    {"name": "trebuchet", "family": "'Trebuchet MS', Arial, sans-serif"},
    {"name": "times", "family": "'Times New Roman', Times, serif"},
]
FONT_SIZES = [12, 13, 14, 15, 16]
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


def _communication_metadata(participants, reverse):
    sender, recipient = (
        (participants[1], participants[0]) if reverse else participants
    )
    direction = f"{sender['entity_type']}_to_{recipient['entity_type']}"
    internal = sender if sender["entity_type"] == "employee" else recipient
    relationship = (
        "internal" if sender["entity_type"] == recipient["entity_type"] == "employee"
        else f"external_{recipient['entity_type'] if sender['entity_type'] == 'employee' else sender['entity_type']}"
    )
    return {
        "communication_direction": direction,
        "sender_entity_type": sender["entity_type"],
        "recipient_entity_types": [recipient["entity_type"]],
        "sender_role": sender.get("title", ""),
        "sender_department": sender.get("department", ""),
        "relationship": relationship,
        "sender_group": f"{direction}:{internal.get('department') or internal.get('title') or 'external'}",
    }


def _stratified_rows(db, people, limit, seed):
    rows = db.execute(
        """SELECT id, instance_key, domain, workflow, variant, description,
                  exception, status, date, source_table, source_id, amount_cents
           FROM workflow_instances
           WHERE source_table IS NOT NULL AND source_id IS NOT NULL"""
    ).fetchall()
    rng = random.Random(seed)
    strata = {}
    for row in rows:
        participants = _participants(db, row[9], row[10], people, row[0] - 1)
        reverse = bool(rng.getrandbits(1)) if participants[1]["entity_type"] != "employee" else False
        communication = _communication_metadata(participants, reverse)
        month = int(row[8][5:7])
        quarter = f"{row[8][:4]}-Q{(month - 1) // 3 + 1}"
        key = (row[3], row[4], quarter, communication["sender_group"])
        strata.setdefault(key, []).append((row, participants, communication, quarter))
    for values in strata.values():
        rng.shuffle(values)
    quota = min(limit or len(rows), len(rows))
    selected = []
    # Guarantee one representative from every populated stratum when possible.
    keys = list(strata)
    rng.shuffle(keys)
    keys.sort(key=lambda key: len(strata[key]))
    if len(keys) > quota:
        by_workflow = {}
        for key in keys:
            by_workflow.setdefault(key[0], []).append(key)
        keys = []
        while len(keys) < quota and any(by_workflow.values()):
            for workflow in sorted(by_workflow):
                if by_workflow[workflow] and len(keys) < quota:
                    keys.append(by_workflow[workflow].pop(0))
    for key in keys[:quota]:
        selected.append(strata[key].pop())
    remaining = quota - len(selected)
    # Square-root allocation prevents the largest workflows from dominating.
    while remaining:
        available = [key for key, values in strata.items() if values]
        if not available:
            break
        weights = [math.sqrt(len(strata[key])) for key in available]
        key = rng.choices(available, weights=weights, k=1)[0]
        selected.append(strata[key].pop())
        remaining -= 1
    rng.shuffle(selected)
    return selected


# CREATE THREAD WORKLOADS
def create_thread_workload(results_folder_path, thread_limit=None, seed=42):
    # create a thread plan
    # thread plan is a list of plans for each email. it contains an evidence
    # string, topic (draw from TOPICS), tone (draw from TONE), mbti (draw from MBTI)
    rng = random.Random(seed)
    with _connect(results_folder_path) as db:
        people = _people(db)
        selected = _stratified_rows(db, people, thread_limit, seed)
        workloads = []
        for index, (row, participants, communication, quarter) in enumerate(selected):
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
            category, topic = _topic(rng)
            workloads.append({
                "thread_id": f"thread-{row[0]:08d}",
                "workflow_instance_id": row[0], "instance_key": row[1],
                "domain": row[2], "workflow": row[3], "variant": row[4],
                "description": row[5], "exception": row[6], "status": row[7],
                "date": row[8], "source_table": row[9], "source_id": row[10],
                "amount_cents": row[11], "steps": steps,
                "participants": participants, "topic_category": category,
                **communication, "quarter": quarter,
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
    expected_sender_type = plan["sender_entity_type"]
    sender_types = {
        person["email"]: person["entity_type"] for person in plan["participants"]
    }
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
        if index == 1 and sender_types[email["sender"]] != expected_sender_type:
            raise ValueError("first sender does not match communication direction")
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


def generate_emails(workload, model=None, concurrency=25):
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
        quote = rng.choice(QUOTES)
        font = rng.choice(FONT_VARIANTS)
        characteristics[email] = {
            **person, "tone": rng.choice(TONE), "mbti": rng.choice(MBTI),
            "signature_template": rng.randrange(len(SIGNATURE_TEMPLATES)),
            "quote": quote["text"], "quote_author": quote["author"],
            "font_variant": font["name"], "font_family": font["family"],
            "font_size_px": rng.choice(FONT_SIZES),
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
    values = dict(characteristic)
    values["quote"] = (
        f"{characteristic['quote']} — {characteristic['quote_author']}"
    )
    return SIGNATURE_TEMPLATES[characteristic["signature_template"]].format(**values)


def _base_subject(subject):
    return re.sub(r"^(?:\s*re\s*:\s*)+", "", subject, flags=re.IGNORECASE).strip()


def _slug(value):
    return "".join(
        character if character.isalnum() else "-"
        for character in value.lower()
    ).strip("-")


def _generate_brand_logos(characteristics, output, image_model, quality, companies):
    assets = output / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    companies = sorted(companies)[:3]
    client = OpenAI()
    logos = {}
    generated = 0
    missing = [
        company for company in companies
        if not (assets / f"{_slug(company)}.png").is_file()
    ]
    generated_images = {}
    if missing:
        print(f"Generating {len(missing)} brand logos in one API batch")
        response = client.images.generate(
            model=image_model,
            prompt=(
                "Generate a brand logo for each of these companies as separate "
                f"images, in this exact order: {', '.join(missing)}"
            ),
            n=len(missing),
            size="1024x1024",
            quality=quality,
            output_format="png",
            background="transparent",
        )
        generated_images = dict(zip(missing, response.data))
    for company in companies:
        path = assets / f"{_slug(company)}.png"
        if not path.is_file():
            path.write_bytes(
                base64.b64decode(generated_images[company].b64_json)
            )
            generated += 1
        logos[company] = path
    return logos, generated


def render(
    email_json_objects, signature_json_path, results_folder_path=None, model=None,
    started_at=None, elapsed_seconds=None, pricing=None, generate_images=False,
    image_model="gpt-image-2.5-flare", image_quality="low",
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
    company_counts = {}
    for thread in email_json_objects:
        for item in thread["emails"]:
            company = characteristics[item["sender"]]["company"]
            company_counts[company] = company_counts.get(company, 0) + 1
    message_companies = [
        company for company, _ in sorted(
            company_counts.items(), key=lambda item: (-item[1], item[0])
        )[:3]
    ]
    logos, generated_logo_count = (
        _generate_brand_logos(
            characteristics, output, image_model, image_quality, message_companies
        )
        if generate_images else ({}, 0)
    )
    flattened = []
    thread_mbox_paths = set()
    for thread in email_json_objects:
        plan, messages = thread["plan"], thread["emails"]
        references, previous = [], None
        thread_messages = []
        base_subject = _base_subject(messages[0]["subject"]) or "Business update"
        for item in messages:
            characteristic = characteristics[item["sender"]]
            message_id = f"{plan['thread_id']}-{item['sequence']:02d}@bonsai.local"
            subject = (
                base_subject if item["sequence"] == 1 else f"Re: {base_subject}"
            )
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
            message["Subject"] = subject
            if previous:
                message["In-Reply-To"] = f"<{previous}>"
                message["References"] = " ".join(f"<{ref}>" for ref in references)
            message.set_content(body)
            logo_path = logos.get(characteristic["company"])
            font_family = html.escape(characteristic["font_family"], quote=True)
            font_size = int(characteristic["font_size_px"])
            html_body = (
                "<html><body>"
                + f'<div style="font-family:{font_family};font-size:{font_size}px">'
                + "<p>" + html.escape(item["text"]).replace("\n", "<br>") + "</p>"
                + "<p>" + html.escape(_signature(characteristic)).replace("\n", "<br>") + "</p>"
            )
            if logo_path:
                cid = f"logo-{_slug(characteristic['company'])}@bonsai.local"
                html_body += (
                    f'<img src="cid:{cid}" alt="{characteristic["company"]} logo" '
                      'style="max-width:180px;max-height:100px">'
                )
            html_body += "</div></body></html>"
            message.add_alternative(html_body, subtype="html")
            if logo_path:
                message.get_payload()[-1].add_related(
                    logo_path.read_bytes(), maintype="image", subtype="png",
                    cid=f"<{cid}>", filename=logo_path.name,
                )
            eml_path = eml_dir / f"{message_id.replace('@', '_at_')}.eml"
            eml_path.write_bytes(message.as_bytes())
            expected_paths.add(eml_path)
            thread_messages.append(message)
            flattened.append({
                **item, "subject": subject, "message_id": message_id,
                "thread_id": plan["thread_id"],
                "body": body, "eml_path": str(eml_path.relative_to(output)),
                "logo_path": (
                    str(logo_path.relative_to(output)) if logo_path else None
                ),
                "sender_name": characteristic["name"],
                "font_variant": characteristic["font_variant"],
                "font_family": characteristic["font_family"],
                "font_size_px": characteristic["font_size_px"],
                "quote": characteristic["quote"],
                "quote_author": characteristic["quote_author"],
                "workflow_instance_id": plan["workflow_instance_id"],
                "workflow": plan["workflow"], "variant": plan["variant"],
                "domain": plan["domain"], "exception": plan["exception"],
                "source_table": plan["source_table"], "source_id": plan["source_id"],
                "topic_category": plan["topic_category"], "topic": plan["topic"],
                "tone": plan["tone"], "mbti": plan["mbti"], "steps": plan["steps"],
                "quarter": plan["quarter"],
                "sender_group": plan["sender_group"],
                "communication_direction": plan["communication_direction"],
                "sender_entity_type": plan["sender_entity_type"],
                "recipient_entity_types": plan["recipient_entity_types"],
                "sender_role": plan["sender_role"],
                "sender_department": plan["sender_department"],
                "relationship": plan["relationship"],
                "generation_type": "workflow_evidence", "in_reply_to": previous,
            })
            previous = message_id
            references.append(message_id)
        mbox_dir = output / "threads"
        mbox_dir.mkdir(parents=True, exist_ok=True)
        mbox_path = mbox_dir / f"{plan['thread_id']}.mbox"
        mbox_path.unlink(missing_ok=True)
        thread_box = mailbox.mbox(mbox_path, create=True)
        try:
            for message in thread_messages:
                thread_box.add(message)
            thread_box.flush()
        finally:
            thread_box.close()
        thread_mbox_paths.add(mbox_path)
        for record in flattened[-len(messages):]:
            record["mbox_path"] = str(mbox_path.relative_to(output))
    for stale_path in eml_dir.glob("*.eml"):
        if stale_path not in expected_paths:
            stale_path.unlink()
    for stale_path in (output / "threads").glob("*.mbox"):
        if stale_path not in thread_mbox_paths:
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
        for path in sorted((output / "threads").glob("*.mbox")):
            bundle.write(path, path.relative_to(output))
        for path in sorted((output / "assets").glob("*")) if (output / "assets").exists() else []:
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
        "image_model": image_model if generate_images else None,
        "image_quality": image_quality if generate_images else None,
        "logos": len(logos), "logos_generated": generated_logo_count,
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
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--input-price", type=float)
    parser.add_argument("--cached-input-price", type=float)
    parser.add_argument("--output-price", type=float)
    parser.add_argument(
        "--generate-images", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--image-model", default="gpt-image-2.5-flare")
    parser.add_argument(
        "--image-quality",
        choices=["low", "medium", "high", "xhigh", "max", "auto"],
        default="low",
    )
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
        generated, signatures, results, model, started_at, elapsed, pricing,
        args.generate_images, args.image_model, args.image_quality,
    )
    print(
        f"Created {len(generated)} threads and {len(rendered)} messages "
        f"in {elapsed:.2f}s"
    )
    print(f"Artifacts: {results / 'email_gen'}")


if __name__ == "__main__":
    main()
