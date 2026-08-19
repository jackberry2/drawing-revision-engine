from unittest.mock import patch

import pytest

from dre import credits


def test_credits_for_tiling_path_only_tiled_costs_five():
    assert credits.credits_for_tiling_path("tiled") == 5
    for path in ("single_pass", "single_pass_no_pdf_source", "tiled_failed_fallback", "not_applicable"):
        assert credits.credits_for_tiling_path(path) == 1


def test_remaining_credits_no_subscription_is_zero():
    with patch("dre.credits.repo.get_current_subscription", return_value=None):
        assert credits.remaining_credits("user-1") == 0


def test_remaining_credits_unrecognized_tier_is_zero():
    with patch(
        "dre.credits.repo.get_current_subscription",
        return_value={"tier": "enterprise", "current_period_start": None, "current_period_end": None},
    ):
        assert credits.remaining_credits("user-1") == 0


def test_remaining_credits_subtracts_usage_within_period():
    subscription = {
        "tier": "starter",
        "current_period_start": "2026-08-01T00:00:00Z",
        "current_period_end": "2026-09-01T00:00:00Z",
    }
    with patch("dre.credits.repo.get_current_subscription", return_value=subscription), patch(
        "dre.credits.repo.sum_credits_used", return_value=6
    ) as mock_sum:
        assert credits.remaining_credits("user-1") == 15 - 6

    mock_sum.assert_called_once_with(
        "user-1", period_start="2026-08-01T00:00:00Z", period_end="2026-09-01T00:00:00Z"
    )


def test_remaining_credits_can_go_negative_from_overage():
    subscription = {
        "tier": "standard",
        "current_period_start": "2026-08-01T00:00:00Z",
        "current_period_end": "2026-09-01T00:00:00Z",
    }
    with patch("dre.credits.repo.get_current_subscription", return_value=subscription), patch(
        "dre.credits.repo.sum_credits_used", return_value=45
    ):
        assert credits.remaining_credits("user-1") == 40 - 45 == -5


def test_check_sufficient_credits_raises_at_zero_or_below():
    with patch("dre.credits.remaining_credits", return_value=0):
        with pytest.raises(credits.InsufficientCreditsError) as exc_info:
            credits.check_sufficient_credits("user-1")
    assert exc_info.value.user_id == "user-1"
    assert exc_info.value.remaining == 0

    with patch("dre.credits.remaining_credits", return_value=-3):
        with pytest.raises(credits.InsufficientCreditsError):
            credits.check_sufficient_credits("user-1")


def test_check_sufficient_credits_allows_a_small_balance_even_if_below_tiled_cost():
    """The real-product decision (point 4): a user with e.g. 3 credits left
    must still be allowed to start an analysis that might turn out to cost
    5 (tiled) — tiling_path isn't known until after real Claude calls have
    already run, so this can only ever check ">0", never ">= this
    analysis's specific cost"."""
    with patch("dre.credits.remaining_credits", return_value=3):
        credits.check_sufficient_credits("user-1")  # must not raise


def test_record_usage_passes_through_the_right_credit_cost():
    with patch("dre.credits.repo.record_credit_usage") as mock_record:
        credits.record_usage(user_id="user-1", analysis_request_id="req-1", tiling_path="tiled")
    mock_record.assert_called_once_with(user_id="user-1", analysis_request_id="req-1", credits_used=5)

    with patch("dre.credits.repo.record_credit_usage") as mock_record:
        credits.record_usage(user_id="user-1", analysis_request_id="req-1", tiling_path="single_pass")
    mock_record.assert_called_once_with(user_id="user-1", analysis_request_id="req-1", credits_used=1)
