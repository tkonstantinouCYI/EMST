# Historical Cyprus daily scenario: 14 October 2026

This is a public-data historical study with representative participants and
assumed offers. It is not a reconstruction of confidential bids, historical unit
availability, official settlements or observed dispatch. The ordinary synthetic
daily example and clearing/settlement engines are unchanged.

## Run and inspect

Install the current package with `python -m pip install ".[audit]"`, then run:

```sh
python examples/historical_cyprus_day.py --output output/historical-cyprus/2026-10-14
```

The example reads the bundled `examples/data/cyprus_2026_10_14.json`; it needs no
internet connection or Excel package. The output contains `raw_results.json`,
individual raw stage files under `raw/`, `readable_results.json`, shared summaries,
private participant reports, `scenario_inputs.json` and `verification.json`.
Participant financial positions contain forward, DAM, ISP, RTBM, other settlement,
UA1 and UA2 cash by MTU and daily totals. Original source files can be retrieved:

```sh
python tools/prepare_cyprus_historical_day.py
```

Downloads are cached in `output/historical-cyprus/sources/`. Each used workbook's
URL, retrieval time and SHA256 are recorded in the extracted dataset. The tool
reads cached workbook values without modifying the source files. It checks date
grids, finite numeric values and all 48 MTUs / 288 historical price intervals.
The selected date is an ordinary 48-MTU day with complete selected series.

## Public inputs

- [TSOC ISP forecast archive](https://tsoc.org.cy/competitive-electricity-market/forecast-results/load_and_res_forecast/):
  `ISP_FCST_20261014.xlsx`, sheet containing forecast data, rows 2–49. Columns
  E/F/G/H are total demand, PV, wind and total RES in MW. I/J/K are upward
  FCR/aFRR/mFRR requirements; L/M/N are their downward requirements. O is the
  published minimum must-run requirement, retained as reference data only.
- [TSOC balancing activity reports](https://tsoc.org.cy/competitive-electricity-market/mms-reports/balancing-market-daily-activity-reports-en/):
  report for 14 October, PT30M row 3 is Expost System Load, rows 5/7/8 are Oil,
  Solar and Wind actual generation. All 48 half-hour values are preserved in MW.
  Biomass and Gas are zero in this day's report. Oil generation is a benchmark,
  not an imposed thermal dispatch schedule.
- Reports for 9, 12 and 13 October supply three preceding weekday PT5M marginal
  price profiles. For each interval, use the upward marginal price when published
  upward activation is at least downward activation, otherwise downward. This is
  an explicit historical-price proxy, not a verified published imbalance price or
  LABEO series. It is used only by settlement's historical fallback, never to
  generate bids or forecast the current day's prices.
- [TSOC DAM activity reports](https://tsoc.org.cy/competitive-electricity-market/mms-reports/day-ahead-market-daily-activity-reports-en/):
  the 48 ALL rows provide observed prices and auction volumes for comparison only.
  These outputs do not enter the simulation's clearing or bidding inputs.
- [Government NECP consultation, Table 36](https://e-consultation.gov.cy/wp-content/uploads/2024/11/241120_NECP_v.1.1_diabouleush.pdf)
  provides the fleet capacity basis: Vasilikos 2x220 MW CCGT, 3x130 MW steam,
  1x38 MW gas turbine; Dhekelia 6x60 MW steam, 2x51 MW engine groups; Moni
  4x37.5 MW gas turbines. The full published fleet totals 1,480 MW. The example selects eight units
  totalling 931 MW: two CCGTs, VASS_ST_2/3, DHEK_ST_2/3/4 and one 51 MW engine
  group. These are planning/nameplate capacities and a representative selection,
  not a verified operational inventory or availability declaration for this date.

## Documented assumptions and exclusions

1. **Participants:** every thermal unit has its own representative owner. Two
   fictitious suppliers split demand 60%/40% with HV/LV tariff classifications.
   Solar and wind each have one representative non-grant-supported portfolio.
   No actual commercial portfolios or supplier market shares are inferred.
2. **Forecasts:** participant forecasts use the published system technology
   forecasts; TSO forecasts are supplied independently with the same aggregate.
   The baseline stays unchanged in the 11:00 rerun, as forecast revisions are
   outside this example's scope. The downloaded forecast snapshot does not prove
   which file version was available at each historical gate closure.
3. **Actual resolution:** repeat each published half-hour actual MW value over
   its six RTUs, preserving half-hour energy exactly. These are reconstructed
   five-minute observations, not actual five-minute measurements. Causal error
   persistence uses only the preceding completed interval. There is no previous
   system observation for the first RTU.
4. **RES:** use `actual_generation_kind="metered"`. Public production is delivered
   generation, not unconstrained resource availability. Preserve it without
   removing further simulated curtailment from the actual meter. Instructed and
   uninstructed quantities are still separated by the existing settlement bridge.
   This cannot validate historical RES availability or instruction compliance.
   The report has nonzero Solar entries overnight; they are retained as reported,
   without claiming they represent pure physical PV output. Aggregate technology
   generation also need not sum to the Expost System Load definition; the source
   difference is not silently filled with invented generation.
5. **Availability:** all eight selected units are available and assumed committed for the
   whole day, consistent with
   the library's current constant-availability assumption. Historical outages and
   CCGT configuration switching are not reconstructed. No interconnections,
   intraday auctions, RR procurement or additional must-run policy constraints
   are modelled. There is no storage in this historical baseline.
6. **Thermal parameters:** the following are assumptions, not official offers:

   | Technology | Minimum MW per unit | Base energy cost EUR/MWh | Ramp MW/h | Startup EUR | Minimum up minutes |
   |---|---:|---:|---:|---:|---:|
   | Vasilikos CCGT | 66 | 210 | 1,500 | 4,000 | 1,440 |
   | Vasilikos steam | 39 | 240 | 300 | 2,000 | 1,440 |
   | Dhekelia engines | 15.3 | 260 | 600 | 500 | 1,440 |
   | Dhekelia steam | 18 | 300 | 180 | 1,000 | 1,440 |

   Minimum output is 30% of nameplate. Add the zero-based unit index (0–7)
   to the base energy cost to distinguish otherwise identical units. This is a
   declared modelling choice, not an observed cost difference. The assumed ramp
   rates also bound reserve response; the CCGT assumption allows the selected
   fleet to supply the published 36 MW FCR requirement within 30 seconds.
   Minimum down time is 60 minutes. Reserve offers are EUR5/MW/MTU in both
   directions. Balancing energy offers use the indexed cost+15 upward and cost-15 downward.
   DAM uses marginal-cost offers, supplier demand bids and forecast RES offers.
   Default thermal transition profiles are used; measured startup trajectories
   are unavailable. No bids are calibrated against the same day's observed price.
7. **Initial state:** distribute the previous day's last reported Oil average MW
   over all eight units in proportion to their capacities. Set dispatch elapsed
   time to zero and minimum up time to 1,440 minutes to impose a fixed committed
   fleet for this baseline. These are explicit boundary/commitment assumptions,
   not observed startup times or declared physical minimum up times. This average is an
   assumed boundary-power proxy, not a measured endpoint. Reported thermal output
   during the simulated day is generated by RTBM, as in the existing library.
8. **Forward contract:** an assumed 15 MWh in every MTU at EUR230/MWh from
   VASS_CCGT_1_owner to supplier_T, attributed to load_T. This is not a reported
   historical bilateral contract.
9. **History cleaning:** ten selected price entries equal 999999.0 and are treated
   as missing/sentinel data. Each is replaced with the preceding valid selected
   price within that historical day; every substitution is listed in
   `history_price_fills`. An initial EUR250 fallback is implemented but not used
   by this dataset. The official [report issues note](https://tsoc.org.cy/files/market-system/market/REPORTS_ISSUES.pdf)
   also identifies sentinel/blank-price issues; the particular 999999 spelling
   and carry-forward treatment here are explicitly our cleaning convention.
10. **Settlement:** the library's default synthetic tariff/parameter bundle,
    tolerance configuration and UA2 zero-deviation fallback remain in effect.
    These amounts are not represented as the historical approved tariffs.
    Daily settlement includes UA1/UA2; UA3/UA4 and meter reconciliation are excluded.

## Verification and interpretation

`verification.json` includes the library's stage audits plus independent checks
of full demand acceptance, source-energy preservation, absence of ISP/RTBM slack,
participant cashflow reconciliation, UA1/UA2 cost recovery and shared-report
privacy. Any failed check exits with an error while retaining results for review.
The observed-vs-simulated DAM price difference is a benchmark diagnostic, not a
test that clearing prices must match. Forecast errors can leave physical energy
imbalances even with feasible dispatch and zero optimisation slack; those residuals
are reported explicitly and are not labelled as measured historical imbalances.
