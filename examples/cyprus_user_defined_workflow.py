from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emst.core.bids import Bid, BlockBid
from emst.core.contracts import ForwardContract
from emst.core.participants import Asset, Participant
from emst.markets.dam import DAMMarket
from emst.markets.forward import ForwardMarket
from emst.markets.intraday import IntradayAuctionMarket
from emst.systems.cyprus import CyprusMarketConfig


def make_participants() -> list[Participant]:
    return [
        Participant(
            participant_id="USER_THERMAL",
            name="User Thermal Portfolio",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="user_ccgt",
                    name="User CCGT",
                    asset_type="thermal",
                    capacity=520.0,
                    marginal_cost=95.0,
                    metadata={"technical_minimum": 80.0},
                )
            ],
        ),
        Participant(
            participant_id="USER_SOLAR",
            name="User Solar Portfolio",
            participant_type="production",
            assets=[
                Asset(
                    asset_id="user_solar",
                    name="User Solar",
                    asset_type="solar",
                    capacity=220.0,
                    marginal_cost=0.0,
                )
            ],
        ),
        Participant(
            participant_id="USER_DEMAND",
            name="User Demand",
            participant_type="demand",
            assets=[
                Asset(
                    asset_id="user_load",
                    name="User Load",
                    asset_type="demand",
                    capacity=650.0,
                    metadata={"demand_share": 1.0},
                )
            ],
        ),
    ]


def make_forward_contract(config: CyprusMarketConfig) -> ForwardContract:
    return ForwardContract.from_period_quantities(
        contract_id="FM-USER-THERMAL-DEMAND-PROFILE",
        delivery_participant_id="USER_THERMAL",
        offtake_participant_id="USER_DEMAND",
        period_quantities={
            period: 90.0 if 8 <= period <= 22 else 55.0
            for period in config.market_time.periods
        },
        price=88.0,
    )


def make_dam_orders(config: CyprusMarketConfig, fm_positions: dict[str, dict[int, float]]):
    demand_profile = {
        1: 300, 2: 285, 3: 275, 4: 270, 5: 285, 6: 330,
        7: 390, 8: 460, 9: 520, 10: 555, 11: 575, 12: 590,
        13: 585, 14: 570, 15: 545, 16: 520, 17: 500, 18: 545,
        19: 610, 20: 640, 21: 610, 22: 520, 23: 410, 24: 330,
    }
    solar_profile = {
        1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.01, 6: 0.08,
        7: 0.22, 8: 0.42, 9: 0.65, 10: 0.82, 11: 0.96, 12: 1.00,
        13: 0.96, 14: 0.86, 15: 0.68, 16: 0.45, 17: 0.20, 18: 0.04,
        19: 0.00, 20: 0.00, 21: 0.00, 22: 0.00, 23: 0.00, 24: 0.00,
    }

    bids: list[Bid] = []
    for period in config.market_time.periods:
        forward_demand = fm_positions["USER_DEMAND"][period]
        demand_residual = demand_profile[period] + forward_demand
        bids.append(
            Bid(
                bid_id=f"DAM-DEMAND-{period}",
                participant_id="USER_DEMAND",
                market="DAM",
                period=period,
                side="buy",
                quantity=demand_residual,
                price=500.0,
            )
        )

        solar_quantity = 220.0 * solar_profile[period]
        if solar_quantity > 0:
            bids.append(
                Bid(
                    bid_id=f"DAM-SOLAR-{period}",
                    participant_id="USER_SOLAR",
                    market="DAM",
                    period=period,
                    side="sell",
                    quantity=solar_quantity,
                    price=0.0,
                )
            )

        bids.append(
            Bid(
                bid_id=f"DAM-THERMAL-RESIDUAL-{period}",
                participant_id="USER_THERMAL",
                market="DAM",
                period=period,
                side="sell",
                quantity=440.0,
                price=95.0,
            )
        )

    base_block = BlockBid(
        bid_id="DAM-THERMAL-BASE-BLOCK",
        participant_id="USER_THERMAL",
        market="DAM",
        periods=config.market_time.periods,
        quantities=tuple(80.0 for _ in config.market_time.periods),
        prices=tuple(70.0 for _ in config.market_time.periods),
        side="sell",
    )
    evening_block = BlockBid(
        bid_id="DAM-THERMAL-EVENING-LINKED-BLOCK",
        participant_id="USER_THERMAL",
        market="DAM",
        periods=(18, 19, 20, 21),
        quantities=(45.0, 45.0, 45.0, 45.0),
        prices=(90.0, 90.0, 90.0, 90.0),
        side="sell",
        parent_bid_id="DAM-THERMAL-BASE-BLOCK",
    )
    return bids, [base_block, evening_block]


def make_ida1_orders(dam_positions: dict[str, dict[int, float]]) -> list[Bid]:
    bids: list[Bid] = []
    for period in (11, 12, 13, 14):
        bids.append(
            Bid(
                bid_id=f"IDA1-SOLAR-BUYBACK-{period}",
                participant_id="USER_SOLAR",
                market="IDA1",
                period=period,
                side="buy",
                quantity=12.0,
                price=120.0,
            )
        )
        bids.append(
            Bid(
                bid_id=f"IDA1-THERMAL-UP-{period}",
                participant_id="USER_THERMAL",
                market="IDA1",
                period=period,
                side="sell",
                quantity=12.0,
                price=105.0,
            )
        )

    for period in (19, 20):
        current_demand_position = abs(dam_positions.get("USER_DEMAND", {}).get(period, 0.0))
        quantity = min(20.0, current_demand_position)
        bids.append(
            Bid(
                bid_id=f"IDA1-DEMAND-REDUCTION-{period}",
                participant_id="USER_DEMAND",
                market="IDA1",
                period=period,
                side="sell",
                quantity=quantity,
                price=180.0,
            )
        )
        bids.append(
            Bid(
                bid_id=f"IDA1-THERMAL-BUYBACK-{period}",
                participant_id="USER_THERMAL",
                market="IDA1",
                period=period,
                side="buy",
                quantity=quantity,
                price=200.0,
            )
        )
    return bids


def combine_positions(*position_maps: dict[str, dict[int, float]]) -> dict[str, dict[int, float]]:
    combined: dict[str, dict[int, float]] = {}
    for positions in position_maps:
        for participant_id, period_values in positions.items():
            combined.setdefault(participant_id, {})
            for period, quantity in period_values.items():
                combined[participant_id][period] = combined[participant_id].get(period, 0.0) + quantity
    return combined


def write_html_report(path: Path, prices: dict[str, dict[int, float]], final_positions: dict[str, dict[int, float]]) -> None:
    periods = sorted(next(iter(prices.values())).keys())
    price_rows = "\n".join(
        f"<tr><td>{market}</td><td>{period}</td><td>{values.get(period, 0):.2f}</td></tr>"
        for market, values in prices.items()
        for period in periods
    )
    position_rows = "\n".join(
        f"<tr><td>{participant}</td><td>{sum(values.values()):.2f}</td></tr>"
        for participant, values in final_positions.items()
    )

    max_price = max(max(values.values()) for values in prices.values()) or 1.0
    polylines = []
    colors = {"FM": "#777", "DAM": "#2563eb", "IDA1": "#dc2626"}
    width, height = 900, 280
    for market, values in prices.items():
        points = []
        for index, period in enumerate(periods):
            x = 40 + index * ((width - 80) / (len(periods) - 1))
            y = 20 + (height - 40) * (1 - values.get(period, 0.0) / max_price)
            points.append(f"{x:.1f},{y:.1f}")
        polylines.append(
            f'<polyline fill="none" stroke="{colors.get(market, "#111")}" stroke-width="3" points="{" ".join(points)}" />'
        )

    max_abs_position = max(abs(sum(values.values())) for values in final_positions.values()) or 1.0
    bars = []
    for index, (participant, values) in enumerate(final_positions.items()):
        total = sum(values.values())
        bar_width = 320 * abs(total) / max_abs_position
        x = 440 if total >= 0 else 440 - bar_width
        y = 30 + index * 38
        color = "#059669" if total >= 0 else "#b91c1c"
        bars.append(f'<text x="20" y="{y + 18}" font-size="13">{participant}</text>')
        bars.append(f'<rect x="{x}" y="{y}" width="{bar_width}" height="24" fill="{color}" />')
        bars.append(f'<text x="{760}" y="{y + 18}" font-size="13">{total:.1f} MWh</text>')

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Cyprus User-Defined EMST Example</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #172033; }}
    h1, h2 {{ margin-bottom: 8px; }}
    table {{ border-collapse: collapse; margin: 16px 0 28px; width: 100%; max-width: 900px; }}
    th, td {{ border: 1px solid #d6dae3; padding: 8px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    svg {{ max-width: 940px; width: 100%; border: 1px solid #d6dae3; background: #fff; }}
    .legend span {{ display: inline-block; margin-right: 18px; }}
  </style>
</head>
<body>
  <h1>Cyprus User-Defined EMST Example</h1>
  <p>Custom participants, bilateral forward contract, user-defined DAM bids, and one IDA1 adjustment auction.</p>

  <h2>Clearing Prices</h2>
  <div class="legend"><span style="color:#777">FM</span><span style="color:#2563eb">DAM</span><span style="color:#dc2626">IDA1</span></div>
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="Market price chart">
    <line x1="40" y1="{height - 20}" x2="{width - 40}" y2="{height - 20}" stroke="#ccd2dd" />
    <line x1="40" y1="20" x2="40" y2="{height - 20}" stroke="#ccd2dd" />
    {"".join(polylines)}
  </svg>

  <h2>Final Position Totals</h2>
  <svg viewBox="0 0 900 180" role="img" aria-label="Final position totals">
    <line x1="440" y1="15" x2="440" y2="160" stroke="#111827" />
    {"".join(bars)}
  </svg>

  <h2>Price Table</h2>
  <table>
    <thead><tr><th>Market</th><th>Period</th><th>Price EUR/MWh</th></tr></thead>
    <tbody>{price_rows}</tbody>
  </table>

  <h2>Position Totals</h2>
  <table>
    <thead><tr><th>Participant</th><th>Total MWh</th></tr></thead>
    <tbody>{position_rows}</tbody>
  </table>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> None:
    config = CyprusMarketConfig.default(mtu_minutes=60)
    participants = make_participants()
    contract = make_forward_contract(config)

    fm_result = ForwardMarket(config.market_time).clear(
        contracts=[contract],
        participants=participants,
    )

    dam_bids, dam_blocks = make_dam_orders(config, fm_result.participant_positions)
    dam_result = DAMMarket(config=config.dam, market_time=config.market_time).clear(
        bids=dam_bids,
        block_bids=dam_blocks,
    )

    ida1_bids = make_ida1_orders(dam_result.participant_positions)
    ida1_result = IntradayAuctionMarket(
        name="IDA1",
        config=config.markets["IDA1"],
        market_time=config.market_time,
    ).clear(bids=ida1_bids)

    final_positions = combine_positions(
        fm_result.participant_positions,
        dam_result.participant_positions,
        ida1_result.participant_positions,
    )
    prices = {
        "FM": fm_result.prices,
        "DAM": dam_result.prices,
        "IDA1": ida1_result.prices,
    }

    output_path = Path(__file__).resolve().parent / "output" / "cyprus_user_defined_results.html"
    write_html_report(output_path, prices, final_positions)

    print("Participants:", ", ".join(participant.participant_id for participant in participants))
    print("Forward contract:", contract.contract_id)
    print("DAM price range:", min(dam_result.prices.values()), max(dam_result.prices.values()))
    print("IDA1 price range:", min(ida1_result.prices.values()), max(ida1_result.prices.values()))
    print("Report:", output_path)


if __name__ == "__main__":
    main()
