"""Full public-API day with sync, soak, dispatch and desync actually exercised."""
import json
from emst import Market,ThermalState


def build_market(start_period=0):
    market=Market();index=market.time_index('2026-11-05')
    for participant in ('thermal_owner','backup_owner','supplier'):market.add_participant(participant)
    market.add_thermal('thermal','thermal_owner',capacity_mw=60,minimum_mw=20,marginal_cost=10,
        ramp_mw_hour=600,startup_cost=300,initial_state=ThermalState('offline',0,600),
        phase_profiles_mw={'sync':[0],'soak':[10,20],'desync':[10,0]})
    market.add_thermal('backup','backup_owner',capacity_mw=100,minimum_mw=0,marginal_cost=100,
        reserve_cost=10,ramp_mw_hour=6000,initial_state=ThermalState('dispatch',20 if start_period==0 else 1,600))
    endpoints=[0,10,20,40,20,10,0];prior=0;system_demand=[1.]*(start_period*6)
    for phase_period,target in enumerate(endpoints):
        baseline=1 if start_period and phase_period<3 else 20
        system_demand.extend(baseline+prior+(target-prior)*(k+.5)/6 for k in range(6));prior=target
    system_demand.extend([10.]*(288-len(system_demand)))
    demand=[sum(system_demand[i:i+6])/6 for i in range(0,288,6)]
    market.add_supplier('load','supplier',demand_forecast=demand,actual_consumption=system_demand)
    history={t:[[80.]*6 for _ in range(3)] for t in range(48)}
    return market,index,system_demand,history


def main():
    market,index,demand,history=build_market()
    result=market.run(time_index=index,system_forecasts={'demand':demand,'res':[0.]*288},
        reserve_requirements={'FCR':{'up':[1. if t in (3,4) else 0 for t in range(48)],'down':[0.]*48}},
        historical_rtbm_prices=history)
    phases=[r['assets']['thermal']['phase'] for r in result.isp_runs[0]['rows']]
    required={'sync','soak','dispatch','desync','offline'}
    if not required<=set(phases):raise AssertionError(phases)
    if sum(r['assets']['thermal']['start'] for r in result.rtbm)!=1:raise AssertionError('Startup count')
    result.save('output/four_phase_cycle_day')
    print(json.dumps(dict(passed=result.audit['passed'],phases_first_eight=phases[:8],
        objective_components_eur=result.isp_runs[0]['objective_components_eur']),indent=2))
    if not result.audit['passed']:raise SystemExit(1)


if __name__=='__main__':main()
