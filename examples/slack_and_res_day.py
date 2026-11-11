"""RES market-curtailment day and an explicitly infeasible-without-slack day."""
import json
from emst import Market,ThermalState


def res_case(*,bid_demand=100,kind='available',actual=100):
    m=Market();m.add_participant('solar_owner');m.add_participant('supplier')
    m.add_res('solar','solar_owner',generation_forecast=[100.]*48,actual_generation=[float(actual)]*48,
        capacity_mw=100,energy_down_price=-5,actual_generation_kind=kind)
    m.add_supplier('load','supplier',demand_forecast=[float(bid_demand)]*48,actual_consumption=[40.]*48)
    inputs=dict(time_index=m.time_index('2026-11-05'),system_forecasts={'demand':[40.]*48,'res':[100.]*48},
        historical_rtbm_prices={t:[[80.]*6 for _ in range(3)] for t in range(48)})
    return m,inputs


def shortage_case():
    m=Market({'slack':{'energy_shortage_eur_mwh':1000.,'reserve_shortage_eur_mw_mtu':{'FCR_up':100.}}})
    m.add_participant('generator');m.add_participant('supplier')
    m.add_thermal('unit','generator',capacity_mw=50,minimum_mw=0,marginal_cost=50,
        initial_state=ThermalState('dispatch',50,600))
    m.add_supplier('load','supplier',demand_forecast=[80.]*48,actual_consumption=[80.]*48)
    inputs=dict(time_index=m.time_index('2026-11-05'),system_forecasts={'demand':[80.]*48,'res':[0.]*48},
        reserve_requirements={'FCR':{'up':[10.]*48,'down':[0.]*48}},
        historical_rtbm_prices={t:[[80.]*6 for _ in range(3)] for t in range(48)})
    return m,inputs


def main():
    for name,builder in (('res_down_day',res_case),('res_DAM_curtailment_day',lambda:res_case(bid_demand=40)),
                         ('slack_shortage_day',shortage_case)):
        m,inputs=builder();result=m.run(**inputs);result.save('output/'+name)
        if not result.audit['passed']:raise AssertionError(result.audit)
        print(json.dumps(dict(case=name,audit_passed=True,solution_class=result.manifest['solution_class'],
            energy_shortage_mwh=result.manifest['RTBM_energy_shortage_mwh'],
            ISP_max_reserve_shortage_mw=result.manifest['ISP_max_reserve_shortage_mw_by_run']),indent=2))


if __name__=='__main__':main()
