"""Reproducible fictional Cyprus participants, forecasts and prior assessments."""
from datetime import date,datetime,time,timedelta
import math
from copy import deepcopy
from .relaxation import SLACK_DEFAULTS
from .calendar import day_intervals,CYPRUS,UTC
from .model import Unit,Registration,Forward,Order,Forecast,PRODUCTS


def synthetic_day(day=date(2026,11,5)):
    from .history import synthetic_tariffs
    periods,rtus = day_intervals(day,30),day_intervals(day,5)
    n = len(periods)
    units = [
        Unit("thermal_A","generator_A",180,20,65,1800,160,1,12,
             startup_eur=350,reserve_eur_mw_period={p:4 for p in PRODUCTS},reserve_limit_mw=22,startup_limit_mw=90,shutdown_limit_mw=90),
        Unit("thermal_B","generator_B",140,15,95,1500,100,1,8,agc_min_mw=20,
             startup_eur=250,reserve_eur_mw_period={p:2.5 for p in PRODUCTS},reserve_limit_mw=20,startup_limit_mw=70,shutdown_limit_mw=70),
        Unit("thermal_C","generator_C",100,10,145,1200,0,0,20,agc_min_mw=15,
             startup_eur=150,reserve_eur_mw_period={p:3 for p in PRODUCTS},reserve_limit_mw=18,startup_limit_mw=50,shutdown_limit_mw=50)]
    regs = {u.id:Registration(u.id,u.participant,f"BRP_{u.id}",f"CM_{u.participant}",f"meter_{u.id}","production","HV") for u in units}
    regs.update({
        "res":Registration("res","RES_portfolio","BRP_RES","CM_RES","meter_RES","production","MV",grant_res=True),
        "load_T":Registration("load_T","supplier_T","BRP_T","CM_T","meter_T","offtake","HV",consumption_share=.6),
        "load_D":Registration("load_D","supplier_D","BRP_D","CM_D","meter_D","offtake","LV",tolerance=.03,small_supplier=True,consumption_share=.4)})
    regs["battery_injection"]=Registration("battery_injection","storage_BSP","BRP_storage","CM_storage","meter_battery_injection","production","MV",tolerance=0)
    regs["battery_load"]=Registration("battery_load","storage_BSP","BRP_storage","CM_storage","meter_battery_load","offtake","MV",tolerance=0)
    charge=[2. if i in (10,11) else 0. for i in range(n)]
    discharge=[1.805 if i in (34,35) else 0. for i in range(n)]
    demand = [280+27*math.sin(2*math.pi*(t.local.hour+t.local.minute/60-7)/24) for t in periods]
    renewable = [115*max(0,math.sin(math.pi*(t.local.hour+t.local.minute/60-6)/12)) for t in periods]
    previous = datetime.combine(day-timedelta(days=1),time(12),CYPRUS)
    initial_cutoff = datetime.combine(day-timedelta(days=1),time(16),CYPRUS)
    rerun_cutoff = datetime.combine(day,time(11),CYPRUS)
    da_d = Forecast(previous,previous,tuple(demand),"DA_demand_v1")
    da_r = Forecast(previous,previous,tuple(renewable),"DA_RES_v1")
    revised_demand = tuple(x+1.5*math.sin(i*.4) for i,x in enumerate(demand))
    revised_res = tuple(max(0,x+math.sin(i*.5)) for i,x in enumerate(renewable))
    revised_d,revised_r=da_d,da_r  # One forecast vintage in the current public profile.
    forwards = [Forward("bilateral_1","generator_A","supplier_T","thermal_A",tuple(15. for _ in periods),72,buyer_entity="load_T"),
                Forward("MM_inherent_T","generator_A","supplier_T","thermal_A",tuple(6. for _ in periods),65,buyer_entity="load_T"),
                Forward("MM_inherent_D","generator_A","supplier_D","thermal_A",tuple(4. for _ in periods),65,buyer_entity="load_D")]
    orders=[]
    for i,t in enumerate(periods):
        for u in units:
            forward = 25 if u.id=="thermal_A" else 0
            orders.append(Order(f"sell:{u.id}:{i}",u.participant,u.id,i,"sell",u.maximum_mw*.5-forward,u.energy_eur_mwh))
        orders.append(Order(f"sell:res:{i}","RES_portfolio","res",i,"sell",renewable[i]*.5,0))
        for e,share,forward in (("load_T",.6,21),("load_D",.4,4)):
            orders.append(Order(f"buy:{e}:{i}",regs[e].participant,e,i,"buy",demand[i]*share*.5-forward,1000))
        orders.append(Order(f"buy:battery:{i}","storage_BSP","battery_load",i,"buy",charge[i],1000,
            block="storage_cycle" if charge[i] else None,minimum_acceptance=1 if charge[i] else 0,circular=bool(charge[i])))
        orders.append(Order(f"sell:battery:{i}","storage_BSP","battery_injection",i,"sell",discharge[i],0,
            block="storage_cycle" if discharge[i] else None,minimum_acceptance=1 if discharge[i] else 0,circular=bool(discharge[i])))
    req = {p:[v]*n for p,v in zip(PRODUCTS,(2,2,3,3,4,2,2))}
    history={i:[[80+day*10+rtu for rtu in range(6)] for day in range(3)] for i in range(n)}
    services = [
        dict(id="blackstart",component="S09",participant="generator_C",account="A-C",quantity=24,rate=8,availability=1,rule="Annex5.2/13.5.2.3"),
        dict(id="contingency_capacity",component="S11",participant="contingency_provider",account="A-C",quantity=10*24,rate=1.5,availability=1,rule="Annex5.3"),
        dict(id="mandatory_service",component="S24",participant="generator_A",account="MM",quantity=n,rate=6,availability=1,rule="MM4",source_entity="thermal_A"),
        *[dict(id=f"storage_{service}_{direction}",component="S27",participant="storage_BSP",account="MS",
            quantity=(1 if service=="FCR" else .5)*n,rate=None,availability=.95 if service=="FCR" else 1,
            rule="MS5-6/ISP_directional_FCR",reference_product=f"FCR_{direction}",coefficient=1 if service=="FCR" else 1.2)
          for service in ("FCR","FFR") for direction in ("up","down")]]
    for service in services:
        service.update(period=day.isoformat(),source="synthetic_procurement_award_v1")
    services=[s for s in services if s['component']!='S27']
    return dict(day=day,periods=periods,rtus=rtus,units=units,registrations=regs,
                brp_participants={r.brp:r.participant for r in regs.values()},
                forecasts=(da_d,da_r,revised_d,revised_r),initial_cutoff=initial_cutoff,
                rerun_cutoff=rerun_cutoff,forwards=forwards,orders=orders,requirements=req,
                history=history,services=services,seed=3026,
                RR_procurement=dict(awards_mw={u.id:[2 if u.id=="thermal_B" else 0]*n for u in units},
                                    prices_eur_mw_period=[3]*n,source="synthetic_monthly_RR_award_and_notifications",
                                    quote_unit="EUR/MW/trading_period_normalised"),
                mandatory_on={u.id:[int(u.id=="thermal_A")]*n for u in units},
                non_delivery_events={},dispatch_equals_delivery=True,
                tariffs=synthetic_tariffs(),
                accounting_parameters=dict(reserve_cost_eur=900,reconciliation_cost_eur=20,
                    frozen_MM_shares={"supplier_T":.6,"supplier_D":.4},
                    penalty_distribution={"UA-1":.4,"UA-6":.3,"UA-7":.3},
                    systematic_penalty_rates=dict(absolute_rate=100,rms_rate=100,absolute_tolerance=.02,rms_tolerance=.02),
                    dispatch_penalty_rates=dict(rate=100,escalation=.1,provenance="synthetic_annual_2026"),
                    opening_dispatch_counters={u.id:2 for u in units},
                    activation_penalty_rates=dict(rate=100,escalation=.1,tolerance=.02,provenance="synthetic_annual_2026"),
                    opening_activation_counters={u.id:0 for u in units},
                    validity_from="2026-09-01",validity_to="2026-12-01",provenance="synthetic_frozen_rules_counterfactual_history"),
                financial_inputs={p:dict(roles=[dict(role="load_representative" if p.startswith("supplier") else "generator",
                    capacity_factors_m7_to_m12=[.5]*6,capacity_mw_or_authorised_mva=
                    next((u.maximum_mw for u in units if u.participant==p),
                         {"RES_portfolio":115,"supplier_T":200,"supplier_D":150,"storage_BSP":10,"contingency_provider":10}[p] if p in ("RES_portfolio","supplier_T","supplier_D","storage_BSP","contingency_provider") else 0),imbalance_factor=.1)],
                    wholesale_rate=100,unit_charges=[12,18,2,1],res_fee=3,
                    six_month_obligations=[100,200,300,250,100,180],latest_MM_debit=200 if p.startswith("supplier") else 0,
                    collateral_eur=100000000,provenance="synthetic_dated_risk_history_2026_11")
                    for p in {r.participant for r in regs.values()}|{"contingency_provider"}},
                storage=dict(id="battery",capacity_mwh=40,power_mw=10,initial_soc_mwh=20,
                             efficiency_charge=.95,efficiency_discharge=.95,
                             day_ahead_schedule_mwh=discharge,charge_schedule_mwh=charge,
                             embedded_RES=False,grid_charging_allowed=True,ancillary_reserved_mw=0),
                ruleset=dict(id="CY_2026_11_05_qualified_synthetic",mode="qualified_synthetic",
                    settlement=dict(res_tolerance=dict(commercial=0.,grant_supported=0.)),
                    official_parameters=False,claim="synthetic_rules_based_replica",
                    official_equivalence_verified=False,conventions=[f"D{i:02}" for i in range(1,14)],
                    storage=dict(participation_balancing_energy=False,participation_balancing_capacity=False),
                    res=dict(participation_balancing_energy=True,participation_balancing_capacity=False),slack=deepcopy(SLACK_DEFAULTS)))


def synthetic_case(day=date(2026,11,5),case="ordinary"):
    from dataclasses import replace
    s=synthetic_day(day);s["case"]=case
    if case=="ordinary":return s
    if case=="trip":
        periods=s["periods"]
        # Deliberately slower standby ramp makes contracted emergency energy
        # necessary during the first RTU after the trip, rather than merely
        # declaring an unused emergency contract in this acceptance case.
        s["units"]=[replace(u,ramp_mw_hour=600) if u.id=="thermal_C" else u for u in s["units"]]
        s["non_delivery_events"]={}
        s["operational_events"]=[dict(entity="thermal_B",kind="trip",effective_at=periods[18].start,
            available_at=periods[18].start,restore_period=20,RR_replacement="thermal_C"),
            dict(entity="thermal_B",kind="restore",effective_at=periods[20].start,available_at=periods[20].start)]
        s["ISP_emergency_resources"]=[dict(id="contracted_standby",availability_mw=[40 if i==18 else 0 for i in range(len(periods))],contract_rate=300)]
        s["emergency_resources_by_rtu"]={i:[dict(id="contracted_standby",participant="contingency_provider",
            available_mw=40,contract_rate=300)] for i in range(108,114)}
        s["emergency_price_eur_mwh"]=1000
        for service in s["services"]:
            if service["id"]=="contingency_capacity":service["quantity"]=40*24
        s["financial_inputs"]["contingency_provider"]["roles"][0]["capacity_mw_or_authorised_mva"]=40
        return s
    if case=="high_res":
        s["external_RES_control"]=True  # Explicit PA priority-control acceptance case, separate from RES market offers.
        factor=2.5
        forecasts=list(s["forecasts"])
        for index in (1,3):forecasts[index]=replace(forecasts[index],values_mw=tuple(v*factor for v in forecasts[index].values_mw))
        s["forecasts"]=tuple(forecasts)
        s["orders"]=[replace(o,energy_mwh=o.energy_mwh*factor) if o.asset=="res" else o for o in s["orders"]]
        s["financial_inputs"]["RES_portfolio"]["roles"][0]["capacity_mw_or_authorised_mva"]*=factor
        return s
    raise ValueError("Unknown synthetic case")
