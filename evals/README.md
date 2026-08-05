# Eval cases

Each subdirectory of `cases/` is one known-correct test case. To add one,
drop in:

```
cases/<case_id>/
  prev.png              # or .jpg — previous sheet version
  revised.png            # revised sheet version
  expected_output.json   # what the engine should produce
```

`dre eval` (or `python evals/run_eval.py`) skips any case directory missing
one of those three files, so partially-populated cases won't fail the run.

## `expected_output.json` schema

```json
{
  "alerts": [
    {
      "category": "panel_relocation",
      "required_entities": ["LP-2", "circuit 14"],
      "description_keywords": ["relocated", "re-route"],
      "min_confidence": 0.6
    }
  ]
}
```

- `category` — must match one of the `ChangeCategory` values in
  `src/dre/models/schemas.py` (e.g. `panel_relocation`, `circuit_reroute`,
  `device_added`, `device_removed`, `device_modified`, `conduit_run_change`,
  `schedule_label_edit`, `annotation_only`).
- `required_entities` — substrings (case-insensitive) that must appear
  somewhere in the matched alert's description or affected-entity
  identifiers. Use this for panel/circuit/device tags the alert must mention.
- `description_keywords` — optional substrings the alert's description text
  must contain.
- `min_confidence` — optional lower bound on the alert's confidence score.

The scorer (`evals/scorer.py`) does not compare ids — those are regenerated
every run. It greedily matches each expected alert to the first actual alert
that satisfies all of the above, and reports:

- **missed** — an expected alert with no actual match (engine under-reported).
- **hallucinated** — an actual alert matching no expected entry (engine
  over-reported, which is exactly the failure mode the materiality/noise
  filtering in the `classify` stage exists to prevent).

A case only passes if there are zero missed and zero hallucinated alerts.
