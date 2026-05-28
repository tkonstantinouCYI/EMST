# Architecture

EMST separates generic market logic from system-specific configuration.

## Package Layout

- `mst.core` contains dataclasses, time indexing, results, and sequential orchestration.
- `mst.markets` contains generic market modules for FM, DAM, and intraday auctions.
- `mst.solvers` contains solver abstractions and the PuLP implementation.
- `mst.systems.cyprus` contains the first predefined system configuration.

## Market Sequence

The v0.1 market sequence is:

```text
FM -> DAM -> IDA1 -> IDA2 -> IDA3
```

The Forward Market loads exogenous schedules. DAM clears residual positions after FM. Each IDA then clears updated residual positions after all previous markets.

## Generic Logic Versus Cyprus Configuration

Generic modules know about participants, bids, positions, periods, and welfare clearing. They do not know Cyprus-specific assets, demand profiles, market-stage names, or forecast assumptions.

`CyprusMarketConfig` supplies:

- default participants and assets,
- default thermal, RES, BESS, and demand assumptions,
- predefined single-day scenarios,
- forward schedules,
- market-stage configuration.

## Same-MTU Inter-Market Arbitrage

Inter-market arbitrage is represented through cumulative participant positions for the same MTU. After each market clears, accepted quantities are added to participant positions. Later markets bid only residual quantities against updated forecasts and previous commitments.

For example, if a participant sells in DAM and the IDA forecast changes, the participant can buy or sell in an IDA to adjust the same hourly MTU position.

## DAM Pricing

DAM and IDA clearing first solve the welfare-maximizing MILP. The accepted binary block variables are then fixed, the resulting LP is re-solved, and clearing prices are read from the shadow prices of the period balance constraints. Paradoxically accepted blocks are rejected iteratively before the final solution is returned.

## Deferred Modules

Balancing markets, reserve procurement, ISP, RTBM, and real-time redispatch are deliberately excluded from v0.1. The package structure leaves room for these as future extensions without coupling them into the energy-exchange clearing engine.
