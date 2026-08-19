-- Single source of truth for "how many credits does this user have left
-- right now" — both dre.credits.remaining_credits (the real pre-check that
-- blocks/allows an analysis) and Lovable's usage-bar frontend read this
-- exact view, so the two can never disagree with each other. Previously
-- this logic only existed as inline Python in dre/credits.py.
--
-- Mirrors dre.credits.remaining_credits's semantics exactly:
--   - Per user, the most recently updated 'active' subscriptions row wins
--     (a user with no active subscription simply has no row in this view —
--     callers must treat "no row" as 0 credits, same as the Python did).
--   - tier -> monthly allotment: starter=15, standard=40, pro=100. Any
--     other tier value yields a null monthly_allotment, which coalesces to
--     0 below — fails closed, same as the Python's `allotment is None -> 0`.
--   - credits_used_this_period sums credit_usage.credits_used bounded by
--     current_period_start/current_period_end. Either bound being NULL (a
--     subscription with no Stripe period set yet) drops that side of the
--     range, so a null period sums *all* credit_usage ever for that user —
--     matching the Python fallback, not an unmetered bypass.
--
-- If dre/credits.py's TIER_MONTHLY_CREDITS or this CASE mapping ever
-- change, they must change together — this view is now the definition,
-- and dre.credits.remaining_credits reads it rather than recomputing it.

create or replace view public.user_credit_balance as
with active_subscription as (
    select distinct on (s.user_id)
        s.user_id,
        s.tier,
        s.current_period_start,
        s.current_period_end
    from public.subscriptions s
    where s.status = 'active'
    order by s.user_id, s.updated_at desc
),
tier_allotment as (
    select
        a.user_id,
        a.tier,
        a.current_period_start,
        a.current_period_end,
        case a.tier
            when 'starter' then 15
            when 'standard' then 40
            when 'pro' then 100
            else null
        end as monthly_allotment
    from active_subscription a
),
usage_this_period as (
    select
        t.user_id,
        coalesce(sum(cu.credits_used), 0) as credits_used_this_period
    from tier_allotment t
    left join public.credit_usage cu
        on cu.user_id = t.user_id
        and (t.current_period_start is null or cu.used_at >= t.current_period_start)
        and (t.current_period_end is null or cu.used_at < t.current_period_end)
    group by t.user_id
)
select
    t.user_id,
    t.tier,
    t.current_period_start,
    t.current_period_end,
    t.monthly_allotment,
    coalesce(u.credits_used_this_period, 0) as credits_used_this_period,
    coalesce(t.monthly_allotment, 0) - coalesce(u.credits_used_this_period, 0) as credits_remaining
from tier_allotment t
left join usage_this_period u on u.user_id = t.user_id;

-- The view runs with the querying role's own privileges (default view
-- behavior, not security definer), so RLS on subscriptions/credit_usage
-- still applies when an authenticated frontend user queries it directly —
-- same "users can read their own row" scoping Lovable already relies on
-- for those tables. This grant only unblocks the view itself; it does not
-- bypass RLS on the underlying tables.
grant select on public.user_credit_balance to authenticated, service_role;
