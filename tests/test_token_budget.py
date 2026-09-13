"""
Unit tests for Token Budget Management, Cost Calculation, and Quota Enforcement.
"""
import pytest
from storage.state_store import StateStore
from analytics.token_budget import TokenBudgetManager


def test_token_estimation_and_cost_calculation(tmp_path):
    store = StateStore(str(tmp_path / "test_token.db"))
    manager = TokenBudgetManager(store, daily_token_limit=100_000)

    sample_prompt = "Analyze NVIDIA SEC 8-K filing regarding GPU datacenter distribution agreement."
    tokens = manager.estimate_tokens(sample_prompt)
    assert tokens > 0

    cost = manager.calculate_cost(
        prompt_tokens=10_000,
        completion_tokens=2_000,
        model_name="gemini-3.8-flash"

    )
    assert cost > 0.0
    assert cost < 0.01  # Flash model is very cost-effective


def test_token_budget_quota_exhaustion_and_fallback(tmp_path):
    store = StateStore(str(tmp_path / "test_token2.db"))
    # Set a tiny limit of 1,000 tokens
    manager = TokenBudgetManager(store, daily_token_limit=1_000)

    status_before = manager.check_budget_status()
    assert status_before.budget_exhausted is False
    assert status_before.remaining_daily_tokens == 1_000

    # Record 1,200 tokens
    manager.record_usage(
        agent_name="Analysis Agent",
        prompt_tokens=1_000,
        completion_tokens=200,
        model_name="gemini-3.8-flash"

    )

    status_after = manager.check_budget_status()
    assert status_after.budget_exhausted is True
    assert status_after.fallback_mode_active is True
    assert status_after.remaining_daily_tokens == 0
