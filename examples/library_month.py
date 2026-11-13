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
        "unit", "generator", capacity_mw=300, minimum_mw=180, marginal_cost=60,
        initial_state=ThermalState("dispatch", 240, 480),
        must_run_participation=True,must_run_offer_eur_per_mtu=100,
    )
    market.add_res("solar", "solar_owner", generation_forecast=[10] * 48,
                   actual_generation=[9] * 48)
    market.add_supplier("load", "supplier", demand_forecast=[250] * 48,
                        actual_consumption=[251] * 48)

    history = {period: [[80] * 6 for _ in range(3)] for period in range(48)}
    scenario = {
        f"2026-11-{day:02}": {
            "forecasts": {"solar": [10] * 48, "load": [250] * 48},
            "actuals": {"solar": [9] * 48, "load": [251] * 48},
            "historical_rtbm_prices": history,
            "must_run_requirement_mw": [180.] * 48,
            "reserve_requirements": {"FCR": {"up": [2] * 48, "down": [2] * 48}},
        }
        for day in range(1, 31)
    }
    results = market.run_period(
        start="2026-11-01", end="2026-11-30", scenario=scenario,
        checkpoint_directory=args.output,
    )
    # Penalty proceeds, if present, are credited through UA1.
    monthly = results.settle_month(
        "2026-11",
    )
    components = {entry["component"] for entry in monthly["ledger"]}
    if (monthly["monthly"]["days"] != 30 or not {"S16", "S17"} <= components
            or {"S18", "S19", "S20", "S21", "S22", "S31"} & components or monthly["reconciliation"] is not None):
        raise RuntimeError("Unexpected monthly settlement coverage")

    # Must-run accruals are retained exactly at closure, outside UA1 and UA2.
    auction_ids={day.inputs['must_run']['auction_id'] for day in results.days.values()}
    if len(auction_ids)!=1 or any(not day.audit['passed'] for day in results.days.values()):
        raise RuntimeError('Must-run monthly clearing or stage audit failed')
    daily_must_run={code:sum((Decimal(e['cash_eur']) for day in results.days.values()
        for e in day.ledger if e['component']==code),Decimal(0)) for code in ('S24','S25','S26')}
    closed_must_run={code:sum((Decimal(e['cash_eur']) for e in monthly['ledger']
        if e['component']==code),Decimal(0)) for code in daily_must_run}
    if daily_must_run!=closed_must_run or closed_must_run!={'S24':Decimal(144000),'S25':Decimal(0),'S26':Decimal(-144000)}:
        raise RuntimeError('Must-run service payment/recovery does not match monthly closure')
    verification=dict(passed=True,days=30,mtus=1440,rtus=8640,auction_ids=sorted(auction_ids),
        all_daily_stage_audits_passed=True,must_run_cash_eur={k:str(v) for k,v in closed_must_run.items()},
        monthly_matches_daily_must_run=True,meter_reconciliation=False)
    (args.output/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')

    # Keep checkpoint result.json files intact; readable reports are separate.
    for day, daily in results.days.items():
        daily.save_readable(args.output / day)

    combined = results.save_readable(args.output, monthly=monthly)
    print(f"Combined monthly readable report: {combined['operator']}")

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
