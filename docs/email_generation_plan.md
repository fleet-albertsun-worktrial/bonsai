# Realistic Email Generation Plan

## Goal

Generate realistic email threads grounded in the selected Bonsai world, preserve
their workflow evidence, package native messages, and expose them in the results
viewer.

## Inputs and outputs

`generate_emails.py` takes a results directory containing `output.sqlite` and
`statistics.json`. It creates `email_gen/` inside that directory with:

- `emails.jsonl` for viewer metadata and evidence.
- `emails/*.eml` for native RFC email messages.
- `emails.zip` containing all native messages.
- `personnel_email_characteristics.json` for stable sender styles/signatures.
- `generation_statistics.json` for model usage, elapsed time, pricing, and cost.

## Generation

1. Read workflow instances, ordered steps, and source entities from SQLite in
   read-only mode.
2. Build deterministic thread plans with real participants and a 50/25/25 mix
   of direct, water-cooler, and deadline-oriented context.
3. Prompt the configured model concurrently using
   `prompt_templates/email_thread_generation.jinja2`.
4. Validate participant addresses, required fields, thread size, and evidence
   boundaries.
5. Apply stable personnel characteristics and signatures, render `.eml` files,
   and create the JSONL and ZIP atomically.

## Viewer

Add an Emails page to `app.py` with:

- A searchable document list on the left.
- Selected message headers and content in the middle.
- Generation, personality, workflow, source record, and step properties on the
  right, with downloads for individual `.eml` files and the complete ZIP.

## Approved validation

Run exactly 100 threads against `results/large-v6` with Terra after a small
structural smoke test. Parse every `.eml`, check every ZIP member, and report
thread/message counts, usage, cost, and wall-clock duration. Cost is calculated
only when explicit provider pricing is supplied; otherwise it remains
unavailable rather than being estimated.
