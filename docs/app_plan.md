# Streamlit results browser

## 1. Add the app runtime
- Add `streamlit` to `pyproject.toml`.
- Implement `app.py` as a single-file, read-only UI launched with `uv run app.py --output_filepath results`.

## 2. Load one result snapshot
- Validate `statistics.json` and `output.sqlite`, load them once, and show clear errors for invalid paths.
- Open SQLite in read-only mode and use parameterized values.

## 3. Show pipeline statistics
- Display database size, elapsed stage time, table and row counts, model, parameters, and verifier outcomes.

## 4. Browse every table efficiently
- Provide table and column selection, server-side pagination, a typed filter, and one-column sorting.
- Query only the visible page and render it read-only without downloads.

## 5. Verify and document
- Smoke-test with `uv run app.py --output_filepath results`.
- Document that command in `README.md`.
