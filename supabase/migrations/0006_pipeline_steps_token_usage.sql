-- Real per-step token usage, captured from the Anthropic API's own
-- `response.usage` on every real Claude call (see dre.llm.client.call_structured's
-- usage_sink and dre.pipeline.orchestrator's per-step aggregation). Additive
-- only, nulls on old rows — no attempt is made to backfill historical token
-- counts, which were never recorded anywhere (input_json/output_json hold
-- the parsed pipeline output, not the raw API response, and aren't a
-- reliable source for token counts).

alter table public.pipeline_steps
    add column if not exists input_tokens integer,
    add column if not exists output_tokens integer;
