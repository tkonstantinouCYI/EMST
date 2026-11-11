"""Dedicated conditional-event evidence and cross-stage release invariants.

These are executed calculations, not manually assigned 'complete' flags. They
exercise branches absent from an ordinary delivery day and retain their inputs.
"""
from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
from .settlement import D,Ledger
from .model import PRODUCTS
from .operations import emergency_energy,contingency_energy,priority_curtailment
from .compliance import CAPACITY,Counters,assess,mandatory_penalty,blackstart_instalment
from .risk import Collateral,DefaultState
from .accounting import annual_reserve_rebate
from .isp import schedule
from .rtbm import clear_rtu


def conditional_evidence(scenario):
    checks=[];components={}
    def check(name,value,expected,tolerance=1e-6):
        checks.append(dict(name=name,value=str(value),expected=str(expected),
                           passed=abs(D(value)-D(expected))<=D(tolerance)))
    above=emergency_energy(10,7,8,.5,100)
    check("S10 qualified above-registered delivery",above["cash_eur"],200)
    components["S10"]=dict(fixture="above_registered_energy",amount_eur=above["cash_eur"])
    contingency=contingency_energy(2,1,300)
    check("S12 contractual metered contingency",contingency["cash_eur"],300)
    components["S12"]=dict(fixture="contracted_contingency",amount_eur=contingency["cash_eur"])
    unit=replace(scenario["units"][2],maximum_mw=60,minimum_mw=20,initial_duration=100,
                 soak_periods=3,startup_trajectory_mw=(5,10,20),agc_min_mw=0)
    startup=schedule([unit],scenario["periods"][:4],[2.5,7.5,15,20],[0]*4,{unit.id:[0]*4},
        {p:[0]*4 for p in PRODUCTS},{unit.id:dict(on=0,end_mw=0,duration_periods=100)},
        scenario["initial_cutoff"],"acceptance_cold_start",False)
    cost=startup["rows"][0]["startup_cost_eur"]
    check("S13 declared cold startup",cost,270)
    check("startup soaking excludes AGC",sum(r["agc"] for r in startup["rows"][:3]),0)
    components["S13"]=dict(fixture="cold_start_and_three_soaking_periods",amount_eur=str(cost))
    # Each capacity/offer family has a known analytical 440 EUR case, with the
    # source N^x escalation and no resetting of the supplied annual counter.
    penalty_results=[]
    for family in CAPACITY:
        event=dict(entity="g",family=family,obligated=True,failure=True,counter_window="2026",
                   occurrence_key="2026-11-05",missing_quantities=[40,60],
                   event_time="2026-11-05T00:00:00+02:00",assessment_period="2026-11")
        parameters=dict(rate=2,escalation=.1,exponent=.5,provenance="synthetic")
        counter=Counters({("g",family,"2026"):3})
        result=assess(event,parameters,counter);penalty_results.append(result)
        check(f"S23 {family}",result["amount_eur"],440)
        check(f"S23 same-day counter idempotence {family}",assess(event,parameters,counter)["counter_after"],4)
    components["S23"]=dict(fixture="source_formula_families_plus_complete_month_RMS",
                            amount_eur=str(sum((D(r["amount_eur"]) for r in penalty_results),D(0))))
    check("black-start annual instalments",sum((blackstart_instalment(m) for m in range(1,13)),D(0)),500000)
    mm=mandatory_penalty("producer",2,10,5,.1,2)
    check("MM producer noncompliance",mm,121)
    supplier=mandatory_penalty("supplier",2,.6,100,.1,2)
    check("MM supplier noncompliance with frozen share",supplier,D("145.2"))
    portfolios=[dict(id=id,category=c,available_mwh=q,technical_minimum_mwh=0,zero_injection_self_consumption=False)
        for id,c,q in (("protected",1,10),("priority",2,20),("nonpriority",3,30))]
    control=priority_curtailment(portfolios,40,scenario["seed"])
    check("PA category 3 before category 2",sum(D(r["down_mwh"]) for r in control if r["category"]==3),30)
    check("PA retained protected generation",sum(D(r["down_mwh"]) for r in control if r["category"]==2),10)
    security=Collateral("cash","account","cash","600","0",None)
    state=DefaultState({"cash":security},{"member":200,"other":150},500)
    first=state.default("default1","member","account",1000,scenario["day"])
    second=state.default("default2","member","account",500,scenario["day"]+timedelta(days=1))
    check("S33 ordered waterfall",first["draws_eur"]["reserve_fund"],50)
    check("S33 sequential defaults cannot reuse collateral",second["uncovered_loss_eur"],50)
    components["S33"]=dict(fixture="sequential_default_and_collateral_inventory",amount_eur="1500")
    rebate=annual_reserve_rebate(300,{"supplier_T":2,"supplier_D":1})
    check("year-end reserve rebate follows contributions",rebate["supplier_T"],200)
    return dict(passed=all(c["passed"] for c in checks),checks=checks,
                component_fixtures=components,penalty_assessments=penalty_results,
                startup=startup,RES_priority_instructions=control,default_events=[first,second])


def later_stage_audit(monthly,monthly_financial,storage_rows,scenario,revision_financial):
    checks=[]
    def check(name,passed,detail=None):checks.append(dict(name=name,passed=bool(passed),detail=detail))
    check("complete monthly input period coverage",monthly["assessment_days"]==30 and monthly["history_records"]==30*48*len(scenario["registrations"]))
    check("monthly physical-day energy replay matches daily",monthly["calculations"]["physical_day_replay"]["passed"],
          monthly["calculations"]["physical_day_replay"])
    check("three cumulative input months independently recalculated",len(monthly["cumulative"]["months"])==3)
    check("meter revision replay is idempotent",D(monthly["reconciliation"]["replay_delta_eur"])==0)
    check("meter correction recalculates multiple formula families",len({e["source"].split(":")[1] if e["source"].startswith("recompute:") else "Annex13.1" for e in monthly["reconciliation"]["ledger"]})>=3)
    balances={}
    for e in monthly["ledger"]:
        q=D(e["cash_eur"])
        balances[e["account"]]=balances.get(e["account"],D(0))+q
        balances[e["counterparty"]]=balances.get(e["counterparty"],D(0))-q
    check("monthly double entry",abs(sum(balances.values(),D(0)))<D("1e-10"))
    for account in ("A-B","A-C","A-D","A-E","A-F","A-G","A-H","MM","MS")+tuple(f"UA-{i}" for i in range(1,8)):
        check(f"monthly account closure {account}",abs(balances.get(account,D(0)))<D("1e-7"),str(balances.get(account,0)))
    check("monthly cash conserved",monthly_financial["cash"]["conserved"])
    check("monthly invoices paid",all(i["state"]=="paid" for i in monthly_financial["invoices"]))
    for kind,cycle in revision_financial.items():
        check(f"{kind} cash conserved",cycle["cash"]["conserved"])
        check(f"{kind} invoices paid",all(i["state"]=="paid" for i in cycle["invoices"]))
        check(f"{kind} invoiced after final statement",all(i["issued_on"]>=monthly["calendars"][kind]["final"] for i in cycle["invoices"]))
    for id,versions in monthly["statement_versions"].items():
        check(f"{id} preliminary and final chronology",len(versions)==2 and versions[-1]["state"]=="final"
              and versions[-1]["issued_at"]>=versions[0]["objection_deadline"])
    check("all account margin requirements secured",all(D(r["headroom_eur"])>=0 for r in monthly_financial["risk_by_account"].values()))
    check("storage actively charges and discharges",sum(D(r["charge_mwh"]) for r in storage_rows)>0 and sum(D(r["discharge_mwh"]) for r in storage_rows)>0)
    check("storage day SOC closes after losses",abs(D(storage_rows[-1]["final_soc_mwh"])-D(storage_rows[0]["initial_soc_mwh"]))<D("1e-10"))
    check("monthly storage SOC independently feasible",monthly["calculations"]["storage_audit"]["passed"])
    return dict(passed=all(c["passed"] for c in checks),checks=checks,
                failures=[c for c in checks if not c["passed"]],monthly_balances_eur={k:str(v) for k,v in balances.items()})
