"""Run a synthetic November trading month and close UA-1 and UA-2 settlement."""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from emst import Market, ThermalState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("month-results"))
    args = parser.parse_args()

    market = Market()
    for name in ("generator", "solar_owner", "supplier"):
        market.add_participant(name)
    market.add_thermal(
        "unit", "generator", capacity_mw=100, minimum_mw=10, marginal_cost=60,
        initial_state=ThermalState("dispatch", 40, 480),
    )
    market.add_res("solar", "solar_owner", generation_forecast=[10] * 48,
                   actual_generation=[9] * 48)
    market.add_supplier("load", "supplier", demand_forecast=[50] * 48,
                        actual_consumption=[51] * 48)

    history = {period: [[80] * 6 for _ in range(3)] for period in range(48)}
    scenario = {
        f"2026-11-{day:02}": {
            "forecasts": {"solar": [10] * 48, "load": [50] * 48},
            "actuals": {"solar": [9] * 48, "load": [51] * 48},
            "historical_rtbm_prices": history,
            "reserve_requirements": {"FCR": {"up": [2] * 48, "down": [2] * 48}},
        }
        for day in range(1, 31)
    }
    results = market.run_period(
        start="2026-11-01", end="2026-11-30", scenario=scenario,
        checkpoint_directory=args.output,
    )
    # These penalty redistribution shares are synthetic scenario inputs.
    monthly = results.settle_month(
        "2026-11", penalty_shares={"UA-1": 1, "UA-6": 0, "UA-7": 0},
    )
    components = {entry["component"] for entry in monthly["ledger"]}
    if (monthly["monthly"]["days"] != 30 or not {"S16", "S17"} <= components
            or {"S18", "S19", "S31"} & components or monthly["reconciliation"] is not None):
        raise RuntimeError("Unexpected monthly settlement coverage")

    # Keep checkpoint result.json files intact; readable reports are separate.
    for day, daily in results.days.items():
        daily.save_readable(args.output / day)

    output = args.output / "monthly.json"
    output.write_text(json.dumps(monthly, indent=2), encoding="utf-8")
    print(f"Settled {monthly['monthly']['days']} days. Ledger written to {output}")
    print(f"Daily readable reports with MTU financial positions: {args.output}/YYYY-MM-DD/readable_results.json")
    for name, code in (("UA-1", "S16"), ("UA-2", "S17")):
        cash = sum(
            Decimal(entry["cash_eur"]) for entry in monthly["ledger"]
            if entry["component"] == code and entry["account"].startswith("participant:")
        )
        direction = "credit" if cash >= 0 else "charge"
        print(f"{name}: participant {direction} of EUR {abs(cash):.2f}")


if __name__ == "__main__":
    main()
