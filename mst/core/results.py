from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Positions = dict[str, dict[int, float]]


@dataclass
class MarketResult:
    """Generic market result shared by FM, DAM, and IDA markets."""

    market_name: str
    prices: dict[int, float]
    accepted_bids: dict[str, float]
    participant_positions: Positions
    social_welfare: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SequentialSimulationResult:
    """Combined result for the FM-DAM-IDA sequence."""

    fm_result: MarketResult
    dam_result: MarketResult
    ida_results: dict[str, MarketResult]
    final_positions: Positions
    price_summary: dict[str, dict[int, float]]
