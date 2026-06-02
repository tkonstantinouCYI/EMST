from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Asset:
    """Physical or contractual resource owned by a participant."""

    asset_id: str
    name: str
    asset_type: str
    capacity: float
    marginal_cost: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Participant:
    """Market participant with one or more assets."""

    participant_id: str
    name: str
    participant_type: str
    assets: list[Asset] = field(default_factory=list)
