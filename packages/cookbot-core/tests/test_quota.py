"""Unit tests for the pure quota math (STEP 42). No Firestore, no I/O."""

from datetime import UTC, datetime

from cookbot.models.quota import (
    check_budget,
    counter_for,
    day_key,
    month_key,
    next_reset,
)
from cookbot.models.tenant import TenantConfig
from cookbot.models.user import TokenQuota, UsageCounter

TZ = "Europe/Warsaw"


def _cfg(**kw) -> TenantConfig:
    return TenantConfig(
        tenant_id="t", persona="p", language="pl",
        recipe_source_url="u", allowed_origins=["*"], **kw,
    )


# ── period keys (timezone-aware) ──────────────────────────────────────────────

def test_day_key_uses_local_timezone():
    # 23:30 UTC on the 7th is 01:30 on the 8th in Warsaw (summer, +2) → day rolls over.
    now = datetime(2026, 7, 7, 23, 30, tzinfo=UTC)
    assert day_key(now, TZ) == "2026-07-08"
    assert month_key(now, TZ) == "2026-07"


def test_month_key_rolls_at_local_month_boundary():
    # 31 Jul 23:00 UTC → 01:00 1 Aug Warsaw → month is August locally.
    now = datetime(2026, 7, 31, 23, 0, tzinfo=UTC)
    assert month_key(now, TZ) == "2026-08"


# ── lazy reset ────────────────────────────────────────────────────────────────

def test_counter_for_resets_on_new_period():
    stale = UsageCounter(period_key="2026-07-06", tokens_used=999)
    fresh = counter_for(stale, "2026-07-07")
    assert fresh.period_key == "2026-07-07"
    assert fresh.tokens_used == 0


def test_counter_for_keeps_same_period():
    same = UsageCounter(period_key="2026-07-07", tokens_used=120)
    assert counter_for(same, "2026-07-07").tokens_used == 120


def test_counter_for_none_is_zeroed():
    assert counter_for(None, "2026-07-07").tokens_used == 0


# ── budget checks ─────────────────────────────────────────────────────────────

def _counter(used: int, key: str = "2026-07-07") -> UsageCounter:
    return UsageCounter(period_key=key, tokens_used=used)


def test_zero_limit_is_unlimited():
    q = TokenQuota(daily_limit=0, monthly_limit=0)
    status = check_budget(q, _counter(10_000), _counter(999_999))
    assert status.allowed
    assert status.exceeded_window is None
    assert status.reason is None


def test_under_limit_allowed():
    q = TokenQuota(daily_limit=1000, monthly_limit=10_000)
    assert check_budget(q, _counter(500), _counter(500)).allowed


def test_daily_limit_reached_refuses():
    q = TokenQuota(daily_limit=1000, monthly_limit=10_000)
    status = check_budget(q, _counter(1000), _counter(500))
    assert not status.allowed
    assert status.exceeded_window == "daily"
    assert status.reason == "daily_limit_reached"


def test_monthly_limit_reached_refuses():
    q = TokenQuota(daily_limit=0, monthly_limit=10_000)
    status = check_budget(q, _counter(500), _counter(10_050))
    assert not status.allowed
    assert status.exceeded_window == "monthly"


def test_daily_reason_wins_when_both_exceeded():
    q = TokenQuota(daily_limit=1000, monthly_limit=10_000)
    status = check_budget(q, _counter(1000), _counter(10_000))
    assert status.exceeded_window == "daily"


# ── reset time ────────────────────────────────────────────────────────────────

def test_next_reset_daily_is_local_midnight():
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    reset = next_reset(now, TZ, "daily")
    assert reset.hour == 0 and reset.minute == 0
    assert reset.date().isoformat() == "2026-07-08"


def test_next_reset_monthly_is_first_of_next_month():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    reset = next_reset(now, TZ, "monthly")
    assert reset.day == 1 and reset.month == 8


def test_next_reset_monthly_wraps_year():
    now = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
    reset = next_reset(now, TZ, "monthly")
    assert reset.year == 2027 and reset.month == 1 and reset.day == 1


# ── config default quota ──────────────────────────────────────────────────────

def test_default_quota_from_config():
    q = _cfg(default_daily_token_limit=500, default_monthly_token_limit=9000).default_quota()
    assert q.daily_limit == 500 and q.monthly_limit == 9000
