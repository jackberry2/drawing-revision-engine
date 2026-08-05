# Drawing Revision Engine (dre)

Detection engine for electrical drawing revision analysis. Compares two
versions of a drawing sheet and produces estimator-grade, trade-language
change alerts — root cause + downstream implications, materiality-filtered,
confidence-scored — not a raw visual diff.

## Why it's built this way

This is meant to become an in-house, fine-tunable system, not a permanent
wrapper around a hosted vision API. So:

- The pipeline is five swappable stages (`detect → classify → reason →
  confidence → describe`), each behind the same input/output contract. Any
  stage — e.g. `detect`, today a Claude vision call — can be replaced by a
  custom-trained model later without touching the others.
- Every stage's input/output is logged to the `pipeline_steps` table in the
  same generic shape regardless of what implementation produced it.
- Every run has a place (`human_reviews` table) to record what a human
  reviewer confirmed or corrected. That table is the future fine-tuning
  dataset — it's populated from day one even though nothing is fine-tuned yet.

## Setup

```bash
cd ~/Documents/drawing-revision-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in ANTHROPIC_API_KEY
```

## Usage

```bash
dre run prev.png revised.png      # run the pipeline once, log to DB
dre review <run_id>               # capture human corrections for a run
dre eval                          # run the pipeline against evals/cases/* and score vs expected_output.json
```

## Eval cases

Drop your three validated test cases into `evals/cases/<case_id>/` as
`prev.png`, `revised.png`, and `expected_output.json` (see
`evals/cases/case_01/expected_output.json` for the schema). `dre eval` runs
every iteration and reports pass/fail per case.

## Layout

```
src/dre/
  config.py          env/config
  cli.py             dre run / eval / review
  models/schemas.py  pydantic contracts between pipeline stages
  pipeline/          the five stages + orchestrator
  llm/               Claude client wrapper + prompt templates
  storage/           SQLAlchemy models, DB session, file storage
evals/               eval harness + the 3 test cases
data/runs/           per-run image storage (gitignored)
```
