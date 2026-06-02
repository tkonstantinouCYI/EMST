from __future__ import annotations

from emst.core.bids import Bid, BlockBid, CircularBlockBid
from emst.core.participants import Participant
from emst.core.scenarios import Scenario
from emst.core.time import MarketTime


def generate_energy_exchange_bids(
    *,
    market_name: str,
    participants: list[Participant],
    scenario: Scenario,
    previous_positions: dict[str, dict[int, float]],
    demand_bid_price: float = 500.0,
    allow_block_orders: bool = True,
    market_time: MarketTime | None = None,
) -> tuple[list[Bid], list[BlockBid], list[CircularBlockBid]]:
    """Generate simple welfare-clearing bids from participant assets and forecasts."""
    forecast = scenario.forecast_for(market_name)
    demand_profile = forecast["demand"]
    solar_profile = forecast["solar"]
    wind_profile = forecast["wind"]

    bids: list[Bid] = []
    block_bids: list[BlockBid] = []
    circular_block_bids: list[CircularBlockBid] = []
    periods = tuple(sorted(demand_profile))
    mtu_hours = market_time.mtu_hours if market_time else 1.0

    for participant in participants:
        previous = previous_positions.get(participant.participant_id, {})
        for asset in participant.assets:
            if asset.asset_type == "demand":
                share = float(asset.metadata.get("demand_share", 1.0))
                for period in periods:
                    target = demand_profile[period] * share
                    residual = target + previous.get(period, 0.0)
                    side = "buy" if residual >= 0 else "sell"
                    bids.append(
                        Bid(
                            bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-{period}",
                            participant_id=participant.participant_id,
                            market=market_name,
                            period=period,
                            side=side,
                            quantity=abs(residual),
                            price=demand_bid_price if side == "buy" else 0.0,
                        )
                    )
            elif asset.asset_type in {"solar", "wind"}:
                profile = solar_profile if asset.asset_type == "solar" else wind_profile
                capacity = max(asset.capacity, 0.0)
                for period in periods:
                    target = profile[period] * capacity
                    residual = target - previous.get(period, 0.0)
                    side = "sell" if residual >= 0 else "buy"
                    bids.append(
                        Bid(
                            bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-{period}",
                            participant_id=participant.participant_id,
                            market=market_name,
                            period=period,
                            side=side,
                            quantity=abs(residual),
                            price=asset.marginal_cost if side == "sell" else demand_bid_price * 0.2,
                        )
                    )
            elif asset.asset_type == "thermal":
                p_min = float(asset.metadata.get("technical_minimum", 0.0))
                p_max = asset.capacity * mtu_hours
                p_min = p_min * mtu_hours
                if allow_block_orders and p_min > 0:
                    quantities = tuple(max(0.0, p_min - previous.get(period, 0.0)) for period in periods)
                    if sum(quantities) > 0:
                        block_bids.append(
                            BlockBid(
                                bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-base",
                                participant_id=participant.participant_id,
                                market=market_name,
                                periods=periods,
                                quantities=quantities,
                                prices=tuple(asset.marginal_cost for _ in periods),
                            )
                        )
                    residual_cap = max(p_max - p_min, 0.0)
                    for period in periods:
                        quantity = max(0.0, residual_cap)
                        bids.append(
                            Bid(
                                bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-residual-{period}",
                                participant_id=participant.participant_id,
                                market=market_name,
                                period=period,
                                side="sell",
                                quantity=quantity,
                                price=asset.marginal_cost + 5.0,
                            )
                        )
                else:
                    for period in periods:
                        quantity = max(0.0, p_max - previous.get(period, 0.0))
                        bids.append(
                            Bid(
                                bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-{period}",
                                participant_id=participant.participant_id,
                                market=market_name,
                                period=period,
                                side="sell",
                                quantity=quantity,
                                price=asset.marginal_cost,
                            )
                        )
            elif asset.asset_type == "storage":
                charge_hours = set(asset.metadata.get("charge_hours", (11, 12, 13, 14)))
                discharge_hours = set(asset.metadata.get("discharge_hours", (18, 19, 20, 21)))
                charge_periods: list[int] = []
                charge_quantities: list[float] = []
                discharge_periods: list[int] = []
                discharge_quantities: list[float] = []
                for period in periods:
                    target = 0.0
                    if period in charge_hours:
                        target = -asset.capacity * mtu_hours
                    elif period in discharge_hours:
                        target = asset.capacity * mtu_hours
                    residual = target - previous.get(period, 0.0)
                    if abs(residual) <= 1e-9:
                        continue
                    if residual < 0:
                        charge_periods.append(period)
                        charge_quantities.append(abs(residual))
                    else:
                        discharge_periods.append(period)
                        discharge_quantities.append(abs(residual))
                if allow_block_orders and charge_periods and discharge_periods:
                    charge_total = sum(charge_quantities)
                    discharge_total = sum(discharge_quantities)
                    balanced_energy = min(charge_total, discharge_total)
                    charge_scale = balanced_energy / charge_total
                    discharge_scale = balanced_energy / discharge_total
                    charge_block = BlockBid(
                        bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-charge-block",
                        participant_id=participant.participant_id,
                        market=market_name,
                        periods=tuple(charge_periods),
                        quantities=tuple(q * charge_scale for q in charge_quantities),
                        prices=tuple(float(asset.metadata.get("charge_bid_price", 35.0)) for _ in charge_periods),
                        side="buy",
                    )
                    discharge_block = BlockBid(
                        bid_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-discharge-block",
                        participant_id=participant.participant_id,
                        market=market_name,
                        periods=tuple(discharge_periods),
                        quantities=tuple(q * discharge_scale for q in discharge_quantities),
                        prices=tuple(asset.marginal_cost for _ in discharge_periods),
                        side="sell",
                    )
                    circular_block_bids.append(
                        CircularBlockBid(
                            family_id=f"{market_name}-{participant.participant_id}-{asset.asset_id}-cycle",
                            participant_id=participant.participant_id,
                            linked_blocks=[charge_block, discharge_block],
                        )
                    )

    return [bid for bid in bids if bid.quantity > 1e-9], block_bids, circular_block_bids
