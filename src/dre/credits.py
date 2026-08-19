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

Tier names are lowercase ('starter'/'standard'/'pro'), matching the `tier`
column's own default value — not yet confirmed against a real subscription
row (none existed at build time), since Stripe wiring is brand new.
"""

from __future__ import annotations

from dre.supa import repository as repo

TIER_MONTHLY_CREDITS = {"starter": 15, "standard": 40, "pro": 100}

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


def remaining_credits(user_id: str) -> int:
    """Real current-period balance: the user's subscription tier's monthly
    allotment minus real credit_usage recorded so far this billing period.

    Fails closed in every ambiguous case — a real, active subscription must
    be found with a recognized tier, or this returns 0 (blocks the request)
    rather than guessing a generous default:
    - No subscription row at all, or none with status='active' -> 0.
    - An unrecognized tier value -> 0.
    - current_period_start/current_period_end unset (no Stripe period yet,
      e.g. a fresh sandbox subscription) -> falls back to summing *all*
      credit_usage ever recorded for this user on that side, so a null
      period can't be used to bypass metering entirely."""
    subscription = repo.get_current_subscription(user_id)
    if subscription is None:
        return 0

    allotment = TIER_MONTHLY_CREDITS.get(subscription.get("tier"))
    if allotment is None:
        return 0

    used = repo.sum_credits_used(
        user_id,
        period_start=subscription.get("current_period_start"),
        period_end=subscription.get("current_period_end"),
    )
    return allotment - used


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
