# Pipeline architecture notes

General lessons about this pipeline's reliability that apply across stages
and modes — not scoped to any one feature's findings doc.

## Batched multi-item generation can produce internally coherent but cross-wired output

**What happened**: a real production run (E-501, single-sheet mode) asked
`confidence` to assess three `ChangeEvent`s in one call and return a list of
three `ConfidenceFactors`. Two of the three came back with their content
swapped: the assessment tagged with one event's `change_event_id` contained
notes and numbers that were actually about a *different* event (a
schedule-row addition's evidence described under the unresolved symbol's
assessment, and vice versa). One of the two was even internally
self-contradictory on its own — its `ambiguity_note` described clearly
unambiguous evidence ("little room for alternate interpretation") while its
`ambiguity_factor` was set to 0.3 (low/ambiguous), disagreeing with its own
justification.

**Why this matters more than a normal wrong answer**: every individual
field was well-formed. The response passed schema validation cleanly — it's
not a malformed-payload case `call_structured`'s existing retry logic would
catch. Nothing about the response *looks* wrong in isolation; you only see
it by cross-referencing multiple items in the same response against each
other, or against what the event they're attached to actually describes.
That makes it a fundamentally different failure class from the ones fixed
earlier this project (title-block noise, unverified spatial claims, causal
re-litigation) — those were cases where the model's *reasoning* was wrong in
a way a tighter prompt could correct. This is the model's response
*generation* getting internally scrambled while producing several similar
structured items back to back — attention/binding slipping between items,
not a reasoning error about any single item.

**The fix applied** (`pipeline/confidence.py`): stopped batching. One Claude
call scores exactly one `ChangeEvent`, every time, response shape is a
single `ConfidenceFactors` rather than a list. There is nothing else in the
same response for one event's content to get confused with, so this failure
mode is structurally impossible for this stage now, not just less likely.
Costs one API call per change event instead of one call per run — a
reasonable trade for eliminating an entire class of undetectable data
corruption in output that gets written directly to the user's production
`flagged_changes` table.

**Where else this risk exists**: `detect`/`detect_single` (a list of raw
detections/`SingleSheetDetection`s per call), `classify` (a list of
`ClassifiedChange`s per call), and `reason`/`reason_single` (a list of
`ChangeEvent`s per call) all still batch multiple items into one response.
None of them have shown this failure yet, but "hasn't been observed" isn't
the same as "can't happen" — this is a generation-level risk, not something
tied to what confidence specifically does. Worth defaulting to one-item-per-call
for any future stage that generates multiple independent structured items
in a single response, and worth revisiting the existing batched stages if
this pattern shows up in their output too. The tradeoff is real (more API
calls, more latency, harder to reason about causally-linked items that
genuinely need to see each other — `reason`'s bundling logic in particular
needs to see all material changes together to decide what belongs with
what, so it can't simply be split apart the same way). This isn't a
"always split everything" rule; it's a reason to weigh the tradeoff
deliberately per stage rather than batch by default, especially for a stage
like `confidence` whose items are already independent of each other by the
time they reach it.
