"""Reproduce RES delivery above the commercial schedule with explicit curtailment."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from emst import Market,ThermalState

HISTORY={t:[[80.]*6 for _ in range(3)] for t in range(48)}

def scenario(demand=100,*,kind='available',output=100,known_curtailment=None,hourly=False):
    m=Market();m.add_participant('solar_owner');m.add_participant('supplier')
    m.add_res('solar','solar_owner',generation_forecast=[100.]*48,
        actual_generation=[float(output)]*48,capacity_mw=100,energy_down_price=-5,
        actual_generation_kind=kind,metered_curtailment_mw=known_curtailment)
    m.add_supplier('load','supplier',demand_forecast=[20.]*48,actual_consumption=[float(demand)]*48)
    if hourly:
        m.add_participant('backup_owner')
        m.add_thermal('backup','backup_owner',capacity_mw=100,minimum_mw=0,marginal_cost=200,
            initial_state=ThermalState('offline',0,1440),commitment_model='on_off',no_load_cost_per_hour=1)
    return m,dict(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
        system_forecasts={'demand':[float(demand)]*48,'res':[100.]*48})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtbm-interval-minutes',type=int,choices=(5,30),default=30)
    parser.add_argument('--isp-interval-minutes',type=int,choices=(30,60),default=30)
    parser.add_argument('--output',default='output/res-availability')
    args=parser.parse_args()
    if args.isp_interval_minutes==60 and args.rtbm_interval_minutes!=30:
        parser.error('Hourly ON/OFF ISP requires 30-minute RTBM')
    summary=[]
    for demand in (100,60):
        market,kwargs=scenario(demand,hourly=args.isp_interval_minutes==60)
        result=market.run(**kwargs,rtbm_interval_minutes=args.rtbm_interval_minutes,isp_interval_minutes=args.isp_interval_minutes)
        assert result.audit['passed'],result.audit
        assert not result.manifest['RTBM_slack_used'],result.manifest
        target=Path(args.output)/str(demand);result.save(target/'raw');result.save_readable(target)
        summary.append(dict(demand_mw=demand,first_RTBM=result.rtbm[0]['assets']['solar'],
            first_MTU_settlement=next(x for x in result.meters if x['asset']=='solar'),audit_passed=True))
    Path(args.output).mkdir(parents=True,exist_ok=True)
    (Path(args.output)/'verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
