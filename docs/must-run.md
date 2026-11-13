# MW-based must-run auctions

This optional research model procures minimum stable thermal generation. It does
not model inertia, minimum entity/site counts, network security, emergency
replacement auctions, must-run-specific penalties or the official reserve-offer
restrictions. Public examples use synthetic bids and a 180 MW requirement.

## Inputs and clearing

Register each participating unit (one thermal per participant) with:

```python
market.add_thermal(
    "unit", "generator", capacity_mw=300, minimum_mw=180, marginal_cost=60,
    initial_state=ThermalState("dispatch", 240, 480),
    must_run_participation=True, must_run_offer_eur_per_mtu=100,
)
```

`must_run_participation` defaults to `False`; the nonnegative service bid defaults
to zero EUR/unit/MTU and is constant for the run. A participating unit must have a
positive `minimum_mw`. Participation makes a unit eligible; it does not guarantee
an award. Other technology types are not currently eligible.

```python
results = market.run(
    time_index=market.time_index("2026-11-05"),
    must_run_requirement_mw=[180.0] * 48,
    historical_rtbm_prices=history,
)
```

Use arrays or correctly indexed Series/single-column DataFrames, as for other MW
inputs. Requirements must be finite and nonnegative, with one value per MTU.
Omitting the requirement disables procurement; a zero requirement awards no units.
Requirements above the sum of eligible units' minimum stable MW are rejected.

For every unit and MTU the auction chooses a binary award. It minimises the sum
of accepted EUR/unit/MTU bids subject to
`sum(minimum_mw * award) >= requirement_mw`. Contributions are indivisible; excess
coverage is possible and is also allocated and paid. Equal-cost optimal selections
need not be unique. Bids are service fees, not EUR/MWh energy prices.

## Energy, dispatch and settlement

Clearing occurs before forward-contract processing. Each selected unit receives
an inherent-energy position of `minimum_mw * 0.5` MWh. Suppliers receive the same
total quantity on the consumption side. Additional supplied forward contracts are
added to these positions; DAM offers cover the residual position. An allocation
above a supplier's forecast demand (or a forward position above eligible supply)
is rejected rather than silently clipped.

Default daily supplier shares use each participant's total forecast consumption.
To use a known historical/reference allocation instead, pass:

```python
must_run_supplier_shares = {"supplier_A": 0.6, "supplier_B": 0.4}
```

Pass this dictionary as `must_run_supplier_shares` to `run`, or in each daily
scenario. It must cover supplier participant IDs exactly and sum to one. A
participant's energy is split across its supplier assets using same-MTU forecasts.
No actual consumption enters auction clearing or default shares.

Both ISP runs force awarded units into the dispatch phase at or above minimum
output. RTBM retains that phase and enforces minimum average output in each
five-minute interval; the existing ramp trajectories and initial boundary still
apply. Non-awarded units remain available for normal clearing. Auction selection
does not co-optimise startup, minimum dwell times, ramps or energy/reserve
feasibility. If selected awards conflict with those constraints, dispatch fails;
no automatic replacement or relaxation of the awards is performed. Provide
feasible initial states and requirements; explicitly model startup before an
award where necessary.

Settlement uses a separate `MUST-RUN` account:

- **S24** credits awarded producers their accepted service bid per MTU.
- **S25** records inherent generation and supplier energy at zero direct price.
- **S26** charges suppliers the service cost using the same fixed shares.

Service costs are fully recovered each MTU, outside UA1/UA2. The zero-price energy
is not additionally remunerated as an ordinary forward contract. Normal DAM,
balancing energy, reserve and other settlement calculations continue independently.

## Monthly execution and results

`run_period(..., must_run_auction_frequency="monthly")` is the default. Put
`must_run_requirement_mw` in each day's scenario and provide the complete month's
requirements and supplier forecasts before clearing. A single auction model covers
all that month's MTUs; awards may differ between MTUs. Default supplier shares use
the whole month's forecast consumption, or supplied shares constant across that
month. Physical daily dispatch does not look ahead into the next day.

Monthly awards and supplier shares are checkpointed. Resuming reuses them and
rejects changed requirements/shares. Select `must_run_auction_frequency="daily"`
to clear independent daily auctions in a multi-day study. Omitting requirements
throughout the scenario preserves existing runs without must-run procurement.

Raw results retain awards and allocations in `inputs.must_run` and postings in
`ledger`. Readable results add `market_stages.must_run_auction`:

- Shared summary: auction ID/frequency, requirement, awarded MW, inherent MWh and
  aggregate service cost. Participant identities, bids and allocations are private.
- Participant view: own awards, own supplier allocations, fixed supplier share,
  cash by component and individual postings.
- Settlement financial positions: `must_run_cash_eur` per MTU and in daily/monthly
  totals, counted once alongside the other stages and UA1/UA2.

Monthly closure carries S24–S26 unchanged from daily accruals without meter
reconciliation. Run `examples/library_day.py` and `examples/library_month.py` for
complete runnable examples, saving raw and readable outputs separately.
