"""Pure quota math — period keys, budget checks, refusal reasons.

No Firestore, no I/O — this is the unit-testable core the WS handler and the
FirestoreService counters build on. Reset windows are computed in a fixed tenant
timezone so "daily"/"monthly" mean the user's local day/month, not UTC.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cookbot.models.user import TokenQuota, UsageCounter


def day_key(now: datetime, tz: str) -> str:
    """Reset-window key for the daily budget, e.g. "2026-07-07"."""
    return now.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def month_key(now: datetime, tz: str) -> str:
    """Reset-window key for the monthly budget, e.g. "2026-07"."""
    return now.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


def next_reset(now: datetime, tz: str, window: str) -> datetime:
    """When the given window ("daily" | "monthly") next resets, in `tz`.

    Daily → next local midnight; monthly → first local midnight of next month.
    """
    local = now.astimezone(ZoneInfo(tz))
    if window == "monthly":
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        return local.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=1)


def counter_for(counter: UsageCounter | None, period_key: str) -> UsageCounter:
    """Lazily reset a counter: if it's missing or belongs to a past window,
    return a fresh zeroed counter for the current window."""
    if counter is None or counter.period_key != period_key:
        return UsageCounter(period_key=period_key, tokens_used=0)
    return counter


@dataclass(frozen=True)
class BudgetStatus:
    """Result of checking a user's remaining budget before a turn."""

    allowed: bool
    exceeded_window: str | None  # "daily" | "monthly" | None
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int

    @property
    def reason(self) -> str | None:
        """Machine-readable refusal reason for the WS error, or None if allowed."""
        return f"{self.exceeded_window}_limit_reached" if not self.allowed else None


def check_budget(
    quota: TokenQuota,
    daily: UsageCounter,
    monthly: UsageCounter,
) -> BudgetStatus:
    """Decide whether the next turn is allowed. A limit of 0 ⇒ unlimited.

    Both counters must already be reset to the current window (see counter_for).
    The daily window is checked first so its reason wins when both are exceeded.
    """
    over_daily = quota.daily_limit > 0 and daily.tokens_used >= quota.daily_limit
    over_monthly = quota.monthly_limit > 0 and monthly.tokens_used >= quota.monthly_limit
    exceeded = "daily" if over_daily else "monthly" if over_monthly else None
    return BudgetStatus(
        allowed=exceeded is None,
        exceeded_window=exceeded,
        daily_used=daily.tokens_used,
        daily_limit=quota.daily_limit,
        monthly_used=monthly.tokens_used,
        monthly_limit=quota.monthly_limit,
    )
