from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """Single-day scenario with market-specific forecast versions."""

    scenario_id: str
    name: str
    demand_profile: dict[int, float]
    solar_profile: dict[int, float]
    wind_profile: dict[int, float]
    forecast_version: str
    description: str = ""
    market_forecasts: dict[str, dict[str, dict[int, float]]] | None = None

    def forecast_for(self, market: str) -> dict[str, dict[int, float]]:
        """Return demand, solar, and wind profiles for a market stage."""
        if self.market_forecasts and market in self.market_forecasts:
            return self.market_forecasts[market]
        return {
            "demand": self.demand_profile,
            "solar": self.solar_profile,
            "wind": self.wind_profile,
        }
