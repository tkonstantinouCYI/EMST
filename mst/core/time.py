from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class MarketTime:
    """Single-day MTU-based market time index."""

    delivery_date: date
    periods: tuple[int, ...] = tuple(range(1, 25))
    mtu_minutes: int = 60

    def __post_init__(self) -> None:
        if self.mtu_minutes <= 0:
            raise ValueError("mtu_minutes must be positive.")
        if 1440 % self.mtu_minutes != 0:
            raise ValueError("mtu_minutes must divide a 24-hour day exactly.")
        expected_periods = 1440 // self.mtu_minutes
        if len(self.periods) != expected_periods:
            raise ValueError(
                f"Expected {expected_periods} periods for {self.mtu_minutes}-minute MTUs, "
                f"got {len(self.periods)}."
            )

    @classmethod
    def single_day(cls, delivery_date: date, mtu_minutes: int = 60) -> "MarketTime":
        periods_per_day = 1440 // mtu_minutes
        return cls(
            delivery_date=delivery_date,
            periods=tuple(range(1, periods_per_day + 1)),
            mtu_minutes=mtu_minutes,
        )

    @classmethod
    def single_day_hourly(cls, delivery_date: date) -> "MarketTime":
        return cls.single_day(delivery_date=delivery_date, mtu_minutes=60)

    @property
    def mtu_hours(self) -> float:
        return self.mtu_minutes / 60.0

    def timestamp_for_period(self, period: int) -> datetime:
        if period not in self.periods:
            raise ValueError(f"Unknown market period {period}.")
        return datetime.combine(self.delivery_date, datetime.min.time()) + timedelta(
            minutes=(period - 1) * self.mtu_minutes
        )

    def periods_for_mtu_hours(self, mtu_hours: tuple[int, ...]) -> tuple[int, ...]:
        """Return periods whose conventional 1-24 MTU-hour label is in mtu_hours."""
        selected = []
        for period in self.periods:
            hour_label = self.timestamp_for_period(period).hour + 1
            if hour_label in mtu_hours:
                selected.append(period)
        return tuple(selected)
