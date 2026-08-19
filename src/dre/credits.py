"""Credit accounting against real Stripe-backed subscriptions.

`subscriptions` and `credit_usage` are Lovable-managed tables that already
exist in the real Supabase project (confirmed via the deployed project's
PostgREST OpenAPI schema — both were empty at the time this was built, since
Stripe wiring is brand new, so there was no real row to sample). This module
only reads/writes them; it doesn't own their schema or the Stripe wiring.

Real, confirmed-live schema:
  subscriptions(id, user_id, stripe_customer_id, stripe_subscription_id,
    tier[default 'starter'], status[default 'active'], current_period_start,
    current_period_end, created_at, price_id, product_id,
    cancel_at_period_end, environment, updated_at)
  credit_usage(id, user_id, analysis_request_id -> analysis_requests.id,
    credits_used[default 1], used_at)

The balance calculation itself (tier -> monthly allotment, minus
current-period credit_usage) lives in exactly one place: the real,
pre-existing `public.get_credit_balance(_user_id)` Postgres RPC. This
service's pre-check and Lovable's frontend usage bar both call that same
RPC directly, so they can never disagree about a user's balance.

That RPC was NOT built by this module — it (along with `consume_credits`,
`refund_credits`, `tier_credit_allotment`) already existed in the real
project, built independently as part of the same Stripe wiring, and was
only discovered via real introspection (migration 0008's diagnostic
functions) after this module's first version had already built an inline
Python computation and then a Postgres view (migration
0007_user_credit_balance_view.sql, since dropped by migration
0009 — both computed the identical real numbers, but having two
implementations was exactly the drift risk this was meant to avoid).

`consume_credits` can't be used from this backend: it's `SECURITY DEFINER`
but hardcodes `auth.uid()` with no override, which is NULL under this
service's service-role auth — every call would silently return `false`.
It also refuses to consume credits that would exceed the remaining
balance, which conflicts with the overage-allowed design below. So
deduction here stays a plain insert into credit_usage (see record_usage),
and the eligibility rule consume_credits enforces internally
(active/trialing/past_due, or canceled-but-still-within-period) is
re-implemented in `_is_eligible` below, since get_credit_balance itself
returns a balance for a row of any status without gating on it.

Tier names are lowercase ('starter'/'standard'/'pro'), matching the
`subscriptions.tier` column's own default value.
"""

from __future__ import annotations

from datetime import datetime, timezone

from dre.supa import repository as repo

# Mirrors consume_credits' real eligibility gate exactly (pulled via real
# introspection, not guessed): active/trialing/past_due are always OK;
# canceled is OK only while still inside its paid-through period; anything
# else (incomplete, unpaid, none, ...) is not.
_ALWAYS_ELIGIBLE_STATUSES = {"active", "trialing", "past_due"}

# Only "tiled" actually ran the more expensive per-tile grid. Every other
# real tiling_path value ("single_pass", "single_pass_no_pdf_source",
# "tiled_failed_fallback", "not_applicable" for two_image mode) did
# single-pass-equivalent real API work, so they're all billed as
# DEFAULT_CREDIT_COST — see dre.pipeline.tiled_detect.TilingOutcome.path and
# dre.service's own tiling_path assignment for the real value set.
TILED_CREDIT_COST = 5
DEFAULT_CREDIT_COST = 1


class InsufficientCreditsError(Exception):
    def __init__(self, user_id: str, remaining: int):
        self.user_id = user_id
        self.remaining = remaining
        super().__init__(
            f"user {user_id} has {remaining} credit(s) remaining this billing "
            "period — insufficient for any analysis"
        )


def credits_for_tiling_path(tiling_path: str) -> int:
    return TILED_CREDIT_COST if tiling_path == "tiled" else DEFAULT_CREDIT_COST


def _is_eligible(balance: dict) -> bool:
    status = balance.get("status")
    if status in _ALWAYS_ELIGIBLE_STATUSES:
        return True
    if status == "canceled":
        period_end = balance.get("current_period_end")
        if period_end is None:
            return False
        return datetime.fromisoformat(period_end) > datetime.now(timezone.utc)
    return False


def remaining_credits(user_id: str) -> int:
    """Real current-period balance, read from the real
    get_credit_balance(_user_id) RPC — the single source of truth (see
    this module's docstring). Fails closed to 0 (blocks the request)
    rather than guessing a generous default:
    - No subscription row at all -> 0.
    - A real row whose status isn't currently payable (see
      _is_eligible) -> 0, even if credits_remaining is positive — the RPC
      itself doesn't gate on status, so this module has to.
    - tier_credit_allotment (inside the RPC) already fails an
      unrecognized tier closed to 0 on its own."""
    balance = repo.get_credit_balance(user_id)
    if balance is None or not _is_eligible(balance):
        return 0
    return balance["credits_remaining"]


def check_sufficient_credits(user_id: str) -> None:
    """Raises InsufficientCreditsError if the user has 0 or fewer credits
    remaining — called before any real Claude API call is made, so a
    request that can't be paid for never spends real money.

    Deliberately checked against >0, not against the specific cost of this
    analysis: tiling_path (and therefore the real cost, 1 vs 5 credits)
    isn't known until after the pipeline has already run real detect
    calls — that's the whole reason this check happens up front and the
    deduction happens after (see dre.service.analyze_request). A user with,
    say, 3 credits left is deliberately allowed to start an analysis that
    might turn out to cost 5 — going into overage rather than being blocked
    for a shortfall that can't be known in advance."""
    remaining = remaining_credits(user_id)
    if remaining <= 0:
        raise InsufficientCreditsError(user_id, remaining)


def record_usage(*, user_id: str, analysis_request_id: str, tiling_path: str) -> None:
    repo.record_credit_usage(
        user_id=user_id,
        analysis_request_id=analysis_request_id,
        credits_used=credits_for_tiling_path(tiling_path),
    )
