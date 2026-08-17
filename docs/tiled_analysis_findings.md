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

**Third data point — the first real tiled test, against the actual failure
that started this document, not a synthetic check.** Using the tuning
harness (`dre tile-detect`, §3b), ran the real `detect_single` stage
against real E-101.3 tiles at 150 DPI, targeting the exact revision-cloud
cluster from the original evidence above. Both original failures resolved:

- The revision tag digit — misread as "1" at every full-page resolution
  tested, including the corrected 2576px ceiling — read correctly as
  **"4"** three independent times across two tiles: *"Red triangular
  revision tag containing number '4'"*; *"a red numbered triangle tag
  '4' near the top of the cloud"*; *"Red triangular tag containing the
  number '4'"*.
- The communications-conduit label text, previously `identity_unresolved`,
  now reads correctly and completely: *"leader lines pointing to text
  labels 'NORTH COMMUNICATIONS CONDUIT SERVICE ENTRANCE' and 'SOUTH
  COMMUNICATIONS CONDUIT SERVICE ENTRANCE'"*.

This is real, not a legibility check on a rendered image — the actual
production `DetectSingleStep` produced this output, the same stage a real
tiled analysis would run. Scoped precisely: this is evidence for 150 DPI
resolving *E-101.3's specific failures*, not a general validation of 150
DPI for tiling overall — see §5.

**The same test also surfaced a real, not hypothetical, instance of the
overlap-margin risk logged in §5.** The actual revision-cloud cluster
spans a corner where four tiles meet; the best single tile covered only
67.9% of it. This showed up directly in the results — one tile's
detections never mention the communications-conduit text at all, only the
other tile's do. The resolution problem is solved; reassembling one
cluster's detections split across multiple tiles into a single coherent
finding (§3c) is now a confirmed real requirement for a usable end-to-end
system, not a theoretical edge case anticipated in advance.

**Fourth data point — the same test repeated against E-101.2, the sheet
whose failure mode §2 established was different (density-driven, not a
resolution problem the same way E-101.3's digit was).** Both of E-101.2's
real known failures resolve at 150 DPI too:

- The coded/general notes tables — never extracted at all across three
  real production runs — extracted completely and correctly: 8 general
  notes (A-H), 14 coded notes (1-14), including the exact text
  independently confirmed against the source PDF: *"PROVIDE AFTER HOURS
  RESTROOM OVERRIDE SWITCH - REFER TO LIGHTING CONTROL DETAIL ON SHEET
  E201"* (coded note 5).
- The fixture-zone revision markup — described only as *"cluster of
  symbols near room E100/E124 area"* in every real production run —
  resolves into specific, correct content: fixture types (*"R2, R2 EM,
  R2 EM"*, *"R6"*), an *"EX1"* exit-sign symbol, and correctly-read *"B5"*
  revision tags, read twice and matching the source. Getting there
  required a real correction, not a clean first pass: the region **three
  independent real production runs agreed on** turned out to be wrong by
  roughly 10 physical inches — the tile rendered from it contained no
  revision markup at all. Found the actual location by scanning the
  full-page render for real red pixels, not by guessing again, and
  verified it by viewing the cropped region directly before trusting it.

**150 DPI is now validated against 2/2 real sheets tested, independently
confirmed on each — E-101.3's digit and label text, E-101.2's coded note
and fixture zone.** Still worth broadening as more real sheets run through
the tuning harness over time (two sheets is not an exhaustive population),
but this has moved past a starting point for tuning into real evidence —
see §5.

**The bounding-box finding above is not a side note — it's a direct
constraint on §3c's design space, addressed explicitly there.** Three
independent runs agreeing with each other did not make the region
correct. Any merge/dedup approach that treats detect's self-reported
tile-local coordinates as ground truth needs to account for this
directly, not assume agreement across runs is a sufficient reliability
check.

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

**Legibility math, attempted with real measured data — and the theory it
was built on didn't survive contact with real behavior.** An earlier
version of this section cited "150-300 DPI" for small print, then
"250-300 DPI" derived from an assumed 6-8pt font size — neither was
grounded in this sheet's actual text. Went to get the real number:

*Path (a), real font-size extraction via PyMuPDF, is closed —
attempted and found inapplicable, not merely unattempted.* Both PDFs have
**zero extractable text objects**: `page.get_text()` returns nothing.
Every character is flattened to vector path outlines (curves), not real
text runs (confirmed via `page.get_drawings()`: 14,747 / 21,182 raw vector
paths, zero embedded raster images). There is no font metadata anywhere in
these files to extract.

*Measured real glyph geometry instead* — clustering nearby vector paths
into connected blobs and measuring rendered bounding-box heights directly,
arguably better ground truth than a nominal font size would be anyway. This
has real limits: whole-page clustering is dominated by continuous connected
linework (walls, conduits merge huge areas into a few giant blobs via
touching lines), and clustering can't distinguish "digit" from "the
triangle symbol outline surrounding it" without real shape recognition —
out of scope here. One clean measurement did come out of it: E-101.3's
comms-conduit label text (the actual text read correctly in the good
run) measures **~15pt** — nearly double the 6-8pt this document had
assumed without measuring anything.

*Plugging that in gives a number — 96-120 DPI (20-25px targets) — but the
formula itself is now directly contradicted, not just imprecise.* That
same 15pt text was already read correctly by Claude at the sheet's actual
render resolution, ~51 DPI: `51 × 15/72 ≈ 10.6px` effective character
height. That's well under the 20-25px "reliable legibility" floor the
whole formula rests on — a floor borrowed from classical OCR-engine
guidance, with no basis for assuming it transfers to how a multimodal
vision-language model actually reads rendered text. Real behavior on this
exact sheet already contradicts it. The formula isn't just missing a
better input number; its own premise doesn't hold, so it can't be trusted
to produce a validated target no matter what font size goes into it.

**A separate, distinct finding — the revision-tag-digit failure (the "4"
misread as "1") remains unresolved, but now because direct measurement
failed to resolve it, not because a calculation predicted it.** Attempted
to isolate the digit's real size the same way; couldn't. One tag region
(E-101.3's) contained **zero real vector content** at `detect`'s own
reported bounding box — its self-reported regions aren't precise enough to
use as crop coordinates for this. Where a tag region did have content
(E-101.2's), the measured blob (13.5-22pt) almost certainly fuses the
triangle symbol's outline with the digit inside it, so it's an
*overestimate* of the true digit size, not a real answer. The original
finding — that small, symbol-embedded numerals may need handling separate
from general tile resolution — still stands; it just stands unmeasured
rather than calculated.

**Conclusion: no single DPI number is defensible from theory alone right
now, for two independent reasons that both point the same direction** —
not just the revision-tag case being unresolved. (1) The digit's real size
still can't be measured cleanly. (2) The formula that would convert *any*
measured size into a DPI target has already been shown to disagree with
real observed behavior. Path (a) (font-size extraction) is closed for
these files. **Empirical testing — path (b) — is now the only remaining
route to a real number**, not one of two options to weigh: run candidate
DPI values against a real tiled pass over E-101.2/E-101.3 and check
directly whether the two known failures (the digit, the coded note)
actually resolve, rather than continuing to reason forward from a formula
whose own assumptions don't hold for this technology.

**Interim default for building against, at the time this was written not a
validated answer**: something in the **100-150 DPI** range — roughly
double the ~51 DPI that already worked for the one text category directly
confirmed readable — as a reasonable starting point for tuning once a
tiling mechanism exists to test against. **Update: the tuning harness now
exists and this has been tested — 150 DPI is validated against 2/2 real
sheets (§1, §5), not just an interim guess anymore.** Reasoning trail kept
here as-is rather than rewritten, since it's how the number was actually
reached, not because it's still the current state.

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

**Build this as a tuning harness from day one, not a shipped fixed
default.** §2 closed off the theoretical path to a validated DPI number —
empirical testing against real sheets is the only route left, which means
the first implementation's job is to make that testing cheap and repeatable,
not to lock in a guess. Concretely: `target_dpi` should be a parameter the
tiling entrypoint takes directly (CLI flag or equivalent), not a buried
constant, so re-running E-101.2/E-101.3 at several candidate values (e.g.
100, 150 DPI to start, informed by §2's interim range) and checking
directly whether the known failures resolve — the digit reading correctly,
the coded note getting extracted — is a fast loop, not a redeploy-and-wait
cycle each time.

Tiles need overlap — proposed 15-20% of tile size — so a revision cloud
sitting on a tile boundary isn't split in half or missed by both tiles.

### 3c. Per-tile detection, then merge — chosen and built (v1, real known limits)

`detect_single` runs once per tile instead of once per sheet (this is
where the cost multiplies — see §4). `extracted_tables` needs the same
per-tile treatment: a schedule table dense enough to need tiling for
legibility is exactly the kind of content this whole change exists to
help with.

**Hard constraint on the design space, confirmed by real evidence, not a
theoretical caveat: detect's self-reported regions cannot be trusted as
ground truth for merge/dedup, and agreement across independent runs does
not establish trust either.** §1's E-101.2 test hit this directly — three
separate real production runs independently agreed on a region for the
fixture-zone revision cloud, and that region was wrong by roughly 10
physical inches; the tile rendered from it contained no revision markup
at all. The actual location was only found by scanning the rendered image
for real red pixels and verifying the crop by eye, not by trusting the
model's own coordinates, however consistently reported. The original
proposal below — remap each tile-local `region` to full-sheet coordinates
via linear transform, then geometrically deduplicate overlapping
detections — was written before this was known, and a purely
coordinate-based approach inherits this unreliability directly: if a
detection's self-reported region is wrong, remapping it doesn't fix that,
and a geometric merge step built on wrong coordinates can produce
confident, wrong answers just as easily as no merge step at all.

**Chosen and built: tile-adjacency-scoped candidates + content-based
confirmation.** Three directions were sketched (pure geometric IOU;
content-based dedup; this hybrid) with real tradeoffs weighed before
picking — chosen specifically because the failure case that sank pure
content-matching alone (two distinct real revision clouds sharing a
generic "B5" tag, in different rooms with different fixture types — a
real case in E-101.2's data, not hypothetical) is exactly what
tile-adjacency scoping was free to guard against, using only
`compute_tile_grid`'s own known-correct grid structure — never a
detection's self-reported region — to decide which detections are even
worth comparing.

Implementation (`dre.pipeline.tile_merge`): `find_merge_candidates`
restricts comparison to detection pairs from the same-or-adjacent tile
(the only spatial fact trusted); `likely_same_element` then requires at
least 2 shared distinctive content tokens (extracted from each
detection's own description text — label-style codes like `R2`/`E124`
weighted as a strong signal, bare quoted short numbers like `'4'` treated
as weak, since bulletin/tag numbers are systematically reused across
unrelated real elements by design, unlike equipment/room codes) —
`group_merge_candidates` turns the resulting pairs into final clusters via
union-find. A single shared tag is deliberately not sufficient on its
own — validated directly against the real E-101.2 case: `_QUOTED_SHORT_TOKEN_RE`
and `_LABEL_CODE_RE` correctly keep the two real "B5" clouds in separate
groups in `tests/test_tile_merge.py`, using their actual detection text,
not synthetic examples.

**Two honest limitations, found by testing the design's own assumptions
against real data, not asserted away:**
- The same real E-101.3 cluster that motivated logging the overlap-margin
  risk in §5 — confirmed the same physical revision cloud by directly
  viewing both tiles — does *not* get merged by this design. The only
  content the two tiles' detections share is the generic "4" bulletin-tag
  number, which `MIN_SHARED_TOKENS=2` is specifically built not to trust
  alone (for good reason — see above). A real, tested gap, not a
  theoretical one.
- The same threshold also means a revision cloud and its own tag,
  captured in different tiles, often won't merge either — tags are
  typically terse (e.g. *"Red triangular tag containing text 'B5'
  pointing at the bottom edge..."*) and don't repeat the cloud's other
  distinguishing details, so they frequently share only the one tag
  token with their own cloud — the same weak signal the design correctly
  distrusts in the cross-element case. Caught by testing an assumption
  that turned out wrong (a "cloud obviously matches its own tag" sanity
  check), not assumed correct going in.

Both failures land in the safer direction deliberately favored throughout
this design: under-merging leaves both fragments individually reported,
rather than an over-merge silently conflating two distinct real elements
into one. Not a finished solution — a real v1 with real, tested,
documented edges, consistent with how every other number in this document
has been treated. No genuine positive full-pipeline merge has been
confirmed against real data yet (today's real cross-tile cases were both
negative/miss cases) — worth specifically looking for as more real sheets
run through the tuning harness.

**Calibration data collection now exists (`compute_merge_diagnostics`,
`dre tile-detect-grid`).** Every adjacent cross-tile pair the grid produces
gets evaluated and logged — including pairs that don't clear
`MIN_SHARED_TOKENS`, with their actual shared-token count — not just the
pairs that already passed. This is what makes the "1 known-good, 1
known-miss" baseline in §5 an actual accumulating dataset rather than two
one-off manual observations: every future run through the harness adds
more real evaluated pairs (matched and unmatched) to tune the threshold
against later. As of this writing it's only reachable via the harness, not
production traffic — see §5 for why, and what production wiring would take.

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
- **The DPI target: 150 DPI, validated against 2/2 real sheets tested —
  not a guess, not an interim placeholder anymore.** §2 attempted both
  paths this document originally proposed for reaching this number. Path
  (a), real font-size extraction via PyMuPDF, closed as inapplicable: both
  PDFs are text-free (all characters flattened to vector outlines),
  confirmed by direct inspection, not assumed. Path (b), empirical
  testing, is what actually delivered the answer: the tuning harness ran
  the real `detect_single` stage against real tiles at 150 DPI on both
  sheets tested so far, and it resolved every known failure independently
  confirmed on each — E-101.3's revision-tag digit (misread as "1" at
  every full-page resolution including the corrected 2576px ceiling, read
  correctly as "4" three times across two tiles) and its
  communications-conduit label text; E-101.2's coded/general notes tables
  (never extracted at all across three real production runs, extracted
  completely and correctly, including the exact text of coded note 5) and
  its fixture-zone revision markup (vague in every production run, precise
  and correct once tested against the actual — not the self-reported —
  location; see §3c). Two sheets is real evidence, not an exhaustive
  population — worth broadening as more real sheets run through the
  harness over time, and still worth treating 150 DPI as revisable if a
  future sheet's failure mode doesn't resolve at it — but this is no
  longer a starting point being tuned toward an answer; it's the answer,
  pending contrary evidence. This number directly determines §3b's tile
  count and therefore §4's real cost.
- Does `reason_single` genuinely do better with the full-page image for
  context, or would it also benefit from seeing the relevant tile(s)
  directly for a bundled detection group? Untested.
- How should tile size interact with different real sheet sizes (ARCH D
  vs ARCH E vs letter) — a fixed grid size, or computed per-sheet as
  proposed in §3b?
- **Merge/dedup (§3c) is built (v1) with a specific, logged calibration
  baseline — deliberately not tuned yet.** `MIN_SHARED_TOKENS=2` is
  currently verified on exactly one data point on each side: **1
  known-good** (E-101.2's two distinct "B5"-tagged clouds correctly kept
  separate — the real false-positive risk this threshold exists to guard
  against) and **1 known-miss** (E-101.3's real cluster, confirmed the
  same physical cloud by directly viewing both tiles, doesn't merge — the
  shared "4" bulletin-tag number alone isn't trusted, for the same reason
  the E-101.2 case shouldn't have been). Both land in the safer direction
  (under-merging, nothing silently conflated), but one hit on each side is
  not enough signal to move the number responsibly — the same reasoning
  that kept the DPI target from being locked in off a single sheet. Not
  adjusting `MIN_SHARED_TOKENS` until more real evidence exists to tune it
  against. **This is no longer blocked on manual one-off checks either**:
  `compute_merge_diagnostics` (§3c) now logs every adjacent cross-tile pair
  evaluated by the tuning harness — matched or not, with its actual
  shared-token count — the same passive-accumulation pattern §3a's trigger
  rule already uses in production. It's reachable today via `dre
  tile-detect-grid` against real sheets; it is NOT yet reachable from real
  production traffic, because no production tiled flow exists yet to
  generate pairs from (see the end-to-end status note below).
- **Residual overlap-margin risk, found while verifying real rendered
  tiles by eye (not from the coverage-math tests, which can't catch this):
  a single note wider than the overlap margin could in principle be split
  across two tiles with neither containing it whole.** Directly checked
  the real 15%-overlap case (E-101.3, row 2 col 0/1 at 150 DPI) — several
  notes cut off mid-word at one tile's edge were recovered complete in the
  overlapping neighbor, so the margin was wide enough for every note on
  that real sheet. Not observed failing, but not ruled out either: those
  notes happened to be short (2-4 lines, narrow). A longer single-line
  note, or a smaller overlap fraction/higher DPI combination that shrinks
  the physical overlap margin, could plausibly still split content with
  neither tile whole. Worth a real check against a sheet with wider note
  text before treating 15% as sufficient in general, not just for this
  case. (Distinct from the dedup/merge finding above: this is about
  whether a margin is wide enough to fully *contain* an element at all;
  the dedup finding is about *recombining* an element that legitimately
  spans multiple tiles even when each tile's portion is intact. Both are
  real, both point at §3c, but they're different failure mechanisms.)
- **End-to-end status: what's left before this is a usable feature, not
  just validated pieces.** Three honestly different buckets:
  - **Solid and tested, ready to build on:** grid math (`tiling.py`, pure
    and tested), per-tile rasterization (`imaging.py`, tested and
    eye-verified against a real rendered tile — see §3b/§1), the 150 DPI
    target (validated 2/2 real sheets, no longer interim — see above),
    §3a's trigger rule (passively logged on every real production run
    today, via `pipeline_runs.tiling_trigger_diagnostics`), and §3c's
    merge logic v1 (tested against real E-101.2/E-101.3 data, known-safe
    direction, now with its own passive calibration-diagnostics logging
    via the harness).
  - **Documented requirements, not yet built:** §3e's duration-hint to
    Lovable (needed once real requests can take tile-count-multiplied
    latency, currently undocumented to the caller) and §3f's
    `ThreadPoolExecutor` for parallel tile calls (`llm/client.py` is fully
    synchronous today — sequential tile calls work in the harness but
    would multiply real request latency by tile count in production
    without this).
  - **Genuine gap, not yet designed at all:** there is currently no
    orchestration code connecting the pieces above into the real request
    path. `service.py`/`analyze_request`/`PipelineContext` have zero
    awareness of tiling — nothing reads §3a's logged `would_trigger` value
    to actually branch into a tiled flow instead of the existing
    single-image call. And `extracted_tables` merging across tiles (flagged
    as needed in §3c's opening paragraph) has no design sketched and no
    code written — only per-detection merge/dedup has been built; a
    schedule table split across tiles has no handling yet.

  **So: not yet a small "wire it in" step.** What exists is a complete,
  independently-tested toolkit (grid, rasterization, trigger signal, merge
  logic) plus one production integration point already live (§3a's passive
  trigger logging). Turning that into an actual tiled analysis path still
  needs: (1) branching logic in `service.py` that checks the trigger signal
  and, when it fires, runs the tile grid → per-tile `detect_single` → merge
  sequence instead of the single-image path; (2) parallel tile execution
  (§3f) so that path doesn't multiply latency unacceptably; (3) the
  duration-hint contract change (§3e) so Lovable isn't caught by a timeout
  when it does; (4) `extracted_tables` cross-tile merging, currently
  undesigned. None of these four are large individually, but none of them
  exist yet either — the honest answer to "is a piece left" is yes, the
  orchestration layer itself, not a finishing touch on what's built.

## Appendix: raw evidence

See `docs/pipeline_notes.md`, "Full-page single-image analysis has a hard
resolution ceiling that real large-format sheets exceed" — the
`pipeline_steps` comparison between the two E-101.3 runs (3 vs. 6
detections, 4 vs. 0 tables extracted, the model's own quality-note text)
is recorded there rather than duplicated here.
