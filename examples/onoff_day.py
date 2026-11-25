"""Single-envelope CCGT and optional native hourly ON/OFF ISP, synthetic data."""
import argparse,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emst import Market,ThermalState,Forward


def build_market(units=2):
    if units<2:raise ValueError('At least a CCGT and backup unit are required')
    m=Market();m.add_participant('cc_owner');m.add_participant('supplier');m.add_participant('solar_owner')
    m.add_thermal('ccgt','cc_owner',capacity_mw=220,minimum_mw=75,marginal_cost=60,
        ramp_mw_hour=720,minimum_up_minutes=120,minimum_down_minutes=120,startup_cost=5000,reserve_cost=2,
        initial_state=ThermalState('dispatch',150,1440),commitment_model='on_off')
    for i in range(units-1):
        name='backup' if i==0 else f'peaker_{i}'
        m.add_participant(name+'_owner')
        m.add_thermal(name,name+'_owner',capacity_mw=140 if i==0 else 40,minimum_mw=10,
            marginal_cost=120+5*i,ramp_mw_hour=600,minimum_up_minutes=60,minimum_down_minutes=60,
            startup_cost=500,reserve_cost=3,commitment_model='on_off',
            initial_state=ThermalState('dispatch' if i==0 else 'offline',40 if i==0 else 0,1440))
    demand=[200+35*math.sin(2*math.pi*(i-8)/48) for i in range(48)]
    res=[60*max(0,math.sin(math.pi*(i-12)/24)) for i in range(48)]
    m.add_supplier('load','supplier',demand_forecast=demand,actual_consumption=demand)
    m.add_res('solar','solar_owner',generation_forecast=res,actual_generation=res,actual_generation_kind='available',capacity_mw=60)
    m.add_forward(Forward('bilateral','cc_owner','supplier','ccgt',tuple([20.]*48),70,buyer_entity='load'))
    requirements={p:{d:[value]*48 for d,value in directions.items()} for p,directions in
        {'FCR':{'up':2,'down':2},'aFRR':{'up':3,'down':2},'mFRR':{'up':4,'down':2}}.items()}
    history={i:[[80.]*6 for _ in range(3)] for i in range(48)}
    return m,requirements,history


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--isp-interval-minutes',type=int,choices=(30,60),default=30)
    p.add_argument('--output',type=Path,default=Path('output/onoff-day'))
    args=p.parse_args();m,requirements,history=build_market()
    r=m.run(time_index=m.time_index('2026-11-05'),reserve_requirements=requirements,
        historical_rtbm_prices=history,isp_interval_minutes=args.isp_interval_minutes,rtbm_interval_minutes=30)
    assert r.audit['passed'],r.audit
    assert all(x['reconciled'] for x in r.economics['participants'].values())
    r.save(args.output/'raw');r.save_readable(args.output)
    report=dict(passed=r.audit['passed'],isp_interval_minutes=args.isp_interval_minutes,
        isp_intervals=[len(run['rows']) for run in r.isp_runs],thermal_binaries=[run['thermal_state_binary_count'] for run in r.isp_runs],
        physical_security_feasible=r.manifest['physical_security_feasible'],ending_states=r.ending_states,
        delivery_residual=r.diagnostics['actual_balance'])
    (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
