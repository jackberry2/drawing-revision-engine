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

## Full-page single-image analysis has a hard resolution ceiling that real large-format sheets exceed

**What happened**: the first genuinely large real-world sheet run through
the pipeline (E-101.3, a 50"x36" ARCH E drawing, single-sheet mode) showed
three symptoms the user caught by comparing output against the source PDF
at full resolution: a revision tag correctly reading "4" (confirmed
elsewhere on the same sheet, in the "BULLETIN 4" issuance table) got
misread by `detect_single` as "1"; a communications-conduit revision cloud
containing clearly-printed text ("NORTH/SOUTH COMMUNICATIONS CONDUIT
SERVICE ENTRANCE", "4\" CONDUIT STUBBED INTO BOILER ROOM") came back fully
`identity_unresolved` despite that text being legible in the source; and
what looked like 2 real clouds producing 5 flagged items — investigated via
the raw `pipeline_steps` trace and turned out to be two separate causes
layered together, not one bug (see the accumulation note below).

**Root cause, confirmed against Anthropic's own vision docs**
(platform.claude.com/docs/en/build-with-claude/vision): Claude 4.7+ models
(this pipeline's `DETECT_MODEL`/`REASONING_MODEL` default,
`claude-sonnet-5`, qualifies) natively use images up to only **2576px on
the long edge / 4784 visual tokens** before Claude resizes them down
server-side — this is a hard ceiling regardless of what resolution is sent.
A 50"-wide sheet at 2576px works out to roughly **51 DPI-equivalent**,
nowhere near what's needed to reliably resolve 6-8pt architectural
callouts and revision-tag digits. This is a full-page-image ceiling, not
something tunable by sending a bigger source image — anything beyond
2576px just gets discarded by Claude's own resize step for zero benefit.

**Real, direct evidence this is the actual mechanism, not just a plausible
theory**: the two runs against the identical source file (same bytes, same
rasterization) produced meaningfully different detection counts (3 raw
detections vs. 6), different digit reads (a legible "1" vs. no digit
legible at all — never a correct "4" in either run), and one run extracted
4 real schedule/legend tables in full while the other extracted zero. The
model's own self-reported `image_quality_note`s in the second run say so
directly: *"the fine text within the cloud... is small and somewhat
difficult to fully verify at native scan quality"* and *"the fine text
inside the small callout markers is too small to read with certainty."*
Run-to-run instability on identical input, landing right at the edge of
legibility, is the signature of marginal/borderline image quality, not
random model variance independent of the input.

**What was fixed immediately**: `imaging.py`'s PDF rasterization target was
raised from an unverified 2000px guess to the confirmed real ceiling,
2576px — free resolution the model could already use that was being left
on the table. This is a correctness fix, not a resolution-adequacy fix: it
does not close the ~3-6x gap between what a single full-page image can
deliver (~51 DPI-equivalent on this sheet) and what small print realistically
needs (roughly 150-300 DPI-equivalent).

**What closing that gap actually requires — not yet built**: per-region/
tiled analysis, splitting a large sheet into multiple overlapping crops
each sent as its own image (each then eligible for the full 2576px ceiling
against a much smaller physical area, multiplying effective DPI). This is
a real architecture change, not a config tweak: proportionally multiplies
vision API cost per sheet, requires mapping each tile's local detections
back to full-sheet normalized coordinates, and requires new merge/dedup
logic for detections spanning a tile boundary or duplicated across
overlapping tiles. Deliberately not built without a scoping discussion
first, same as single-sheet mode itself went through a findings doc before
implementation (`docs/single_sheet_mode_findings.md`).

**Separate finding surfaced alongside this, not the same bug**: the
"2 clouds → 5 items" observation was actually two independently-triggered
runs against the same `analysis_request_id` (one from verification, one
independently re-triggered — Lovable or the user retrying after an earlier
attempt failed) each writing their own `flagged_changes` rows, with nothing
in `service.analyze_request` superseding or clearing a prior run's rows for
the same request. Combined with the run-to-run detection-count instability
above, this produced what looked like single-run over-segmentation but
wasn't. Whether re-analysis should delete-and-replace prior rows, be
rejected outright once a request is already analyzed, or stay something the
caller (Lovable) is responsible for avoiding is a product decision, not
answered here.

## `reason` can fabricate a confident causal claim while the real cause sits orphaned nearby, unlinked

**What happened**: a real production re-run of E-201 (two-image mode)
produced a `panel_relocation` event whose bundle included circuit C3's
reroute, with `root_cause_summary`/the bundled item's own text stating C3
was rerouted *"because"* the panel moved. Separately, in the same run, an
unrelated new element (a short unlabeled vertical line near Office B)
came back as its own honestly-hedged `identity_unresolved` event — "an
item on this sheet is flagged for revision, but its identity and purpose
cannot be confirmed." The two were never connected. A second run against
the identical sheet pair got it right: `detect` resolved the same visual
element as a wall/partition, and `reason` correctly bundled C3's reroute
under a *separate* event caused by the new wall, exactly matching the
original validated ground truth for this sheet (the answer key
specifically constructed this case to test that a shared circuit doesn't
get its cause conflated with an unrelated nearby event).

**Where the fabrication actually starts, confirmed against the raw
trace, not assumed**: the causal claim ("replaced by a new home run path
from the relocated panel") is already present in `classify`'s output for
that item, before `reason` ever runs — `classify.md` never explicitly
forbids asserting a specific external cause, even though determining root
cause is architecturally `reason`'s job. `reason` then fails to catch or
override it. That second failure is the more surprising one: `reason.md`
already contains guidance for *this exact scenario*, using the same
circuit-letter pattern as the real bug — *"If a panel relocation forces
circuits C1, C2, and C4 to reroute, but C3 reroutes for a different
reason — a new wall in its path — C3 does not belong in the panel's
bundle just because it's also a reroute on the same panel."* The model
was given this near-verbatim and still violated it once under real
conditions. Worth keeping that reinforcement — it's cheap — but not
trusting it as sufficient alone, having now watched it fail once despite
being about as explicit as prompt guidance gets.

**Why this isn't enforceable in code the same way `identity_unresolved`
is** (see `identity_resolution.py`): that mechanism works because
`identity_unresolved` is a boolean the model already self-reports on
`ClassifiedChange` — code only has to guarantee the flag survives
propagation, never re-deriving or verifying it. There's no equivalent
self-reported signal for "this bundling is correct." Mechanically
verifying the *causal claim itself* would mean independently redoing the
same visual/electrical judgment the model already got wrong — not
something code can do cheaply or reliably. Checked whether the structured
data offers a proxy anyway: it doesn't, for a specific reason — the
orphaned item's `involved_entities` is empty precisely *because* its
identity is unresolved, which is exactly the signal that would be needed
to mechanically link it back to the wrongly-attributed item. Spatial
(bounding-box) cross-referencing was also considered and rejected — this
project's own tiling work independently found detect's self-reported
regions unreliable even across independently-agreeing runs (see
`docs/tiled_analysis_findings.md` §3c), so trusting them here would repeat
a mistake already paid for elsewhere.

**What's buildable instead: a structural co-occurrence signal into
confidence scoring and describe's output, not a fix at `reason` itself.**
Not a guarantee the causal claim is correct — a real, honestly-scoped
signal that a run's causal claims are less trustworthy than usual, always
true whenever the shape recurs:

- **The signal**: within one pipeline run, does `ctx.change_events`
  contain (a) at least one event with `identity_unresolved: true`, and
  (b) at least one *other* event, not itself unresolved, whose `category`
  is one that asserts a specific external cause for a device/circuit —
  `panel_relocation`, `device_relocation`, `circuit_reroute`,
  `device_modified` (deliberately excludes `device_added`/
  `device_removed`, whose own cause is normally the addition/removal
  itself, and excludes `schedule_label_edit`/`annotation_only`/
  `noise_non_material`, which don't assert this kind of causal narrative).
  Both facts are already present on `ChangeEvent` — no new detection or
  classification work needed, purely a property of what `reason` already
  produced for this run.
- **Confidence discount**: extend `apply_mode_ceiling` (`confidence.py`)
  with a new ceiling on `ambiguity_factor`, applied to any event where the
  signal fires — mirroring exactly how `_SINGLE_SHEET_AMBIGUITY_CEILING`
  and `_UNRESOLVED_IDENTITY_AMBIGUITY_CAP` already work, just a third,
  weaker case. Proposed starting value: **0.75** — chosen specifically to
  sit just below `_CLEAR_AMBIGUITY_CUTOFF` (0.93), so a flagged event
  can't reach the "textbook clear, 0.90+" scoring band regardless of how
  clean the rest of its evidence looks (E-201's bad `evt1` scored 0.8525
  — in that band — specifically because its *other* bundled items really
  were clear), while staying well above `_UNRESOLVED_IDENTITY_AMBIGUITY_CAP`
  (0.3): this signal is real but much weaker evidence than "this specific
  item's own identity is unresolved," so it shouldn't be punished nearly
  as hard. Requires threading the full `ctx.change_events` list (or a
  precomputed boolean) into the per-event confidence call, which currently
  only sees one event at a time — a real but small structural change.
  **0.75 is a starting point, not a calibrated number** — same posture as
  every other constant introduced this way in this codebase (DPI,
  `MIN_SHARED_TOKENS`): don't lock it in off one confirmed real instance.
- **User-facing caveat**: append (never replace — this isn't a certainty
  the way `identity_unresolved` is) a fixed, code-authored sentence to the
  flagged event's `downstream_implications`, in the same post-`reason`
  pass as `enforce_identity_unresolved` — e.g. *"This sheet also has a
  separate, unidentified flagged item; if that item is actually related to
  this change, the stated cause here may need to be revisited."* Flows
  into `impact_note` for free through `describe.py`'s existing
  `_build_impact_note`, which already assembles `impact_note`
  deterministically from `downstream_implications` — no `describe.py`
  change needed.
- **Passive diagnostic logging, before trusting the number**: log whether
  the signal fired per run (and for how many events), the same pattern
  established for §3a's tiling trigger and §3c's merge-threshold
  diagnostics — real accumulating evidence to tune the 0.75 ceiling
  against later, instead of guessing once and leaving it. Natural home:
  alongside the existing `ambiguity_factor_raw` pattern on
  `ConfidenceScore`, or in `pipeline_change_events.confidence_rationale`,
  which already stores this kind of per-event reasoning detail.

**Not yet built** — sketched and reviewed, deliberately not implemented
before deciding whether to prioritize it against the tiling work still in
flight (§3f parallel execution, §3e's duration hint). A real, scoped,
buildable gap, not an open question.

## One-off: transient `classify` malformed tool-call failure

A single E-101.3 re-run hit `call_structured`'s "malformed payload 3 times
in a row" failure on `classify` (`emit_classifyresponse`), unrelated to the
resolution work above — the response never hit the max_tokens/truncation
branch, so it wasn't an oversized-output issue. Succeeded cleanly on
immediate retry with no other change. Not investigated further since it
hasn't recurred; noting it here in case it does.
