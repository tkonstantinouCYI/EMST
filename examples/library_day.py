"""Runnable public API example: arrays by default, optional pandas DataFrames.

Run from the repository root: python examples/library_day.py [--dataframes]
Storage flags can be independently changed in the rules dictionary below.
"""
import argparse,math,json
from zoneinfo import ZoneInfo
from emst import Market,ThermalState,StorageState,Strategy,Forward


def build_market(dataframes=False,storage_energy=False,storage_capacity=False):
    rules={"id":"cyprus_isolated_example_v1", "storage":{
        "participation_balancing_energy":storage_energy,
        "participation_balancing_capacity":storage_capacity}}
    market=Market(ruleset=rules)
    index=market.time_index("2026-11-05")
    local=[t.astimezone(ZoneInfo('Europe/Nicosia')) for t in index]
    demand=[280+27*math.sin(2*math.pi*(t.hour+t.minute/60-7)/24) for t in local]
    solar=[115*max(0,math.sin(math.pi*(t.hour+t.minute/60-6)/12)) for t in local]
    actual_demand=[x+math.sin(i*.3) for i,x in enumerate(demand)]
    actual_solar=[x*.98 for x in solar]
    def series(values):
        if not dataframes:return values
        import pandas as pd
        return pd.DataFrame({'MW':values},index=index)
    for owner in ('producer_A','producer_B','producer_C'):
        market.add_participant(owner,strategies={'thermal':'marginal_cost'})
    for owner in ('supplier_T','supplier_D','solar_owner','storage_owner'):
        market.add_participant(owner)
    market.add_thermal('plant_A','producer_A',capacity_mw=180,minimum_mw=110,marginal_cost=65,ramp_mw_hour=1800,
        must_run_participation=True,must_run_offer_eur_per_mtu=80,
        startup_cost=350,reserve_cost=4,initial_state=ThermalState('online',160,360))
    market.add_thermal('plant_B','producer_B',capacity_mw=140,minimum_mw=70,marginal_cost=95,ramp_mw_hour=1500,
        must_run_participation=True,must_run_offer_eur_per_mtu=60,
        startup_cost=250,reserve_cost=2.5,initial_state=ThermalState('online',100,240))
    market.add_thermal('plant_C','producer_C',capacity_mw=100,minimum_mw=10,marginal_cost=145,ramp_mw_hour=1200,
        startup_cost=150,reserve_cost=3,initial_state=ThermalState('offline',0,600))
    market.add_supplier('load_T','supplier_T',demand_forecast=series([.6*x for x in demand]),actual_consumption=series([.6*x for x in actual_demand]))
    market.add_supplier('load_D','supplier_D',voltage='LV',small_supplier=True,
        demand_forecast=series([.4*x for x in demand]),actual_consumption=series([.4*x for x in actual_demand]))
    market.add_res('solar_portfolio','solar_owner',capacity_mw=115,grant_res=True,
        generation_forecast=series(solar),actual_generation=series(actual_solar))
    market.add_storage('battery','storage_owner',power_mw=10,capacity_mwh=40,
        initial_state=StorageState(power_mw=0,soc_mwh=20),strategy='fixed_circular',
        strategy_parameters={'buy_mwh':[2 if i in (10,11) else 0 for i in range(48)],
            'sell_mwh':[1.805 if i in (34,35) else 0 for i in range(48)],
            'balancing_up_price':100,'balancing_down_price':0})
    market.add_forward(Forward('bilateral','producer_A','supplier_T','plant_A',tuple([15.]*48),72,buyer_entity='load_T'))
    # Scenario data: requirements and supplied historical prices are not rules.
    requirements={p:{direction:series([value]*48) for direction,value in directions.items()} for p,directions in {'FCR':{'up':2,'down':2},'aFRR':{'up':3,'down':3},'mFRR':{'up':4,'down':2}}.items()}
    history={i:[[80.+day*10+rtu for rtu in range(6)] for day in range(3)] for i in range(48)}
    return market,index,requirements,history


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataframes',action='store_true')
    parser.add_argument('--storage-energy',action='store_true');parser.add_argument('--storage-capacity',action='store_true')
    parser.add_argument('--output',default='output/public_library_day');args=parser.parse_args()
    market,index,requirements,history=build_market(args.dataframes,args.storage_energy,args.storage_capacity)
    # A user supplies TSO forecasts independently of participants' bid forecasts.
    tso={'demand':[sum(float(a.forecast.iloc[t,0] if args.dataframes else a.forecast[t]) for a in market.assets.values() if a.technology=='supplier')+1 for t in range(48)],
         'res':[float(market.assets['solar_portfolio'].forecast.iloc[t,0] if args.dataframes else market.assets['solar_portfolio'].forecast[t])*.99 for t in range(48)]}
    results=market.run(time_index=index,must_run_requirement_mw=[180.]*48,system_forecasts=tso,reserve_requirements=requirements,historical_rtbm_prices=history)
    results.save(args.output)
    readable = results.save_readable(args.output)
    print(f"Readable results: {readable['operator']}")
    print(f"Shared summaries: {readable['shared']}")
    print(json.dumps(dict(passed=results.audit['passed'],checks=results.audit['checks'],
        physical_imbalance_mwh=results.manifest['physical_imbalance_mwh'],ending_states=results.ending_states),indent=2))
    if not results.audit['passed']:raise SystemExit(1)


if __name__=='__main__':main()
