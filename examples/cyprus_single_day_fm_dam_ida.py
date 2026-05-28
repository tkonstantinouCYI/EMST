from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mst.core.simulation import SequentialMarketSimulation
from mst.systems.cyprus import CyprusMarketConfig


def main() -> None:
    config = CyprusMarketConfig.default()
    scenario = config.load_scenario("high_solar_day")
    participants = config.load_participants()

    simulation = SequentialMarketSimulation(
        config=config,
        participants=participants,
        scenario=scenario,
    )
    results = simulation.run()

    print("Price summary")
    for market, prices in results.price_summary.items():
        first_hours = {period: prices[period] for period in range(1, 7)}
        print(f"{market}: {first_hours} ...")

    print("\nFinal position totals")
    for participant_id, positions in results.final_positions.items():
        print(f"{participant_id}: {sum(positions.values()):.2f} MWh")


if __name__ == "__main__":
    main()
