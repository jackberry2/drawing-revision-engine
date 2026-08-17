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

For a 50"-wide sheet, 2576px works out to **~51 DPI-equivalent** — not a
marginal shortfall from what small print needs, a real gap (see the
legibility derivation below). This scales with sheet size: a 34"x22" ARCH D
sheet lands around 76 DPI at the same ceiling; a letter/tabloid sheet is
already fine (200-300+ DPI at 2576px). Large-format sheets are the normal
case for this application, not an edge case — E-501, E-101.3, and E-101.2,
the three real sheets tested so far, are all large-format.

**Legibility math, derived properly, not cited from an unverified rule of
thumb.** An earlier version of this section cited "150-300 DPI" for small
print as if from a checked source — it wasn't; that number was never
verified the way the Claude API figures above were. Redone from first
principles: reliable text legibility wants roughly 20-25px of rendered
character height. At font size `s` (points), required DPI ≈
`target_px_height × 72 / s`:

| font size | for 20px char height | for 25px char height |
|---|---|---|
| 8pt (top of the architectural-callout range) | 180 DPI | 225 DPI |
| 6pt (bottom of the range) | 240 DPI | 300 DPI |

So the properly-derived target for 6-8pt body text/notes is **roughly
250-300 DPI**, not 150 — 150 DPI is below what this math says is needed
even for the *largest* font in that range. This number is not yet locked
in as a design decision (see §5) — it needs grounding in either sheets'
actual measured font sizes or empirical testing, not just this formula in
isolation; captured here as the corrected derivation, not as a final
answer.

**A separate, distinct finding: the revision-tag-digit failure (the "4"
misread as "1") may not be fixable by *any* uniform DPI target.** Revision
tag digits are visually smaller than the 6-8pt body text this formula
covers — a single numeral crammed inside a small triangle symbol, likely
below the font-size floor this calculation even accounts for. Raising
overall tile resolution to 250-300 DPI may still leave that specific class
of small, symbol-embedded text under-resolved. If so, fixing it isn't a
resolution-target question at all — it's a targeted-handling question
(e.g. rendering detected revision-tag regions at extra magnification
regardless of the tile's general DPI), a genuinely separate design problem
from "what DPI should tiles use," not a special case of it.

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

### 3a. Tile only when it's actually needed — retry-driven, layered under a cheap pre-filter

Original proposal: compute DPI from physical page size and the 2576px
ceiling, tile below a threshold. **E-101.2 shows this trigger alone isn't
sufficient** — it's the same page size and DPI as E-101.3, which didn't
need tiling, so a size-only trigger would have skipped tiling for E-101.2
too and been wrong. Page-size-DPI stays as a cheap first-stage filter (it
correctly rules out tiling for a small/sparse sheet before spending
anything), but a second signal for content density is needed for the
sheets it doesn't rule out.

**Chosen direction: retry-driven, not pre-computed.** Density is read off
`detect`'s own real output — the exact signals that flagged E-101.2 in the
first place — rather than guessed in advance from a proxy. This means the
cheap single-image `detect` pass always runs first (as it does today); its
output is then evaluated against a concrete, mechanical rule, and only
escalates to tiling on a retry if the rule fires. Sparse sheets like
E-101.3 pay nothing extra. Considered and deprioritized: a self-reported
"can you read this" pre-check call, and a pure geometric/CV density proxy
computed before any Claude call — see the trigger-options discussion this
doc's history is built on (not reproduced here) for why evidence-based won
out over prediction-based.

**The concrete rule**, evaluated against `detect`/`detect_single`'s raw
output (schema-safe across both modes — `flagged_by` only exists on
`SingleSheetDetection`, not `RawDetection`, so the rule deliberately avoids
depending on it, a real bug caught while validating this rule against
E-101.2's actual two-image-mode trace):

```
distinct_extracted_table_titles = number of distinct `title` values
                                   across extracted_tables
quoted_fraction = (detections whose geometry_description contains a
                    quoted substring) / (total detection count)

IF detection_count < 3:
    # Too few detections to trust quoted_fraction as a signal — a sparse-
    # but-legitimate sheet could swing from 0/1 to 1/1 on a single data
    # point. Fall back to the stronger, stricter all-tables-missing case.
    TRIGGER = (distinct_extracted_table_titles == 0)
ELSE:
    TRIGGER = (distinct_extracted_table_titles <= 1) AND (quoted_fraction < 0.5)
```

The `<=1` threshold (not `==0`) in the main branch is deliberate: E-101.2
*did* find one table — the issuance/revision list, the easiest one on any
sheet like this (large print, fixed corner position, low information
density) — while missing every substantive content table (notes, legend).
Requiring strictly zero tables would have missed this real case.

**Validated against real stored traces, not hypothetically**:

| metric | E-101.3 (good, post-2576px-fix) | E-101.2 (degraded) |
|---|---|---|
| `detection_count` | 4 | 5 |
| `distinct_extracted_table_titles` | 4 | 1 |
| `quoted_fraction` | 0.5 (2/4) | 0.2 (1/5) |
| rule branch | `>=3`, main | `>=3`, main |
| `TRIGGER` | `4<=1` False → **off** | `1<=1` and `0.2<0.5` both True → **fires** |

Correctly stays off for the sheet that didn't need tiling and fires for
the one that did, using the exact stored numbers.

**This is directionally validated, not fully validated — see §5.** Two
data points is enough to catch a real schema bug (which it did) and
confirm the signal's direction, not enough to trust the specific numeric
thresholds against edge cases the sample doesn't cover.

### 3b. Grid with overlap, sized to the DPI target

**Per-tile pixel ceiling, corrected: ~1900px per side for a square tile,
not 2576px.** Claude's high-resolution tier has two *simultaneous*
constraints, not one: 2576px max long edge, **and** 4784 max visual tokens
(28×28px patches ≈ 3,750,656 px² total). 2576px is only reachable at an
extreme aspect ratio (Anthropic's own documented example downsizes a 16:9
image to 2576×1449, not 2576×2576) — a square tile hitting 2576 on both
sides would need ~8,464 tokens, 77% over budget. For a square tile the real
ceiling is `√3,750,656 ≈ 1936.7px`. Since the token formula uses
`⌈width/28⌉ × ⌈height/28⌉` (ceiling, not exact division), a render sized
right at that boundary risks tipping over after rounding — so the safe
working number is **1900px**, not the theoretical 1936.7, leaving real
margin rather than an assumption that never gets tested until it fails.

Tile grid sizing, as a function of whatever DPI target §5 eventually
settles (deliberately not hardcoded here):

```
tile_edge_px = 1900              # safe square-tile ceiling, margin included
core_px = tile_edge_px * (1 - overlap_fraction)   # overlap_fraction = 0.15-0.20

cols = ceil((sheet_width_in  * target_dpi) / core_px)
rows = ceil((sheet_height_in * target_dpi) / core_px)
```

Tiles need overlap — proposed 15-20% of tile size — so a revision cloud
sitting on a tile boundary isn't split in half or missed by both tiles.

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

### 3e. Duration hint to Lovable — a real requirement, minimal scope for now

A retry-driven trigger means a dense sheet's request gets meaningfully
slower than today's baseline, synchronously, within the same request/
response cycle Lovable calls today — the exact class of problem that
already caused a real incident this session (the cold-start "Can't reach
the server" investigation, where an unexpectedly-long/uncertain request
duration was the root confusion). Shipping tiling without addressing this
reintroduces that risk deliberately, not accidentally, so it belongs in
scope now rather than as a follow-up once someone reports a "hang."

Minimal version, ships alongside tiling: reuse 3a's cheap page-size
pre-filter (already computed before any Claude call) to return an
expected-duration hint to Lovable at submission time — before knowing
whether the retry-driven trigger will actually fire, just from "this sheet
is large enough that tiling is plausible." Cheap, no new architecture.

**Explicitly out of scope for now, a separate later decision**: a real
async/polling API contract (return immediately, let Lovable poll status)
that would remove the timeout risk structurally instead of just warning
about it. That's a real cross-team API contract change and needs its own
decision, not something to fold into this design by default — tracked as
its own item in §5.

### 3f. Parallel tile execution — required, and not automatic

**Sequential vs. parallel changes latency only, not cost** — same call
count, same tokens, same dollars either way; this is purely a wall-clock
tradeoff, not a cost one. At tile counts in the 20-70 range (§3b, once a
real DPI target is set), sequential execution is not a reasonable fallback
— it would add many minutes of pure sequential wait on top of today's
baseline, compounding directly into §3e's timeout-risk concern rather than
just being a latency nuisance. **Parallel is required, not optional, at
this scale.**

Two things checked directly rather than assumed:

- **Anthropic rate limits — verified against this account's real headers,
  not guessed**: `anthropic-ratelimit-requests-limit: 10000`,
  `anthropic-ratelimit-tokens-limit: 12000000` (input) — 20-70 simultaneous
  tile calls is nowhere near either ceiling. Not a constraint worth
  designing around.
- **This codebase cannot do this today without a real code change — not
  automatic.** `llm/client.py` uses the fully synchronous `anthropic.Anthropic`
  client (no `async def` anywhere in the file), and `render.yaml`'s start
  command (`uvicorn dre.api:app --host 0.0.0.0 --port $PORT`) has no
  `--workers` flag — a single worker process. A synchronous Claude call
  blocks that process for its full duration regardless of design intent;
  "parallel" isn't achieved by deciding it should be. The natural fix given
  the existing synchronous style is a bounded `ThreadPoolExecutor` around
  the existing `call_structured` calls (Python threads release the GIL
  during network I/O wait, so this works without an async rewrite) — with
  a bounded worker count (e.g. 5-8 concurrent, not all 20-70 at once), both
  to be a reasonable API citizen and because concurrent-connection behavior
  on a small Render instance hasn't been tested, not confirmed safe by
  assumption.

This is a real implementation requirement to scope into the build, not a
design preference noted in passing.

## 4. Cost and latency impact

**Revised for the retry-driven design (§3a) — cost isn't a flat multiplier,
it's bimodal.** The original framing ("9x detect calls for every large
sheet") assumed tiling always ran once a sheet passed a size threshold.
Under the retry-driven trigger, cost is per-*outcome*, not per-*sheet*:

- **Sheets the §3a rule doesn't fire on** (e.g. E-101.3): pay exactly
  today's cost. One `detect`/`detect_single` call, nothing else changes.
- **Sheets the rule fires on** (e.g. E-101.2): pay today's cost *plus* the
  tiled retry — the "wasted" first pass, plus the tile grid's call count
  (§3b, sized to whatever DPI target §5 eventually settles — likely well
  above the original "9x" estimate once §2's corrected legibility math and
  §3b's corrected per-tile ceiling are both accounted for), run in parallel
  per §3f's bounded-`ThreadPoolExecutor` requirement rather than
  sequentially.

**The real number this section needs is the trigger rate — how often real
sheets actually fire the rule — and that's currently unknown, not a
placeholder worth guessing.** Total cost/latency impact across a real
population is `(sheets that don't fire × baseline cost) + (sheets that
fire × (baseline + tiled cost))`, and the whole equation is dominated by
that unknown fraction. Guessing a rate here would make this section look
more resolved than it is.

**This is no longer blocked on building tiling to start measuring.** §3a's
trigger rule is now computed and logged passively on every real analysis
run (`dre.tiling_trigger.compute_tiling_trigger_diagnostics`, persisted to
`pipeline_runs.tiling_trigger_diagnostics` via migration
`0004_tiling_trigger_diagnostics.sql`), regardless of whether tiling
itself exists yet. Once enough real sheets have run through it, the
trigger rate becomes a real observed number instead of a guess, and this
section can be filled in properly rather than estimated from 2 data
points.

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
- **The §3a trigger rule is directionally validated, not fully
  validated — explicitly not settled.** Two data points (one good sheet,
  one degraded sheet) confirmed the signal's direction and caught a real
  bug (`flagged_by` doesn't exist on `RawDetection`, only on
  `SingleSheetDetection` — the rule was redesigned to not depend on it).
  That's not the same as validating the specific thresholds (`<=1` table
  titles, `<0.5` quoted_fraction, `<3` detection-count floor) against edge
  cases the sample doesn't cover — e.g. a sheet with 3-5 detections that's
  genuinely sparse (nothing worth quoting) rather than illegible could
  still trip the quoted_fraction condition as a false positive. Now
  passively collected on every real run (§4), so this stops being blocked
  on manually running more sheets through by hand — but the thresholds
  themselves haven't been re-examined against any data beyond the original
  two yet, so "not settled" still holds until that review happens.
- **Async/polling API contract — explicitly deferred, not decided
  here.** §3e's duration-hint is the minimal version shipping with tiling;
  a real fix that removes the timeout risk structurally (return
  immediately, Lovable polls status) is real cross-team scope and needs
  its own separate decision, not something to fold into this design by
  default.
- **The DPI target is explicitly open, not settled at any number.** §2's
  legibility math (redone properly, not from an unverified citation) says
  roughly 250-300 DPI for 6-8pt body text — well above the 150 DPI this
  document previously proposed, which didn't survive the actual derivation.
  Separately, revision-tag digits are likely smaller than that 6-8pt floor
  and may need targeted higher-resolution handling regardless of whatever
  general tile DPI gets chosen — a distinct design question, not a special
  case of picking the right number. Two concrete paths to actually
  resolving the number, neither taken yet: (a) extract real font sizes
  from the source PDFs directly (PyMuPDF can read this) and compute DPI
  from measured sizes instead of an assumed 6-8pt range; (b) empirically
  test candidate DPI values against a real tiled run of E-101.2/E-101.3
  and check whether the two known failures (the digit, the coded note)
  actually resolve. Whichever number is eventually chosen, it directly
  determines §3b's tile count and therefore §4's real cost.
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
