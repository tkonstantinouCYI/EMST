# Cyprus Configuration

`CyprusMarketConfig` is the first predefined EMST system configuration.

It includes:

- thermal production participants,
- solar and wind aggregators,
- one battery energy storage system,
- one system-demand participant,
- one high-solar single-day scenario,
- market sequence `FM -> DAM -> IDA1 -> IDA2 -> IDA3`.

The configuration is deliberately simple. It is designed to demonstrate the library architecture and sequential residual-position logic, not to reproduce all Cyprus market rules.

## MTU Resolution

`CyprusMarketConfig.default()` accepts `mtu_minutes`.

```python
CyprusMarketConfig.default(mtu_minutes=60)  # 24 hourly MTUs
CyprusMarketConfig.default(mtu_minutes=30)  # 48 half-hour MTUs
CyprusMarketConfig.default(mtu_minutes=15)  # 96 quarter-hour MTUs
```

The predefined Cyprus profiles are held as hourly source values and expanded to MTU-energy quantities. Thermal and storage capacities are also scaled by MTU duration when bids are generated.

## Forward Market

The v0.1 forward market is exogenous. `CyprusMarketConfig.load_forward_contracts()` returns bilateral contracts, and the forward market converts them into participant net positions. These positions affect residual quantities in DAM and subsequent IDAs.

## DAM And IDA Forecasts

The predefined scenario includes market-specific forecasts. DAM uses the base forecast, while IDA1, IDA2, and IDA3 introduce small demand, solar, and wind forecast updates. This allows the same MTU to be re-traded across markets.

## Future Cyprus Extensions

Future versions can add richer Cyprus market rules, calibrated unit data, external imports, reserve products, balancing-market logic, and validation cases.
