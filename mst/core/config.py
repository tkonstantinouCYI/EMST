from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarketStageConfig:
    """Configuration for one market stage."""

    name: str
    price_floor: float = -500.0
    price_cap: float = 4000.0
    demand_bid_price: float = 500.0
    allow_blocks: bool = True


@dataclass(frozen=True)
class SimulationConfig:
    """Generic sequential market simulation configuration."""

    market_sequence: tuple[str, ...] = ("FM", "DAM", "IDA1", "IDA2", "IDA3")
    markets: dict[str, MarketStageConfig] = field(default_factory=dict)
