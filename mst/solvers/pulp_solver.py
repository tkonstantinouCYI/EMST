from __future__ import annotations

from collections import defaultdict

from mst.core.bids import Bid, BlockBid, CircularBlockBid
from mst.solvers.base import MarketClearingSolver


class PulpMarketClearingSolver(MarketClearingSolver):
    """PuLP-backed social-welfare market-clearing solver."""

    def __init__(self, msg: bool = False, reject_paradoxical_blocks: bool = True) -> None:
        self.msg = msg
        self.reject_paradoxical_blocks = reject_paradoxical_blocks

    def clear(
        self,
        *,
        market_name: str,
        periods: tuple[int, ...],
        bids: list[Bid],
        block_bids: list[BlockBid] | None = None,
        circular_block_bids: list[CircularBlockBid] | None = None,
    ) -> dict:
        try:
            import pulp
        except ImportError as exc:
            raise RuntimeError("PuLP is required. Install with `pip install -e .`.") from exc

        block_bids = list(block_bids or [])
        circular_block_bids = list(circular_block_bids or [])
        for family in circular_block_bids:
            block_bids.extend(family.linked_blocks)

        rejected_blocks: set[str] = set()
        while True:
            result = self._clear_once(
                pulp=pulp,
                market_name=market_name,
                periods=periods,
                bids=bids,
                block_bids=block_bids,
                circular_block_bids=circular_block_bids,
                rejected_blocks=rejected_blocks,
            )
            paradoxical_blocks = self._paradoxically_accepted_blocks(
                block_bids=block_bids,
                accepted_bids=result["accepted_bids"],
                prices=result["prices"],
            )
            if not self.reject_paradoxical_blocks or not paradoxical_blocks:
                result["metadata"] = {
                    "price_method": result.pop("price_method"),
                    "rejected_paradoxical_blocks": sorted(rejected_blocks),
                }
                return result
            rejected_blocks.update(paradoxical_blocks)

    def _clear_once(
        self,
        *,
        pulp,
        market_name: str,
        periods: tuple[int, ...],
        bids: list[Bid],
        block_bids: list[BlockBid],
        circular_block_bids: list[CircularBlockBid],
        rejected_blocks: set[str],
    ) -> dict:
        problem = pulp.LpProblem(f"{market_name}_welfare", pulp.LpMaximize)
        bid_vars = {
            bid.bid_id: pulp.LpVariable(f"q_{bid.bid_id}", lowBound=0, upBound=max(0.0, bid.quantity))
            for bid in bids
            if bid.quantity > 0
        }
        block_ratio_vars = {
            block.bid_id: pulp.LpVariable(f"ar_{block.bid_id}", lowBound=0, upBound=1)
            for block in block_bids
        }
        block_active_vars = {
            block.bid_id: pulp.LpVariable(f"y_{block.bid_id}", cat="Binary")
            for block in block_bids
        }

        simple_welfare = []
        for bid in bids:
            if bid.bid_id not in bid_vars:
                continue
            sign = 1.0 if bid.side == "buy" else -1.0
            simple_welfare.append(sign * bid.price * bid_vars[bid.bid_id])

        block_welfare = []
        for block in block_bids:
            sign = 1.0 if block.side == "buy" else -1.0
            value = sum(q * p for q, p in zip(block.quantities, block.prices))
            block_welfare.append(sign * value * block_ratio_vars[block.bid_id])

        problem += pulp.lpSum(simple_welfare + block_welfare)

        for block in block_bids:
            ratio = block_ratio_vars[block.bid_id]
            active = block_active_vars[block.bid_id]
            if block.bid_id in rejected_blocks:
                problem += active == 0, f"reject_block_{block.bid_id}"
            if block.acceptance_binary:
                problem += ratio == active, f"binary_ratio_{block.bid_id}"
            else:
                problem += ratio <= active, f"ratio_upper_{block.bid_id}"
                problem += ratio >= block.minimum_acceptance_ratio * active, f"ratio_min_{block.bid_id}"
            if block.parent_bid_id:
                if block.parent_bid_id not in block_active_vars:
                    raise ValueError(
                        f"Block '{block.bid_id}' links to unknown parent '{block.parent_bid_id}'."
                    )
                problem += active <= block_active_vars[block.parent_bid_id], f"linked_active_{block.bid_id}"
                problem += ratio <= block_ratio_vars[block.parent_bid_id], f"linked_ratio_{block.bid_id}"

        balance_constraints = {}
        for period in periods:
            buys = [
                bid_vars[bid.bid_id]
                for bid in bids
                if bid.period == period and bid.side == "buy" and bid.bid_id in bid_vars
            ]
            sells = [
                bid_vars[bid.bid_id]
                for bid in bids
                if bid.period == period and bid.side == "sell" and bid.bid_id in bid_vars
            ]
            for block in block_bids:
                for block_period, quantity in zip(block.periods, block.quantities):
                    if block_period != period:
                        continue
                    if block.side == "sell":
                        sells.append(quantity * block_ratio_vars[block.bid_id])
                    else:
                        buys.append(quantity * block_ratio_vars[block.bid_id])
            constraint = pulp.lpSum(buys) - pulp.lpSum(sells) == 0
            problem += constraint, f"balance_{period}"
            balance_constraints[period] = constraint

        for family in circular_block_bids:
            if family.net_energy_balance_rule != "zero_sum":
                continue
            problem += (
                pulp.lpSum(
                    (1.0 if block.side == "sell" else -1.0)
                    * sum(block.quantities)
                    * block_ratio_vars[block.bid_id]
                    for block in family.linked_blocks
                    if block.bid_id in block_ratio_vars
                )
                == 0
            ), f"circular_balance_{family.family_id}"

        status = problem.solve(pulp.PULP_CBC_CMD(msg=self.msg))
        status_name = pulp.LpStatus[status]
        if status_name != "Optimal":
            raise RuntimeError(f"{market_name} clearing failed with status {status_name}.")

        fixed_binary_values = {
            bid_id: round(float(var.value() or 0.0))
            for bid_id, var in block_active_vars.items()
        }
        for bid_id, value in fixed_binary_values.items():
            problem += block_active_vars[bid_id] == value, f"fixed_binary_{bid_id}"

        fixed_status = problem.solve(pulp.PULP_CBC_CMD(msg=self.msg, mip=False))
        fixed_status_name = pulp.LpStatus[fixed_status]
        if fixed_status_name != "Optimal":
            raise RuntimeError(f"{market_name} fixed-binary LP failed with status {fixed_status_name}.")

        accepted_bids = {bid_id: float(var.value() or 0.0) for bid_id, var in bid_vars.items()}
        accepted_blocks = {bid_id: float(var.value() or 0.0) for bid_id, var in block_ratio_vars.items()}
        accepted_bids.update(accepted_blocks)
        prices = self._prices_from_solution(periods, bids, block_bids, accepted_bids, balance_constraints)
        positions: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))

        for bid in bids:
            quantity = accepted_bids.get(bid.bid_id, 0.0)
            signed = quantity if bid.side == "sell" else -quantity
            positions[bid.participant_id][bid.period] += signed

        for block in block_bids:
            acceptance_ratio = accepted_bids.get(block.bid_id, 0.0)
            if acceptance_ratio <= 1e-9:
                continue
            for period, quantity in zip(block.periods, block.quantities):
                signed = quantity * acceptance_ratio if block.side == "sell" else -quantity * acceptance_ratio
                positions[block.participant_id][period] += signed

        return {
            "prices": prices,
            "accepted_bids": accepted_bids,
            "participant_positions": {pid: dict(values) for pid, values in positions.items()},
            "social_welfare": float(pulp.value(problem.objective) or 0.0),
            "price_method": "fixed_binary_lp_shadow_prices",
        }

    def _paradoxically_accepted_blocks(
        self,
        *,
        block_bids: list[BlockBid],
        accepted_bids: dict[str, float],
        prices: dict[int, float],
    ) -> set[str]:
        paradoxical_blocks: set[str] = set()
        for block in block_bids:
            acceptance_ratio = accepted_bids.get(block.bid_id, 0.0)
            if acceptance_ratio <= 1e-9:
                continue
            total_quantity = sum(block.quantities)
            if total_quantity <= 1e-9:
                continue
            block_vwap = sum(
                prices.get(period, 0.0) * quantity
                for period, quantity in zip(block.periods, block.quantities)
            ) / total_quantity
            bid_vwap = sum(
                price * quantity
                for price, quantity in zip(block.prices, block.quantities)
            ) / total_quantity
            if block.side == "sell" and block_vwap + 1e-6 < bid_vwap:
                paradoxical_blocks.add(block.bid_id)
            elif block.side == "buy" and block_vwap - 1e-6 > bid_vwap:
                paradoxical_blocks.add(block.bid_id)
        return paradoxical_blocks

    def _prices_from_solution(
        self,
        periods: tuple[int, ...],
        bids: list[Bid],
        block_bids: list[BlockBid],
        accepted_bids: dict[str, float],
        balance_constraints: dict[int, object],
    ) -> dict[int, float]:
        prices: dict[int, float] = {}
        for period in periods:
            traded_volume = self._accepted_volume_for_period(period, bids, block_bids, accepted_bids)
            if traded_volume <= 1e-9:
                prices[period] = 0.0
                continue
            dual = getattr(balance_constraints[period], "pi", None)
            if dual is not None:
                prices[period] = round(float(dual), 6)
                continue

            accepted_sell_prices = [
                bid.price
                for bid in bids
                if bid.period == period and bid.side == "sell" and accepted_bids.get(bid.bid_id, 0.0) > 1e-6
            ]
            for block in block_bids:
                if accepted_bids.get(block.bid_id, 0.0) > 0.5 and period in block.periods:
                    accepted_sell_prices.extend(block.prices)
            prices[period] = max(accepted_sell_prices) if accepted_sell_prices else 0.0
        return prices

    def _accepted_volume_for_period(
        self,
        period: int,
        bids: list[Bid],
        block_bids: list[BlockBid],
        accepted_bids: dict[str, float],
    ) -> float:
        simple_buy_volume = sum(
            accepted_bids.get(bid.bid_id, 0.0)
            for bid in bids
            if bid.period == period and bid.side == "buy"
        )
        block_buy_volume = 0.0
        for block in block_bids:
            if block.side != "buy":
                continue
            acceptance_ratio = accepted_bids.get(block.bid_id, 0.0)
            for block_period, quantity in zip(block.periods, block.quantities):
                if block_period == period:
                    block_buy_volume += quantity * acceptance_ratio
        return simple_buy_volume + block_buy_volume
