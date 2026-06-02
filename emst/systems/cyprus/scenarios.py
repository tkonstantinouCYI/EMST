from __future__ import annotations

from datetime import date

from emst.core.scenarios import Scenario
from emst.core.time import MarketTime


def _profile(values: list[float], market_time: MarketTime) -> dict[int, float]:
    if len(values) != 24:
        raise ValueError("Cyprus single-day profiles must contain 24 hourly values.")
    expanded: dict[int, float] = {}
    for period in market_time.periods:
        timestamp = market_time.timestamp_for_period(period)
        expanded[period] = float(values[timestamp.hour]) * market_time.mtu_hours
    return expanded


def high_solar_day(market_time: MarketTime | None = None) -> Scenario:
    """Representative single-day scenario with updated IDA forecasts."""
    market_time = market_time or MarketTime.single_day_hourly(date(2026, 6, 1))
    demand = _profile([
        430, 405, 390, 385, 400, 445, 520, 610,
        690, 735, 760, 775, 780, 770, 745, 720,
        700, 735, 790, 830, 805, 720, 610, 500,
    ], market_time)
    solar = _profile([
        0.00, 0.00, 0.00, 0.00, 0.01, 0.08, 0.22, 0.42,
        0.62, 0.78, 0.91, 0.98, 0.95, 0.86, 0.70, 0.48,
        0.24, 0.07, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
    ], market_time)
    wind = _profile([
        0.40, 0.42, 0.39, 0.36, 0.35, 0.34, 0.31, 0.28,
        0.25, 0.23, 0.22, 0.24, 0.27, 0.30, 0.34, 0.39,
        0.43, 0.48, 0.52, 0.55, 0.53, 0.50, 0.47, 0.44,
    ], market_time)

    return Scenario(
        scenario_id="high_solar_day",
        name="High Solar Day",
        demand_profile=demand,
        solar_profile=solar,
        wind_profile=wind,
        forecast_version="DAM",
        description="Single-day Cyprus scenario with strong midday solar and evening ramp.",
        market_forecasts={
            "DAM": {"demand": demand, "solar": solar, "wind": wind},
            "IDA1": {
                "demand": {p: v * 1.01 for p, v in demand.items()},
                "solar": {p: v * 0.98 for p, v in solar.items()},
                "wind": {p: v * 1.03 for p, v in wind.items()},
            },
            "IDA2": {
                "demand": {p: v * (1.00 if market_time.timestamp_for_period(p).hour < 16 else 1.025) for p, v in demand.items()},
                "solar": {p: v * 0.94 for p, v in solar.items()},
                "wind": {p: v * 1.01 for p, v in wind.items()},
            },
            "IDA3": {
                "demand": {p: v * (0.995 if market_time.timestamp_for_period(p).hour < 11 else 1.01) for p, v in demand.items()},
                "solar": {p: v * 0.97 for p, v in solar.items()},
                "wind": {p: v * 0.99 for p, v in wind.items()},
            },
        },
    )


def predefined_scenarios(market_time: MarketTime | None = None) -> dict[str, Scenario]:
    scenario = high_solar_day(market_time=market_time)
    return {scenario.scenario_id: scenario}
