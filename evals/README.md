# Eval cases

Each subdirectory of `cases/` is one known-correct test case. To add one,
drop in:

```
cases/<case_id>/
  old.png                 # or .jpg — the OLD (prior) sheet revision
  new.png                 # the NEW (revised) sheet revision
  expected_output.json    # what the engine should produce
```

`dre eval` (or `python evals/run_eval.py`) skips any case directory missing
one of those three files, so partially-populated cases won't fail the run.
This runs fully locally — no Supabase credentials needed — since it executes
the pipeline directly against these two image files with a no-op step logger,
then maps the result onto the same `flagged_changes` shape (`change_type`,
`confidence_tier`, ...) the live service writes, via `dre.mapping`.

## `expected_output.json` schema

```json
{
  "alerts": [
    {
      "change_type": "moved",
      "required_entities": ["LP-2", "circuit 14"],
      "description_keywords": ["relocated", "re-route"],
      "confidence_tier": "high"
    }
  ]
}
```

- `change_type` — one of `"added"`, `"removed"`, `"moved"`, `"modified"` —
  matches `flagged_changes.change_type` exactly.
- `required_entities` — substrings (case-insensitive) that must appear
  somewhere in the matched alert's description, impact note, or affected
  entity identifiers. Use this for panel/circuit/device tags the alert must
  mention.
- `description_keywords` — optional substrings that must appear in the
  description/impact note text.
- `confidence_tier` — optional; if set, the actual alert's tier
  (`"high"`/`"medium"`/`"low"`, using the `>=90 / 70-89 / <70` bands) must
  match exactly. Omit it to not grade confidence for that alert.

Per how we agreed to judge correctness — same changes captured, a roughly
matching confidence tier, and correct reasoning (e.g. bundling a panel move
with all of its downstream circuit impacts into one alert instead of one per
circuit) rather than exact wording — the scorer never compares exact text,
only substrings/keywords and the `change_type`/`confidence_tier` fields.

The scorer (`evals/scorer.py`) does not compare ids — those are regenerated
every run. It greedily matches each expected alert to the first actual alert
that satisfies all of the above, and reports:

- **missed** — an expected alert with no actual match (engine under-reported,
  or failed to bundle several changes into the one alert you expected).
- **hallucinated** — an actual alert matching no expected entry (engine
  over-reported, which is exactly the failure mode the materiality/noise
  filtering in the `classify` stage exists to prevent).

A case only passes if there are zero missed and zero hallucinated alerts.
