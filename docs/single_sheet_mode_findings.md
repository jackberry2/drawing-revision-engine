# Single-sheet mode: findings from a manual E-501 test

## What this is

A single standalone sheet (E-501, Rev 2) — no paired prior-revision image —
already carrying the drafter's own revision clouds, numbered revision
triangles, and a highlighted schedule row, was fed through `detect` →
`classify` → `reason` manually, using the existing two-image-oriented
prompts/schemas completely unmodified. The goal was to see what breaks or
falls short before designing a real single-sheet mode, not to build that
mode yet. Full raw output is in the appendix.

Bottom line up front: this is not a smaller version of the two-image
pipeline. It's a categorically different task — *verify and interpret the
drafter's own revision markup* — rather than *independently detect change*.
That distinction should drive every design decision below, especially
confidence.

## 1. The core limitation: recall is capped by markup discipline, not model capability

Every material item the pipeline found in the test was one the drafter had
already circled with a revision cloud or flagged with a numbered triangle.
`detect` has no genuine "before" state to diff against, so it has no
mechanism to notice a change that exists on the sheet but wasn't clouded —
that information simply isn't present in a single image. This is not a
recall gap prompt-tuning can close, the way we've closed other gaps this
session (title-block noise, unverified spatial claims, etc.). Those were
cases where the *information needed* to get it right was available in the
input and the model just wasn't using it well. This is different: the
information needed to detect an unmarked change — a prior state — does not
exist in a single-sheet input, at any model capability level.

**Design implication:** treat single-sheet mode as a permanent-ceiling
capability, not a temporary limitation to iterate away. It should be
positioned (in code, in any UI/messaging that surfaces its output, and in
how its confidence is scored — see below) as "verifies what's marked," not
"detects what changed." Two-image mode should remain the preferred path
whenever a prior revision is available; single-sheet mode is a fallback for
when it isn't, not an equivalent alternative.

## 2. Confidence scoring should have a structurally lower ceiling

Agree with the instinct that single-sheet mode should never reach the
confidence a two-image comparison can reach, even on a clearly-marked,
unambiguous item — but the mechanism matters. Given what we learned from the
E-201 confidence-variance bug (put guarantees in code, not prompt-only
guidance, when they need to actually hold), the ceiling should be a
structural clamp in `synthesize_score`, not a hope that the model
self-reports lower factors in single-sheet mode.

Concretely: add a `mode` (or similar) parameter to the synthesis call, and
in single-sheet mode, clamp the two factors that are inherently weaker
*before* running the existing formula:

- **`ambiguity_factor` capped at 0.85.** In two-image mode, ambiguity
  measures whether an independently-detected pixel difference is real. In
  single-sheet mode, the best available evidence is "the drafter clouded
  this and labeled it clearly" — which is real evidence, but it's evidence
  about markup, not a verified visual difference. Capping below the
  existing `_CLEAR_AMBIGUITY_CUTOFF` (0.93) is what actually matters: it
  structurally prevents single-sheet items from ever qualifying for the
  "textbook clear" score band, regardless of how clean the markup is.
- **`cross_sheet_corroboration_factor` capped at 0.7.** Corroboration in
  two-image mode means "the schedule changed in a way consistent with the
  visual change" — direct evidence of a state *transition*. In single-sheet
  mode there's only one schedule snapshot, so the best available
  corroboration is "the current schedule is consistent with what the
  markup claims" (see #3) — evidence about consistency, not about change
  having occurred.

With both caps applied, the maximum achievable score under the existing
formula's "otherwise" blend (`0.60*ambiguity + 0.25*image_quality +
0.15*corroboration`) works out to about **0.835** even with perfect image
quality — solidly in the medium tier, never high. That's a clean,
falsifiable ceiling rather than an ad hoc rule, and it reuses the synthesis
architecture that's already been validated rather than adding parallel
special-case logic.

## 3. `present_in` and `schedule_corroboration` need mode-specific meanings

Both fields, as defined today, describe a *comparison outcome* between two
images. Neither has a valid answer for a single-sheet input, and forcing
one produces the failure we saw directly in the test: all 6 raw detections
got `present_in: "new_only"` — technically required by the schema, but a
meaningless claim, since "new_only" specifically means "present in the new
image, absent in the old," and there is no old image to be absent from.

Proposed redefinitions for single-sheet mode (new field names, not reused
two-image semantics):

- **`present_in` → `flagged_by`**: `Literal["revision_cloud",
  "revision_tag", "annotation_note", "unmarked"]`. Describes *how* the
  drafter indicated this is a change, not which side of a diff it's on.
  `"unmarked"` stays available for the rare case where something looks
  suspicious despite no markup at all — per #1, the pipeline can't reliably
  *find* those, but if one surfaces incidentally, labeling it honestly as
  unmarked (rather than forcing a fake cloud/tag attribution) matters.
- **`schedule_corroboration` → `schedule_consistency`**: describes whether
  the *current* schedule state is internally consistent with what the
  markup claims changed (e.g., "schedule shows C3/HVAC Unit (NEW)/30A,
  consistent with the revision cloud's implied addition"). This is
  explicitly weaker than corroboration: it confirms the current state
  matches the story, not that comparing two states confirms a transition
  happened. Keeping the name distinct (rather than reusing
  `schedule_corroboration`) keeps that distinction visible everywhere it's
  read later, including in `pipeline_change_events` and any future
  human-review UI.

## 4. Unresolved symbols should surface as their own explicitly-unresolved item

In the test, the unlabeled black bar symbol got folded into a normal,
confidently-shaped `device_added` classification — `is_material: true`,
a real category, a `trade_description` — with the genuine uncertainty
buried inside the free-text `materiality_reason` ("this *looks like* a new
device/equipment... warrants estimator review") rather than structurally
represented. `reason` handled its own part honestly (flagged that no
schedule entry matches, left `affected_entities` empty), but nothing stops
this from eventually reaching `flagged_changes` shaped exactly like a
normal, confident `"added"` row — the same failure mode we already fixed
once for CONFIRM WITH EC, just recurring in a different stage.

Recommendation: yes, treat this the same way we resolved CONFIRM WITH EC —
as its own explicitly-unresolved item, not a best-guess folded into a
normal category. Concretely:

- Add an `identity_unresolved: bool` (or equivalent) field to the
  classified-change shape for single-sheet mode, set when the item is
  flagged by markup but can't be matched to a schedule row, a legend entry,
  or a legible tag.
- When set, `reason` should not bundle it into a confidently-categorized
  event. It should become its own `ChangeEvent` whose
  `root_cause_summary` says plainly that the item is flagged but
  unidentified, rather than asserting a best-guess category.
- In confidence scoring, `identity_unresolved` should *force* — not just
  suggest — `ambiguity_factor` into the low band (same mechanism as the
  `< 0.5` cutoff in `synthesize_score`), so it structurally lands in the
  low confidence tier. Per the lesson from the E-201 bug, this shouldn't
  depend on the model consistently self-reporting low ambiguity for this
  case every time; it should be enforced in code once `identity_unresolved`
  is set.
- The final description should read like the DS-2/E-301 precedent —
  "flagged for [X], identity/purpose unconfirmed" — not a confident device
  description standing in for something the pipeline actually doesn't know.

## 5. "Moved" claims are structurally less verifiable than "added" claims

Confirmed by the real, implemented single-sheet mode against E-501 (see
appendix addendum): O1's relocation landed at 61% confidence
(`ambiguity_factor` 0.5) while the C3 schedule addition landed at 82%
(`ambiguity_factor` 0.85), even though both were marked by the drafter
equally clearly. That gap emerged correctly from the model's own reasoning
on this run — worth understanding *why*, and whether to trust it to keep
happening.

An addition can be self-evidenced by the *current* state alone: an explicit
"(NEW)" label, a newly-highlighted schedule row, a row that simply wasn't
there before all directly prove something new exists, with no prior-state
comparison needed. A relocation claim is different in kind — verifying "this
moved" fundamentally requires comparing a position now to a position before,
which is exactly the information single-sheet mode structurally lacks.
There's no current-state marker that substitutes for "I can see it used to
be somewhere else." So the *category* of the underlying claim (added/removed
vs. moved) carries real information about how verifiable it can even be in
this mode, independent of how clearly it's marked.

The model discounted O1's ambiguity correctly this run, but per the same
reasoning behind `identity_unresolved` (#4), trusting that discount to be
consistent run-to-run is a weaker guarantee than a structural one — nothing
stops a future run from scoring a relocation claim's `ambiguity_factor` near
1.0 if the markup happens to look especially convincing, the way E-201's
`ambiguity_factor` swung between runs on visual evidence alone (see the
earlier confidence-variance investigation).

**Recommendation**: yes, enforce this in scoring, the same way as
`identity_unresolved` — a second, stricter ceiling specific to relocation
categories (`panel_relocation`, `device_relocation`) in single-sheet mode.
Proposed: `ambiguity_factor` capped around **0.6** for these categories
specifically (vs. 0.85 for other single-sheet material changes). Under the
existing formula that puts maximum relocation confidence at roughly
`0.60*0.6 + 0.25*image_quality + 0.15*corroboration` ≈ high-50s/low-60s%
even at perfect image quality and corroboration — solidly low tier, not
merely excluded from high tier the way other single-sheet changes are.
Whether removal claims deserve the same treatment is untested (no removal
case has come up yet), but the same logic — no current-state marker proves
something used to be present and now isn't — likely applies just as
strongly, maybe more so. Worth checking against a real single-sheet removal
case before deciding whether to extend the cap to that category too.

## Appendix A: raw manual test output (pre-implementation)

Sheet: E-501 Rev 2 (Conference Room, Panel P-5). Fed through `detect` →
`classify` → `reason` with the unmodified prompts, plus one clarifying note
that this is a standalone single image with no paired old/new revision.

**detect** found 6 raw detections, all `present_in: "new_only"` (see
finding #3): the O1/C1 relocation cloud + "RELOCATED PER RFI #14" label, the
unlabeled black bar symbol's cloud, three separate revision-triangle
markers, and a highlighted new `C3 / HVAC Unit (NEW) / 30A` schedule row.
One schedule table was extracted, correctly labeled `sheet_version: "new"`
only — it did not fabricate a phantom "old" table.

**classify** materialized 3 of the 6 as material: the O1 relocation
(`device_relocation`), the unlabeled bar (`device_added`, hedged
materiality reasoning, empty `involved_entities`), and the new C3 circuit
(`device_added`). The three revision-triangle markers were correctly
classified as `annotation_only` / non-material, since each one duplicates a
change already captured by its paired detection.

**reason** produced 3 independent `ChangeEvent`s (no bundling needed — none
of the three material items were causally linked to each other):

- O1 relocation, corroborated by the schedule showing C1 unchanged
  (consistent with only the physical location changing).
- The unlabeled bar, with `reason` explicitly noting no schedule entry
  matches it and `downstream_implications` recommending the estimator
  confirm its identity — this is the item finding #4 addresses.
- The new C3/HVAC circuit, directly corroborated by the new schedule row.

## Appendix B: real implementation verification

After implementing single-sheet mode (`detect_single`/`reason_single`
stages, `flagged_by`/`schedule_consistency` fields, the confidence ceiling
clamp, `identity_unresolved` forcing), the same E-501 sheet was run through
the actual `POST /analyze/{id}` endpoint (`mode: "single_sheet"` read
automatically off `analysis_requests.mode`, no separate endpoint needed).
Confirmed the designed behaviors held on real output, not just in unit
tests against synthetic factor values:

- **O1 relocation**: `device_relocation` → `moved`, 61% confidence (low
  tier). `ambiguity_note` explicitly states it "there is no way to visually
  verify the outlet actually moved" — the honest-uncertainty framing #1 and
  #2 called for, produced without a category-specific ceiling in place yet
  (see #5 above on whether to make this structural rather than emergent).
- **Unlabeled vertical bar**: category `other` (not a confident device
  guess), 35% confidence (low tier, `ambiguity_factor` 0.2), description
  states plainly its identity "cannot be determined from this sheet." This
  is the #4 behavior working end to end — no schedule/legend match, no
  false-certainty category assigned.
- **C3/HVAC schedule addition**: `device_added` → `added`, 82% confidence
  (medium tier — never high, per the #2 ceiling) despite being the clearest,
  best-corroborated item of the three (`ambiguity_factor` 0.85,
  `image_quality_factor` 0.95). Description cites the schedule row itself
  as primary evidence.

All three landed in the user's real project via a dry run first, then a
real write to `flagged_changes` once confirmed correct — no code changes
were needed after the initial implementation to get this output.
