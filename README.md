# Drawing Revision Engine (dre)

Backend service for an existing Lovable + Supabase app. Given an
`analysis_requests` row, it fetches the OLD/NEW drawing images from Supabase
Storage, compares them, and writes estimator-grade, trade-language change
alerts into `flagged_changes` — root cause + downstream implications,
materiality-filtered, confidence-scored — not a raw visual diff. It reads/
writes the app's existing tables using their exact schemas; it does not own
or redefine them.

## Why it's built this way

This is meant to become an in-house, fine-tunable detection system, not a
permanent wrapper around a hosted vision API. So:

- The reasoning pipeline is five swappable stages (`detect → classify →
  reason → confidence → describe`), each behind the same input/output
  contract (`src/dre/models/schemas.py`). Any stage — e.g. `detect`, today a
  Claude vision call — can be replaced by a custom-trained model later
  without touching the others or the storage/service layer around them.
- Every stage's input/output is logged to `pipeline_steps` in the same
  generic shape regardless of what implementation produced it.
- `flagged_changes` only holds the final, simplified fields the app displays
  (`change_type`, `confidence_tier`, ...). The richer internal reasoning
  behind each row — category taxonomy, which raw changes got bundled
  together, the confidence factor breakdown — is preserved in
  `pipeline_change_events`, linked to that exact row.
- `human_reviews` captures what a reviewer confirmed or corrected against the
  real `flagged_changes` row. That table is the future fine-tuning dataset —
  populated from day one even though nothing is fine-tuned yet.

## Setup

```bash
cd ~/Documents/drawing-revision-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DRE_API_KEY
```

Apply the additive migration for the proprietary logging tables (this
project can't run it for you — no DB credentials, and it's your production
project) via the Supabase SQL editor or CLI:

```bash
supabase db push   # or paste supabase/migrations/0001_pipeline_logging.sql into the SQL editor
```

It only creates `pipeline_runs`, `pipeline_steps`, `pipeline_change_events`,
and `human_reviews` — it never alters `drawings`, `analysis_requests`, or
`flagged_changes`.

## Usage

```bash
uvicorn dre.api:app --reload        # POST /analyze/{analysis_request_id}
dre analyze <analysis_request_id>   # same thing, from the CLI, for manual testing
dre review <flagged_change_id>      # capture a human correction on one flagged_changes row
dre eval                            # run the pipeline against evals/cases/* — fully local, no Supabase needed
```

`POST /analyze/{analysis_request_id}` looks up the request, downloads both
drawing images from Storage, runs the pipeline, writes one row per alert to
`flagged_changes` (`drawing_id` = the NEW revision's drawing id) plus a
linked `pipeline_change_events` row, and sets
`analysis_requests.status = "in_review"`. It is **not** yet wired to the
"Analyze Changes" button in the app — that trigger is a separate, later step.

Requires an `X-API-Key` header matching `DRE_API_KEY` — this is a key *we*
issue to callers (Lovable), separate from the third-party secrets above.
`/health` does not require it.

## Eval cases

Drop your three validated test cases into `evals/cases/<case_id>/` as
`old.png`, `new.png`, and `expected_output.json` — see
[evals/README.md](evals/README.md) for the schema and grading rules. `dre
eval` runs entirely locally (no Supabase credentials needed) and should run
every iteration while you tune the prompts.

## Layout

```
src/dre/
  config.py          env/config (ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DRE_API_KEY)
  api.py             FastAPI app: POST /analyze/{analysis_request_id}
  service.py          shared analyze_request() core used by the API and the CLI
  mapping.py          ChangeCategory -> change_type, confidence score -> tier/percentage
  cli.py             dre analyze / eval / review
  models/schemas.py  pydantic contracts between pipeline stages
  pipeline/          the five stages + orchestrator + StepLogger abstraction
  llm/               Claude client wrapper + prompt templates
  supa/              Supabase client + repository (existing tables + proprietary logging tables)
supabase/migrations/  additive SQL for pipeline_runs/pipeline_steps/pipeline_change_events/human_reviews
evals/                eval harness + the 3 test cases
```
