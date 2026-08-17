# Tiled/regional analysis for large-format sheets: findings and proposed design

## What this is

A design proposal, not yet implemented. Triggered by the first genuinely
large real-world sheet run through the pipeline (E-101.3, a 50"x36" ARCH E
architectural sheet, single-sheet mode) showing real legibility failures
that no synthetic test sheet was large or dense enough to expose. This
document lays out the evidence, the confirmed root cause, and a proposed
tiling design — for review before anything here gets built, the same way
`single_sheet_mode_findings.md` preceded single-sheet mode itself.

## 1. The evidence

Comparing pipeline output against the source PDF at full resolution
surfaced three symptoms on the same real sheet:

- A revision tag correctly reading "4" in the source (confirmed
  independently — the sheet's own "BULLETIN 4" issuance/revision table
  extracted it correctly) was misread by `detect_single` as "1".
- A revision cloud containing clearly machine-printed text ("NORTH/SOUTH
  COMMUNICATIONS CONDUIT SERVICE ENTRANCE", "4\" CONDUIT STUBBED INTO
  BOILER ROOM") came back `identity_unresolved` — the pipeline couldn't
  identify text that's legible in the source.
- What looked like single-run over-segmentation (2 real clouds → 5 flagged
  items) turned out, on inspection of the raw `pipeline_steps` trace, to be
  two separate issues layered together: an accumulation bug (fixed
  separately — see `pipeline_notes.md`) plus genuine run-to-run instability
  in `detect_single`'s own output on identical input.

That last point is the important one for this document. Two runs against
the *exact same source bytes* produced meaningfully different results: one
run found 3 raw detections and extracted 4 schedule/legend tables cleanly;
the other found 6 detections and extracted zero tables. The model's own
`image_quality_note` in the second run says directly: *"the fine text
within the cloud... is small and somewhat difficult to fully verify at
native scan quality"* and *"the fine text inside the small callout markers
is too small to read with certainty."* Instability on identical input,
landing right at the edge of legibility, with the model self-reporting
marginal quality — that's the signature of a genuine resolution problem,
not unrelated model variance.

**Second real sheet, after the 2576px fix landed — E-101.2, a full floor
lighting plan (dozens of fixtures) at the identical 50"x36" physical size as
E-101.3.** The cheap resolution fix alone fully closed E-101.3's gaps (see
`pipeline_notes.md`), so this was the test of whether it generalizes. It
didn't: `detect` returned only vague, unquoted descriptions ("cluster of
symbols," "circular symbol cluster" — no fixture types, no tag numbers) and
extracted zero notes/fixture-schedule tables, despite independently
confirming a specific coded note (an after-hours restroom override switch)
is clearly legible on the source PDF. Three resulting `ChangeEvent`s all
landed on an identical 40% confidence — traced through `synthesize_score`
directly and confirmed as real deterministic math, not a fallback path, but
the *inputs* converged because all three items were starved of the same
missing schedule data, itself a symptom of the same extraction failure.
This is the important new data point: **same physical page size, same
2576px resolution, meaningfully worse outcome — driven by content density,
not page size.** §2 and §3a below are revised accordingly.

## 2. Root cause, with real numbers

Confirmed against Anthropic's vision API docs (not assumed): `claude-sonnet-5`
(this pipeline's `DETECT_MODEL`/`REASONING_MODEL`) gets the "high-resolution"
tier — **2576px max long edge, 4784 max visual tokens**. Images larger than
that are resized down server-side regardless of what's sent; there is no
way to push more effective resolution into a single image call than this.

For a 50"-wide sheet, 2576px works out to **~51 DPI-equivalent**. Standard
document-legibility guidance puts reliable small-print reading (6-8pt
architectural callouts, revision-tag digits) at roughly 150-300 DPI. This
sheet's full-page image is running at somewhere between a third and a
sixth of what fine print needs — not a marginal shortfall, a real gap. And
this scales with sheet size: a 34"x22" ARCH D sheet lands around 76 DPI at
the same ceiling; a letter/tabloid sheet is already fine (200-300+ DPI at
2576px). Large-format sheets are the normal case for this application, not
an edge case — E-501, E-101.3, and E-101.2, the three real sheets tested so
far, are all large-format.

**Revised after E-101.2: DPI-from-page-size is a necessary but not
sufficient predictor.** E-101.2 and E-101.3 are the identical physical
size (50"x36") and therefore the identical ~51 DPI-equivalent, yet
E-101.2's extraction was meaningfully worse — dozens of small densely-packed
fixture symbols compete for the same pixel budget a sparser detail sheet
doesn't need to share. A DPI number computed purely from page dimensions
would have scored both sheets identically and predicted no problem for
either, which is wrong for one of them. Content density — how much has to
be resolved per unit area, not just how big the area is — is a second,
independent factor. This matters directly for §3a below.

One easy piece of this was already fixed: this pipeline's own PDF
rasterization was capped at 2000px, *below* the real 2576px ceiling —
throwing away resolution the model could already use for free. That's
corrected now, but only recovers part of the gap (2000px → 2576px is
~30% more linear resolution; the sheet needs 3-6x more than that).

**The only way to close the remaining gap is more images, not a bigger
one.** Splitting the sheet into regions and sending each region as its own
image lets each region hit the same 2576px ceiling against a much smaller
physical area, multiplying effective DPI roughly by the number of tiles
per axis.

## 3. Proposed design

### 3a. Tile only when it's actually needed — revised, not fully solved

Original proposal: compute DPI from physical page size and the 2576px
ceiling, tile below a threshold. **E-101.2 shows this trigger alone isn't
sufficient** — it's the same page size and DPI as E-101.3, which didn't
need tiling, so a size-only trigger would have skipped tiling for E-101.2
too and been wrong. Page-size-DPI is still a legitimate first filter (it
correctly would have skipped tiling for a small/sparse sheet), but a second
signal for content density is needed alongside it.

The real difficulty: density is naturally read off `detect`'s own output
(detection count, table-extraction success, quoted-vs-vague geometry
descriptions — exactly the signals that flagged E-101.2 in the first
place) — but that means the *cheap, low-res, single-image* `detect` call
has to run first before the system can decide whether tiling was needed,
which is a chicken-and-egg problem for anything trying to decide "tile or
not" before spending the tokens. Two directions worth considering, neither
worked out yet: (a) always run the cheap single-image pass first, and
re-run tiled only if that pass's own output looks thin (missing expected
tables, detections with no quoted text, low self-reported image-quality
notes) — a retry-driven trigger, not a pre-computed one; or (b) find a
cheaper proxy for density that doesn't require a full detect call (e.g.
vector-content complexity from the PDF itself — path/text-object count —
for PDF uploads, though this doesn't help raster-only uploads). Needs more
thought before this section can be called settled.

### 3b. Grid with overlap, sized to the DPI target

For a sheet that needs tiling, compute a grid (rows x cols) such that each
tile's physical area, rendered at up to 2576px on its long edge, hits the
target DPI (150 DPI proposed as a starting point — worth validating against
a few more real dense sheets before locking in). For the 50"x36" E-101.3
sheet at a 150 DPI target, that's roughly a 3x3 grid (each tile covering
~17"x12" of physical sheet). Tiles need overlap — proposed 15-20% of tile
size — so a revision cloud sitting on a tile boundary isn't split in half
or missed by both tiles.

### 3c. Per-tile detection, then merge

`detect_single` runs once per tile instead of once per sheet (this is
where the cost multiplies — see §4). Each tile's `SingleSheetDetection.region`
comes back normalized to *that tile's* image; it needs remapping to
full-sheet-normalized coordinates using the tile's known offset/size within
the full sheet (a straightforward linear transform, no model involvement
needed). After remapping, detections from overlapping tile regions need
deduplication — proposed as a geometric step (IOU-style overlap threshold
on remapped regions, not another model call) rather than an LLM
reconciliation pass, since it's a well-defined geometric problem and
shouldn't need judgment. `extracted_tables` needs the same per-tile
treatment: a schedule table dense enough to need tiling for legibility is
exactly the kind of content this whole change exists to help with.

### 3d. Reason/classify/confidence/describe stay mostly as-is

Everything downstream of `detect_single` already treats detections as a
flat list — `classify`, `reason_single`, `confidence`, `describe` don't
need to know a detection came from tile 4 of 9 rather than a single
full-page image, once coordinates are in full-sheet space. `reason_single`
should probably keep seeing the full low-res page image (not every tile)
for holistic/spatial context — "does this align with the room label
elsewhere on the sheet" is a full-page question, not a per-tile one — while
trusting the higher-resolution tile-derived `geometry_description`s for
local detail. This needs validation once built, not assumed correct here.

## 4. Cost and latency impact

Tiling multiplies `detect_single`'s call count by the tile grid size (9x
for the 3x3 case above) — that's the dominant cost increase, since
`classify`/`reason_single`/`describe` stay at roughly today's call count
and `confidence` already scores one call per `ChangeEvent` regardless of
detection source. Concretely, per large sheet: 9 detect calls instead of 1,
run either sequentially (simpler, adds real latency — plausibly 30-60s
more per sheet at today's per-call latency) or in parallel (faster
wall-clock, more complexity, and worth checking Anthropic rate limits
before committing to it). This needs a real dollar-cost estimate against
actual detect_single token usage before implementation, not just the
multiplier — worth pulling from `pipeline_steps` on a few more real runs.

## 5. Open questions before building this

- **Action item, not just a question**: once tiling (or another resolution
  fix) ships, re-run E-101.2 specifically and check whether `detect`
  correctly reasons about the known "cloud tagged '36' removed on the newer
  revision" trap — i.e. recognizes it as old markup being cleaned up, not a
  real change — once it can actually see the sheet clearly. Right now
  `detect` never even surfaced the old cloud as a detection at all (zero
  `present_in: "old_only"` results), so the trap technically didn't fire,
  but there's no way to tell whether that was correct judgment or a missed
  detection that got lucky. This needs re-testing specifically, not
  inferring from other sheets passing.
- How should the tiling trigger actually work, given page-size-only DPI
  isn't sufficient (see revised §3a)? This is now the most load-bearing
  open question in this document.
- Is 150 DPI the right target, or should it be tuned against more real
  dense sheets first? E-101.3 is one data point.
- Sequential vs. parallel tile calls — latency/cost/complexity tradeoff.
- Does `reason_single` genuinely do better with the full-page image for
  context, or would it also benefit from seeing the relevant tile(s)
  directly for a bundled detection group? Untested.
- How should tile size interact with different real sheet sizes (ARCH D
  vs ARCH E vs letter) — a fixed grid size, or computed per-sheet as
  proposed in §3b?
- Geometric dedup (§3c) will have real edge cases (a cloud that's mostly
  in one tile's overlap zone but not fully inside either tile's core
  region) — worth a small spike against real overlapping-boundary cases
  before committing to the approach.

## Appendix: raw evidence

See `docs/pipeline_notes.md`, "Full-page single-image analysis has a hard
resolution ceiling that real large-format sheets exceed" — the
`pipeline_steps` comparison between the two E-101.3 runs (3 vs. 6
detections, 4 vs. 0 tables extracted, the model's own quality-note text)
is recorded there rather than duplicated here.
