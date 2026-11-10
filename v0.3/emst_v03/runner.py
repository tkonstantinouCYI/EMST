"""Run a physical delivery day and separate synthetic accounting-cycle fixtures."""
import argparse
from dataclasses import asdict
from datetime import date,datetime,time,timedelta
from decimal import Decimal
import hashlib,json,math
import platform
import pulp
from pathlib import Path
from .calendar import CYPRUS,UTC
from .model import PRODUCTS
from .system import observation_update,reserve_series,system_series
from .scenario import synthetic_day,synthetic_case
from .commercial import forward_schedules,clear_dam
from .isp import schedule
from .rtbm import clear_rtu
from .settlement import (Ledger,D,CATALOGUE,settle_energy,reserve_cash,recover,
                         assessed_service,statement)
from .clearing import required_margin,invoice,pay,default_waterfall,late_payment_charge,ClearingCycle
from .audit import audit_day
from .metering import Meter,allocate_profile,storage_delivery,validate_rtbm_eligibility
from .history import full_cycles,synthetic_tariffs
from .accounting import post_tariffs,period_recovery,allocate,business_offset
from .notifications import Notification,NotificationBook
from .lifecycle import CashBook,StatementBook
from .risk import minimum_margin,Collateral,DefaultState
from .operations import trip,rerun_horizon,emergency_energy,project_handover
from .acceptance import conditional_evidence,later_stage_audit
from .compliance import daily_activation_penalties


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,default=str,allow_nan=False),encoding="utf-8")


def run_day(day=date(2026,11,5),output=Path("output/test_day"),price_ranges=False,scenario=None,accounting_cycles=False):
    s=synthetic_day(day) if scenario is None else scenario
    day=s["day"]
    if s["ruleset"]["mode"]!="qualified_synthetic":
        raise ValueError("Verified-rules-only mode cannot use the active unverified conventions")
    periods,intervals=s["periods"],s["rtus"]
    units={u.id:u for u in s["units"]}
    regs=s["registrations"]
    permissions=s['ruleset'].get('storage',{})
    if permissions.get('participation_balancing_energy',False):
        raise ValueError('Use the participant-independent Market API for storage balancing-energy participation')
    if not permissions.get('participation_balancing_capacity',False) and any(x['component']=='S27' for x in s['services']):
        raise ValueError('Storage capacity payments forbidden by ruleset')
    if abs(sum(r.consumption_share for r in regs.values() if r.role=="offtake")-1)>1e-9:
        raise ValueError("Explicit supplier consumption shares must sum to one")
    for unit in s["units"]:
        validate_rtbm_eligibility("thermal")
    events=[]
    def event(kind,at,**details):
        events.append(dict(kind=kind,at=at.isoformat(),**details))
    print("Validating forwards and clearing DAM",flush=True)
    schedules,private_known=forward_schedules(s["forwards"],regs,units,len(periods))
    gate=datetime.combine(day-timedelta(days=1),time(9),CYPRUS)
    notifications=NotificationBook(s["forwards"],gate,gate+timedelta(hours=1))
    for c in s["forwards"]:
        notifications.submit(Notification(c.id,"delivery",c.seller,c.energy_mwh,1,gate))
        notifications.submit(Notification(c.id,"offtake",c.buyer,c.energy_mwh,1,gate))
    accepted_notifications,mismatches=notifications.finalise()
    if mismatches:raise ValueError("Uncorrected physical notification mismatch")
    event("forward_notifications_accepted",datetime.combine(day-timedelta(days=1),time(10),CYPRUS),versions=[c.version for c in s["forwards"]])
    for order in s["orders"]:
        if order.asset not in regs or regs[order.asset].participant!=order.participant:
            raise ValueError("DAM order ownership/registration mismatch")
    margins={(u.id,t.period):u.maximum_mw*t.hours-schedules[u.id][t.period] for u in s["units"] for t in periods}
    for t in periods:
        margins["res",t.period]=s["forecasts"][1].values_mw[t.period]*t.hours
        margins["battery_injection",t.period]=(s["storage"]["power_mw"]-s["storage"]["ancillary_reserved_mw"])*t.hours
    # Reject an infeasible offered cycle before clearing. The circular block
    # preserves its profile; realised energy must still come from accepted orders.
    storage_offer_feasibility=storage_delivery(s["storage"],s["storage"]["charge_schedule_mwh"],
        s["storage"]["day_ahead_schedule_mwh"],[p.hours for p in periods],
        [s["storage"]["ancillary_reserved_mw"]]*len(periods))
    dam=clear_dam(s["orders"],len(periods),capacity_margins=margins)
    statements=StatementBook()
    statement_id=f"DAM:{day}"
    dam_lines={o.id:dict(participant=o.participant,quantity_mwh=dam["accepted_mwh"][o.id],
        price_eur_mwh=dam["prices_eur_mwh"][o.period],
        cash_eur=dam["accepted_mwh"][o.id]*dam["prices_eur_mwh"][o.period]*(1 if o.side=="sell" else -1)) for o in s["orders"]}
    statements.preliminary(statement_id,dam_lines,
        datetime.combine(day-timedelta(days=1),time(15),CYPRUS),
        datetime.combine(day-timedelta(days=1),time(16),CYPRUS))
    statements.finalise(statement_id,dam_lines,
        datetime.combine(day-timedelta(days=1),time(17),CYPRUS),[])
    event("DAM_published",datetime.combine(day-timedelta(days=1),time(14),CYPRUS),status=dam["status"])
    ledger=Ledger()
    for c in s["forwards"]:
        if c.price_eur_mwh is None:
            continue
        component="S25" if c.id.startswith("MM_") else "S01"
        for t,q in enumerate(c.energy_mwh):
            ledger.post(f"forward:{c.id}:{t}",component,f"participant:{c.seller}:private",
                f"participant:{c.buyer}:private",q,c.price_eur_mwh,periods[t].id,
                "MM3/inherent_input" if component=="S25" else "chapter6/private_extension",c.id)
    for order in s["orders"]:
        q=dam["accepted_mwh"][order.id]
        schedules[order.asset][order.period]+=q
        ledger.post(f"DAM:{order.id}","S02",f"participant:{order.participant}:DAM","A-A",
                    q*(1 if order.side=="sell" else -1),dam["prices_eur_mwh"][order.period],
                    periods[order.period].id,"13.3",order.id)
    da_d,da_r,revised_d,revised_r=s["forecasts"]
    s['requirements']=reserve_series(s['requirements'],[p.start for p in periods])
    tso_mtu,tso_rtu,tso_source=system_series(s.get('system_forecasts'),[p.start for p in periods],
        [p.start for p in intervals],name='system_forecasts',
        fallback={'demand':da_d.values_mw,'res':da_r.values_mw})
    if s.get('system_forecasts') is not None:
        from dataclasses import replace
        da_d=replace(da_d,values_mw=tuple(tso_mtu['demand']))
        da_r=replace(da_r,values_mw=tuple(tso_mtu['res']))
        revised_d=replace(revised_d,values_mw=tuple(tso_mtu['demand']))
        revised_r=replace(revised_r,values_mw=tuple(tso_mtu['res']))
        s['forecasts']=(da_d,da_r,revised_d,revised_r)
    storage_charge=schedules["battery_load"]
    storage_discharge=schedules["battery_injection"]
    storage_rows=storage_delivery(s["storage"],storage_charge,storage_discharge,
        [p.hours for p in periods],[s["storage"]["ancillary_reserved_mw"]]*len(periods))
    demand=tuple(v+storage_charge[i]/periods[i].hours for i,v in enumerate(da_d.at_cutoff(s["initial_cutoff"])))
    res=tuple(v+storage_discharge[i]/periods[i].hours for i,v in enumerate(da_r.at_cutoff(s["initial_cutoff"])))
    def isp_res_offers(values,start=0):
        if s.get('external_RES_control',False):return None
        return [dict(id='res',participant=regs['res'].participant,down_price=float(s.get('res_down_price_eur_mwh',0)),
            forecast_mw=[max(0,values[t]-storage_discharge[t]/periods[t].hours) for t in range(start,len(periods))],
            commercial_mwh=schedules['res'][start:])]
    initial={u.id:dict(on=u.initial_on,end_mw=u.initial_mw,duration_periods=u.initial_duration) for u in s["units"]}
    print("Solving initial ISP MILP and fixed-discrete LP shadow prices",flush=True)
    first=schedule(s["units"],periods,demand,res,schedules,s["requirements"],initial,
                   s["initial_cutoff"],"ISP_initial",price_ranges,
                   rr_awards=s["RR_procurement"]["awards_mw"],rr_prices=s["RR_procurement"]["prices_eur_mw_period"],mandatory_on=s["mandatory_on"],slack_parameters=s["ruleset"].get("slack"),res_energy_offers=isp_res_offers(res))
    runs=[first]
    effective={(r["entity"],r["period"]):r for r in first["rows"]}
    event("ISP_initial",s["initial_cutoff"],publication=(s["initial_cutoff"]+timedelta(hours=1)).isoformat())
    state={u.id:dict(on=u.initial_on,end_mw=u.initial_mw,duration_rtus=u.initial_duration*6) for u in s["units"]}
    operational=[]
    previous_observation=s.get("previous_system_observation")
    availability_profiles={u.id:[u.maximum_mw]*len(periods) for u in s["units"]}
    rr_profiles={e:list(v) for e,v in s["RR_procurement"]["awards_mw"].items()}
    active_demand,active_res,active_version=demand,res,da_d.version
    active_run="ISP_initial"
    results=[]
    pending_rerun=None
    for i,rtu in enumerate(intervals):
        triggering=[e for e in s.get("operational_events",[]) if e["effective_at"]==rtu.start]
        for notice in triggering:
            entity=notice["entity"]
            if notice["available_at"]>rtu.start:raise ValueError("Future outage notice leaked into dispatch")
            if notice["kind"]=="trip":
                record=trip(state,entity,rtu.start);operational.append(record)
                for p in range(rtu.period,notice["restore_period"]):availability_profiles[entity][p]=0
                for p in range(rtu.period,notice["restore_period"]):
                    lost=rr_profiles[entity][p];rr_profiles[entity][p]=0
                    if lost and notice.get("RR_replacement"):
                        rr_profiles[notice["RR_replacement"]][p]+=lost
            elif notice["kind"]=="restore":
                for p in range(rtu.period,len(periods)):availability_profiles[entity][p]=units[entity].maximum_mw
                operational.append(dict(entity=entity,cause="availability_restored",effective_at=rtu.start.isoformat()))
            else:raise ValueError("Unknown operational event")
            # Exceptional runs retain the same legal trading periods. An event
            # between boundaries queues a future-effective run; immediate RTBM
            # records the forced outage separately below.
            future=rerun_horizon(periods,rtu.start)
            record_at=future[0].start
            event("exceptional_ISP_requested",rtu.start,cause=notice["kind"],effective_at=record_at.isoformat())
            notice["rerun_at"]=record_at
        due=[e for e in s.get("operational_events",[]) if e.get("rerun_at")==rtu.start]
        if due:
            start=rtu.period
            initial={e:dict(on=v["on"],end_mw=v["end_mw"],duration_periods=v["duration_rtus"]//6,
                **({"stage":v["stage"],"phase_elapsed_minutes":v["phase_elapsed_minutes"]} if "stage" in v else {})) for e,v in state.items()}
            run_id=f"ISP_exceptional_{i}"
            extra=schedule(s["units"],periods[start:],active_demand[start:],active_res[start:],
                {e:v[start:] for e,v in schedules.items()},
                {p:v[start:] for p,v in s["requirements"].items()},initial,rtu.start,run_id,price_ranges,
                availability_mw={e:v[start:] for e,v in availability_profiles.items()},
                emergency_resources=[{**r,"availability_mw":r["availability_mw"][start:]} for r in s.get("ISP_emergency_resources",[])],
                rr_awards={e:v[start:] for e,v in rr_profiles.items()},rr_prices=s["RR_procurement"]["prices_eur_mw_period"][start:],
                mandatory_on={e:v[start:] for e,v in s["mandatory_on"].items()},
                slack_parameters=s["ruleset"].get("slack"),res_energy_offers=isp_res_offers(active_res,start))
            runs.append(extra);active_run=run_id
            effective.update({(r["entity"],r["period"]):r for r in extra["rows"]})
            event("ISP_exceptional_rerun",rtu.start,first_effective_period=start,cause=[e["kind"] for e in due])
        if rtu.local.hour==11 and rtu.local.minute==0:
            print("Solving 11:00 ISP rerun for noon onward using a forecast noon state",flush=True)
            effective_at=datetime.combine(day,time(12),CYPRUS)
            start=next(p.period for p in periods if p.start==effective_at)
            d2=tuple(v+storage_charge[i]/periods[i].hours for i,v in enumerate(revised_d.at_cutoff(s["rerun_cutoff"])))
            r2=tuple(v+storage_discharge[i]/periods[i].hours for i,v in enumerate(revised_r.at_cutoff(s["rerun_cutoff"])))
            projection=project_handover(s["units"],[t for t in intervals if rtu.start<=t.start<effective_at],
                state,effective,schedules,d2,r2,storage_discharge,s["history"],rtu.start,effective_at,s["seed"],previous_observation=previous_observation,
                system_forecasts_rtu=tso_rtu if s.get("system_forecasts") is not None else None,storage_charge=storage_charge,res_offer_price=None if s.get("external_RES_control",False) else float(s.get("res_down_price_eur_mwh",0)),
                res_participant=regs["res"].participant,slack_parameters=s["ruleset"].get("slack"),requirements=s["requirements"])
            initial={e:dict(on=v["on"],end_mw=v["end_mw"],duration_periods=v["duration_rtus"]//6,
                **({"stage":v["stage"],"phase_elapsed_minutes":v["phase_elapsed_minutes"]} if "stage" in v else {}))
                     for e,v in projection["projected_state"].items()}
            rerun=schedule(s["units"],periods[start:],d2[start:],r2[start:],
                {e:v[start:] for e,v in schedules.items()},
                {p:v[start:] for p,v in s["requirements"].items()},initial,s["rerun_cutoff"],"ISP_1100",price_ranges,
                availability_mw={e:v[start:] for e,v in availability_profiles.items()},
                emergency_resources=[{**r,"availability_mw":r["availability_mw"][start:]} for r in s.get("ISP_emergency_resources",[])],
                rr_awards={e:v[start:] for e,v in rr_profiles.items()},rr_prices=s["RR_procurement"]["prices_eur_mw_period"][start:],
                mandatory_on={e:v[start:] for e,v in s["mandatory_on"].items()},
                slack_parameters=s["ruleset"].get("slack"),res_energy_offers=isp_res_offers(r2,start))
            runs.append(rerun)
            rerun.update(effective_at=effective_at.isoformat(),handover_projection=projection)
            pending_rerun=rerun
            active_demand,active_res,active_version=d2,r2,revised_d.version
            event("ISP_scheduled_rerun",s["rerun_cutoff"],first_effective_period=start,
                  supersedes="ISP_initial_future_only",publication=(s["rerun_cutoff"]+timedelta(hours=1)).isoformat())
        if pending_rerun and rtu.start==datetime.fromisoformat(pending_rerun["effective_at"]):
            active_run=pending_rerun["id"]
            effective.update({(r["entity"],r["period"]):r for r in pending_rerun["rows"]})
            event("ISP_scheduled_results_effective",rtu.start,first_effective_period=rtu.period)
            pending_rerun=None
        base_d=tso_rtu['demand'][i]+storage_charge[rtu.period]/rtu.hours/6
        base_r=tso_rtu['res'][i]+storage_discharge[rtu.period]/rtu.hours/6
        if s.get('system_forecasts') is None:
            base_d=active_demand[rtu.period];base_r=active_res[rtu.period]
        prediction=observation_update(base_d,base_r,previous=previous_observation)
        predicted_d=prediction['demand_prediction_mw']
        predicted_r=prediction['res_prediction_mw']
        # Synthetic telemetry is generated independently of the corrected forecast.
        actual_d=base_d+.7*math.sin(i*.4)+.15*math.cos(i*.7)
        actual_r=max(0,base_r+.4*math.sin(i*.3)+.1*math.sin(i*.9))
        failures=s["non_delivery_events"].get(i,{})
        current_rows={u.id:effective[u.id,rtu.period] for u in s["units"]}
        for entity,profile in availability_profiles.items():
            if profile[rtu.period]==0 and current_rows[entity]["on"]:
                current_rows[entity]={**current_rows[entity],"on":0,"agc":0,"available_mw":0,
                                      "reserve_mw":{p:0 for p in s["requirements"]}}
        emergency=s.get("emergency_resources_by_rtu",{}).get(i,[])
        try:
            result=clear_rtu(rtu,s["units"],state,
                current_rows,
                {u.id:schedules[u.id][rtu.period]/6 for u in s["units"]},
                predicted_d,predicted_r,actual_d,actual_r,failures,
                emergency_resources=emergency,emergency_price=s.get("emergency_price_eur_mwh"),
                delivery_equals_dispatch=s.get('dispatch_equals_delivery',True),
                slack_parameters=s['ruleset'].get('slack'),
                res_energy_offers=None if s.get('external_RES_control',False) else [dict(id='res',participant=regs['res'].participant,
                    available_mwh=max(0,predicted_r*rtu.hours-storage_discharge[rtu.period]/6),commercial_mwh=schedules['res'][rtu.period]/6,
                    down_price=float(s.get('res_down_price_eur_mwh',0)))],
                high_res_reference_price=sum(d[i%6] for d in s["history"][rtu.period])/3,
                res_portfolios=[dict(id="res",category=s.get("RES_priority_category",3),
                    available_mwh=max(0,predicted_r*rtu.hours-storage_discharge[rtu.period]/6),
                    technical_minimum_mwh=0,zero_injection_self_consumption=False)])
        except RuntimeError:
            write_json(Path(output)/"FAILED_RTU.json",dict(interval=asdict(rtu),state=state,
                isp_rows={u.id:effective[u.id,rtu.period] for u in s["units"]},
                demand_prediction_mw=predicted_d,res_prediction_mw=predicted_r,
                prior_rtu=results[-1] if results else None))
            raise
        result["isp_run"]=active_run
        result["forced_trip_entities"]=[e["entity"] for e in triggering if e["kind"]=="trip"]
        result["forecast_version"]=active_version
        result['system_prediction']=dict(prediction,measurement_index=i-1 if i else None)
        result['system_observation']=dict(demand_mw=actual_d,res_mw=actual_r,
            forecast_demand_mw=base_d,forecast_res_mw=base_r)
        result['system_observation_available_at']=rtu.end.isoformat()
        previous_observation=dict(result['system_observation'])
        battery_charge=storage_charge[rtu.period]/6
        battery_discharge=storage_discharge[rtu.period]/6
        native_consumption=result["demand_actual_mwh"]-battery_charge
        result["entity_meters"]={"load_T":native_consumption*.6,"load_D":native_consumption*.4,
            "battery_load":battery_charge,"battery_injection":battery_discharge,
            "res":result["res_actual_mwh"]-battery_discharge}
        result["storage_charge_mwh"]=battery_charge
        result["storage_discharge_mwh"]=battery_discharge
        results.append(result)
        event("RTBM",rtu.start,rtu=rtu.id,isp_run=result["isp_run"])
    print("Settling trades, balancing, services, recovery and metering",flush=True)
    bridges,prices=settle_energy(ledger,periods,results,regs,schedules,s["history"],s["brp_participants"],s["ruleset"].get("settlement"))
    brp_settlement={}
    for b in bridges:
        key=f"{b['brp']}:{b['period']}"
        group=brp_settlement.setdefault(key,dict(brp=b["brp"],participant=b["brp_participant"],period=b["period"],cash_eur=D(0),entities=[]))
        group["cash_eur"]+=D(b["uninstructed_cash_eur"]);group["entities"].append(b["entity"])
    meters=[]
    acquired=datetime.combine(day+timedelta(days=1),time(12),CYPRUS)
    for b in bridges:
        reg=regs[b["entity"]]
        meter=Meter(reg.meter,b["entity"],periods[b["period"]].id,str(b["MQ"]),"1",
                    "injection" if reg.role=="production" else "offtake","certified",1,acquired)
        meters.append({**asdict(meter),"adjusted_mwh":str(meter.adjusted())})
    rows=list(effective.values())
    availability={(r["entity"],r["period"],p):min(
        sum(row["on"] for rt in results if rt["period"]==r["period"] for row in rt["rows"] if row["entity"]==r["entity"])/6,
        .9 if r["entity"]=="thermal_B" and r["period"]==20 else 1.) for r in rows for p in PRODUCTS}
    reserve_cash(ledger,rows,availability,periods,units,{r["period"] for r in results if r["regime"]=="emergency"})
    for rtu in results:
        for instruction in rtu["emergency_rows"]:
            if instruction.get("kind")=="above_registered":
                host=next(r for r in rtu["rows"] if r["entity"]==instruction["host_entity"])
                payment=emergency_energy(units[host["entity"]].maximum_mw,
                    host["actual_mwh"]+instruction["delivered_mwh"],
                    host["instructed_energy_mwh"]+instruction["delivered_mwh"],rtu["duration_hours"],instruction["contract_rate"])
                assessed_service(ledger,dict(id=f"above_registered:{rtu['rtu']}:{instruction['id']}",component="S10",
                    participant=instruction["participant"],account="A-C",quantity=payment["eligible_mwh"],rate=instruction["contract_rate"],
                    availability=1,period=periods[rtu["period"]].id,rule="13.5.2.4",source=instruction["id"]))
                continue
            assessed_service(ledger,dict(id=f"contingency_energy:{rtu['rtu']}:{instruction['id']}",component="S12",
                participant=instruction["participant"],account="A-B",quantity=instruction["delivered_mwh"],
                rate=instruction["contract_rate"],availability=1,period=periods[rtu["period"]].id,
                rule="13.4.6.11/contract",source=instruction["id"]))
    for row in rows:
        if row["start"]:
            u=units[row["entity"]]
            assessed_service(ledger,dict(id=f"startup:{u.id}:{row['period']}",component="S13",
                participant=u.participant,account="A-C",quantity=1,rate=row["startup_cost_eur"],availability=1,
                period=periods[row["period"]].id,rule="13.5.2.6/synthetic_eligible_start",source="effective_ISP_start"))
    for service in s["services"]:
        for t in periods:
            item=dict(service)
            item["id"]=f"{service['id']}:{t.period}"
            item["period"]=t.id
            item["quantity"]=D(service["quantity"])/len(periods)
            if item["component"]=="S24" and "source_entity" in service:
                item["availability"]=int(all(r["on"] for rt in results if rt["period"]==t.period for r in rt["rows"] if r["entity"]==service["source_entity"]))
            if item["rate"] is None:
                row=next(r for r in rows if r["period"]==t.period)
                item["rate"]=row["reserve_price_eur_mw_period"][service["reference_product"]]*service["coefficient"]
            assessed_service(ledger,item)
    total_consumption=sum(r["demand_actual_mwh"] for r in results)
    base={}
    tariff_meters=[]
    for b in bridges:
        reg=regs[b["entity"]]
        if reg.role!="offtake":continue
        base[reg.participant]=base.get(reg.participant,D(0))+D(b["MQ"])
        tariff_meters.append(dict(id=f"fee:{b['entity']}:{b['period']}",participant=reg.participant,
            period=periods[b["period"]].id,energy_mwh=b["MQ"],hours=periods[b["period"]].hours,
            voltage=reg.voltage,purpose="eligible_storage_charging" if b["entity"]=="battery_load" else "consumption"))
    tariff_assessments=post_tariffs(ledger,tariff_meters,s["tariffs"])
    # Daily accruals remain separate from final monthly reserve/reconciliation cost.
    from calendar import monthrange
    days_in_month=monthrange(day.year,day.month)[1]
    for component,account,amount in (("S18","A-D",D(s["accounting_parameters"]["reserve_cost_eur"])/days_in_month),
                                   ("S19","A-H",D(s["accounting_parameters"]["reconciliation_cost_eur"])/days_in_month)):
        recover(ledger,component,account,amount,base,day,"13.8.4-5/monthly_cost_daily_accrual")
    seed=list(ledger.entries)
    daily_recoveries=[]
    for t in periods:
        local=[b for b in bridges if b["period"]==t.period]
        native={}
        for b in local:
            reg=regs[b["entity"]]
            if reg.role=="offtake":native[reg.participant]=native.get(reg.participant,D(0))+D(b["MQ"])
        daily_recoveries.append(period_recovery(ledger,t.id,native,{b["entity"]:b["UNINSTQ"] for b in local},
            {e:s["brp_participants"][r.brp] for e,r in regs.items()},"qualified_consumption_fallback"))
        storage_cost=sum((D(e.cash_eur) for e in seed if e.period==t.id and e.component=="S27"),D(0))
        recover(ledger,"S28","MS",storage_cost,native,t.id,"MS6.2.3")
    for component,account,rule in (("S26","MM","MM4/MY_frozen_shares"),):
        amount=-ledger.balances().get(account,D(0))
        allocation=s["accounting_parameters"]["frozen_MM_shares"] if account=="MM" else base
        recover(ledger,component,account,amount,allocation,day,rule)
    daily_penalties=daily_activation_penalties(ledger,bridges,regs,day,
        s["accounting_parameters"]["activation_penalty_rates"],s["accounting_parameters"]["opening_activation_counters"])
    financial=financial_fixtures(ledger,day,regs,risk_inputs=s["financial_inputs"])
    participant_financials=participant_results(ledger,results,rows,units,private_known)
    monthly=None;monthly_financial=None;revision_financial=None;revision=None;penalties=None
    if accounting_cycles:
        monthly=full_cycles(day,periods,bridges,schedules,regs,prices,ledger,s["storage"],s["tariffs"],s["accounting_parameters"],results)
        monthly_ledger=Ledger()
        for e in monthly["ledger"]:
            monthly_ledger.post(e["id"],e["component"],e["account"],e["counterparty"],e["quantity"],e["price"],
                e["period"],e["rule"],e["source"],revision=e["revision"],cash=e["cash_eur"])
        monthly_final=date.fromisoformat(monthly["calendar"]["final"])
        monthly_financial=financial_fixtures(monthly_ledger,monthly_final,regs,scope="final_monthly_nonDAM",risk_inputs=s["financial_inputs"],
            category_issue_dates={"MM":date.fromisoformat(monthly["MM_calendar"]["invoice"]),"NON_DAM":monthly_final})
        revision_financial={}
        for kind in ("reconciliation","cumulative"):
            adjusted=Ledger()
            for e in monthly[kind]["ledger"]:
                adjusted.post(e["id"],e["component"],e["account"],e["counterparty"],e["quantity"],e["price"],
                    e["period"],e["rule"],e["source"],revision=e["revision"],cash=e["cash_eur"])
            final=date.fromisoformat(monthly["calendars"][kind]["invoice"])
            revision_financial[kind]=financial_fixtures(adjusted,final,regs,scope=kind,risk_inputs=s["financial_inputs"])
        for id,versions in monthly["statement_versions"].items():
            for version in versions:
                event("accounting_statement",datetime.fromisoformat(version["issued_at"]),statement=id,version=version["version"],state=version["state"])
        revision={**monthly["reconciliation"],"meter_delta_mwh":1,"frozen_dispatch":True,
                  "classification":"full_affected_component_recalculation"}
        penalties=monthly["calculations"]["monthly_penalties"]
    audit=audit_day(s,dam,schedules,runs,results,bridges,ledger,revision,financial)
    conditional=conditional_evidence(s)
    later=(later_stage_audit(monthly,monthly_financial,storage_rows,s,revision_financial)
           if accounting_cycles else dict(status="skipped_by_scope",passed=None,checks=[]))
    for stage,checks in (("conditional_events",conditional["checks"]),("later_settlement",later["checks"])):
        for c in checks:
            record=dict(stage=stage,name=c["name"],passed=c["passed"],residual=0 if c["passed"] else 1,
                        tolerance=0,details=c)
            audit["details"].append(record);audit["checks"]+=1
            if not c["passed"]:audit["failures"].append(record)
        audit["stages"][stage]=dict(checks=len(checks),failures=sum(not c["passed"] for c in checks),
                                    maximum_residual=int(any(not c["passed"] for c in checks)))
    audit["passed"]=not audit["failures"]
    catalogue=coverage(ledger,monthly,conditional,later,storage_capacity_enabled=permissions.get('participation_balancing_capacity',False))
    common_complete=(private_known and all(r["pricing_method"]=="fixed_discrete_lp_shadow_prices_v2" for r in runs)
              and audit["passed"] and conditional["passed"])
    daily_complete=common_complete and all(v["status"] in ("complete","not_applicable","deferred_by_scope") for v in catalogue.values())
    complete=(common_complete and accounting_cycles and later["passed"]
              and all(v["status"] in ("complete","not_applicable") for v in catalogue.values()))
    selected_complete=complete if accounting_cycles else daily_complete
    metrics=dict(demand_mwh=total_consumption,res_mwh=sum(r["res_actual_mwh"] for r in results),
                 thermal_mwh=sum(row["actual_mwh"] for r in results for row in r["rows"]),
                 up_mwh=sum(row["up_mwh"] for r in results for row in r["rows"]),
                 down_mwh=sum(row["down_mwh"] for r in results for row in r["rows"]),
                 dam_price_min=min(dam["prices_eur_mwh"]),dam_price_max=max(dam["prices_eur_mwh"]),
                 rtbm_price_min=min(r["price_eur_mwh"] for r in results),rtbm_price_max=max(r["price_eur_mwh"] for r in results),
                 resource_cost_eur=sum(row["actual_mwh"]*units[row["entity"]].energy_eur_mwh for r in results for row in r["rows"]),
                 storage_charge_mwh=sum(D(r["charge_mwh"]) for r in storage_rows),
                 storage_discharge_mwh=sum(D(r["discharge_mwh"]) for r in storage_rows),
                 ledger_entries=len(ledger.entries),periods=len(periods),rtus=len(results),isp_runs=len(runs))
    metrics['physical_imbalance_mwh']=sum(r['actual_balance_residual_mwh'] for r in results)
    manifest={**s["ruleset"],"version":"0.3.0","day":day.isoformat(),"case":s.get("case","ordinary"),
        "calculation_complete":complete,"current_rules_conformance_verified":False,
        "physical_security_feasible":all(r["physical_security_feasible"] for r in runs) and all(r["physical_security_feasible"] for r in results),
        "accounting_cycles_enabled":accounting_cycles,
        "calculation_scope":"full_accounting_cycles" if accounting_cycles else "daily_accrual",
        "daily_calculation_complete":daily_complete,"selected_scope_calculation_complete":selected_complete,
        "implemented_path_audit_passed":audit["passed"],"private_cash_known":private_known,
        "release_status":("qualified_synthetic_complete" if complete else "qualified_daily_accrual_complete" if daily_complete else "provisional_or_failed_validation"),
        "full_joint_price_audit_performed":False,
        "reserve_price_range_diagnostics_performed":price_ranges,
        "reserve_pricing_method":"fixed_discrete_lp_shadow_prices_v2",
        "rtbm_forecast_method":"TSO_baseline_plus_latest_completed_RTU_error",
        "system_forecast_source":tso_source,"thermal_physics":"shared_thermal_path_kernel_v1",
        "runtime":{"python":platform.python_version(),"pulp":pulp.__version__,
                   "solver":"HiGHS" if pulp.HiGHS().available() else "CBC"},
        "code_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))},
        "source_manifest_sha256":hashlib.sha256((Path(__file__).parents[1]/"docs/sources/manifest.json").read_bytes()).hexdigest(),
        "metrics":metrics,"limitations":limitations()}
    skipped=dict(status="skipped_by_scope",reason="Daily-only run; enable accounting_cycles for monthly/cumulative/reconciliation calculations")
    output=Path(output)
    for name,value in dict(manifest=manifest,inputs={"units":[asdict(u) for u in s["units"]],
        "registrations":{k:asdict(v) for k,v in regs.items()},"forwards":[asdict(c) for c in s["forwards"]],
        "orders":[asdict(o) for o in s["orders"]],"forecasts":[asdict(f) for f in s["forecasts"]],
        "requirements_mw":s["requirements"],"system_forecast_mw":tso_rtu,"historical_prices":s["history"],"storage":s["storage"],
        "financial_inputs":s["financial_inputs"],"tariffs":s["tariffs"],"accounting_parameters":s["accounting_parameters"],"RR_procurement":s["RR_procurement"],
        "mandatory_on":s["mandatory_on"],"non_delivery_events":s["non_delivery_events"],
        "operational_event_inputs":s.get("operational_events",[]),"ISP_emergency_resources":s.get("ISP_emergency_resources",[]),
        "RTBM_emergency_resources":s.get("emergency_resources_by_rtu",{})},
        events=sorted(events,key=lambda e:datetime.fromisoformat(e["at"])),notifications=[asdict(n) for n in notifications.records],dam=dam,commercial_schedules=schedules,
        isp=runs,rtbm=results,operational_events=operational,metering_bridges=bridges,settlement_prices=prices,
        ledger=ledger.export(),meters=meters,storage=storage_rows,storage_offer_feasibility=storage_offer_feasibility,
        account_balances={k:str(v) for k,v in ledger.balances().items()},
        daily_penalties=daily_penalties,penalties=penalties if penalties is not None else skipped,reconciliation=revision if revision is not None else skipped,
        financial=financial,monthly_financial=monthly_financial if monthly_financial is not None else skipped,
        revision_financial=revision_financial if revision_financial is not None else skipped,
        statements=statements.versions,tariffs=tariff_assessments,
        uplift_derivations=daily_recoveries,
        participant_financials=participant_financials,brp_settlement=brp_settlement,accounting_cycles=monthly if monthly is not None else skipped,
        coverage=catalogue,audit=audit,conditional_evidence=conditional,later_stage_audit=later).items():
        write_json(output/f"{name}.json",value)
    summary=render_report(manifest,audit,catalogue)
    (output/"REPORT.md").write_text(summary,encoding="utf-8")
    print(summary,flush=True)
    return manifest,audit


def financial_fixtures(ledger,day,registrations,scope="daily_accrual",risk_inputs=None,category_issue_dates=None):
    participants={r.participant:r.member for r in registrations.values()}
    participants.update(storage_BSP="CM_storage",contingency_provider="CM_contingency")
    positions=[]
    for e in ledger.entries:
        if e.component in ("S01","S25"):
            continue
        category="DAM" if e.component=="S02" else "MM" if e.component in ("S24","S26") else "NON_DAM"
        for side,account,sign in (("receipt",e.account,1),("payment",e.counterparty,-1)):
            participant=account.split(":")[1] if account.startswith("participant:") else "market_operator"
            member=participants[participant] if participant!="market_operator" else "CM_operator"
            positions.append(dict(id=f"{e.id}:{side}",member=member,clearing_account=participant,
                category=category,value_eur=str(D(e.cash_eur)*sign),reference_period=e.period,
                source=e.id,instrument_code=e.component,eic=f"SYNTHETIC:{participant}"))
    cycle=ClearingCycle(f"cycle:{day}",set(participants.values())|{"CM_operator"})
    frozen=cycle.finalise(positions)
    invoices=[]
    for position in frozen["net_positions"]:
        account=f"{position['member']}:{position['clearing_account']}:{position['category']}"
        issue=category_issue_dates.get(position["category"],day) if category_issue_dates else day
        clearing_day=business_offset(issue,0 if position["category"]=="DAM" else 2,frozenset())
        doc=invoice(f"INV:{account}",[position["receipt_eur"]],issue,"0",cleared_on=clearing_day)
        doc["clearing_day_provenance"]="synthetic_cycle_inside_invoice_objection_resolution_windows"
        invoices.append(pay(doc,abs(D(doc["gross_eur"]))))
    opening={k:D(100000000) for k in set(participants.values())|{"CM_operator","clearing_body"}}
    cash=CashBook(dict(opening))
    risk={}
    if risk_inputs is None:raise ValueError("Explicit dated role/exposure/security histories required")
    for participant,configuration in risk_inputs.items():
        minimum=minimum_margin(configuration["roles"],configuration["wholesale_rate"],configuration["unit_charges"],configuration["res_fee"])
        net=sum((D(p["receipt_eur"]) for p in frozen["net_positions"] if p["clearing_account"]==participant and p["category"] in ("DAM","MM")),D(0))
        margin=required_margin(max(D(0),-net),configuration["latest_MM_debit"],configuration["six_month_obligations"],minimum["total_eur"])
        available=D(configuration["collateral_eur"])
        if D(margin["total"])>available:raise ValueError(f"Margin shortfall for {participant}")
        member=participants[participant]
        cash.restricted[member]=D(cash.restricted.get(member,0))+D(margin["total"])
        risk[participant]=dict(minimum_method=minimum,margin=margin,eligible_collateral_eur=str(available),
                               headroom_eur=str(available-D(margin["total"])),provenance=configuration["provenance"])
    for position,doc in zip(frozen["net_positions"],invoices):
        amount=D(doc["gross_eur"])
        sender,receiver=("clearing_body",position["member"]) if amount>=0 else (position["member"],"clearing_body")
        cash.transfer(f"cash:{doc['id']}",sender,receiver,abs(amount),
                      datetime.combine(date.fromisoformat(doc["due_on"]),time(11),CYPRUS),doc["id"])
    minimum=minimum_margin([dict(role="generator",capacity_factors_m7_to_m12=[.5]*6,
        capacity_mw_or_authorised_mva=10,imbalance_factor=.1)],100,[12,18,2,1],3)
    return dict(invoices=invoices,positions=positions,finalised_cycle=frozen,
        invoice_scope=scope,risk_by_account=risk,minimum_margin_method=minimum,
        category_issue_dates={k:v.isoformat() for k,v in (category_issue_dates or {}).items()},
        cash=dict(opening_eur={k:str(v) for k,v in opening.items()},
                  closing_eur={k:str(v) for k,v in cash.balances.items()},transfers=cash.transfers,
                  conserved=sum(opening.values(),D(0))==sum(cash.balances.values(),D(0))),
        member_mapping=participants,
        margin=required_margin(1000,200,[100,200,300,250,100,180],800),
        collateral=dict(provided_eur=3000,required_eur=2400,headroom_eur=600,profit_effect_eur=0),
        default=default_waterfall(1000,600,200,{"CM_other_1":100,"CM_other_2":50},500),
        late_charge=late_payment_charge(1000,30))


def participant_results(ledger,rtus,effective_rows,units,private_known):
    result={}
    for account,amount in ledger.balances().items():
        if not account.startswith("participant:"):
            continue
        _,participant,category=account.split(":")
        item=result.setdefault(participant,dict(central_receipts_eur=D(0),private_receipts_eur=D(0),
                                                variable_resource_cost_eur=D(0),startup_cost_eur=D(0),
                                                no_load_cost_eur=D(0)))
        item["private_receipts_eur" if category=="private" else "central_receipts_eur"]+=amount
    for rtu in rtus:
        for r in rtu["rows"]:
            u=units[r["entity"]]
            result[u.participant]["variable_resource_cost_eur"]+=D(r["actual_mwh"])*D(u.energy_eur_mwh)
    for r in effective_rows:
        u=units[r["entity"]]
        result[u.participant]["startup_cost_eur"]+=D(r["startup_cost_eur"])
        result[u.participant]["no_load_cost_eur"]+=D(r["on"])*D(u.no_load_eur_period)
    for participant,item in result.items():
        cost=item["variable_resource_cost_eur"]+item["startup_cost_eur"]+item["no_load_cost_eur"]
        item["total_resource_cost_eur"]=cost
        item["net_cash_less_modelled_resource_cost_eur"]=(item["central_receipts_eur"]+item["private_receipts_eur"]-cost) if private_known else None
        item["negative_net_cash_cost_diagnostic"]=(item["net_cash_less_modelled_resource_cost_eur"]<0) if private_known else None
        item["profit_scope"]="test_day_accrual_before_fixed_capital_costs_tax_and_full_monthly_assessments"
        for key,value in list(item.items()):
            if isinstance(value,Decimal):item[key]=str(value)
    return result


def limitations():
    return [
        "Qualified synthetic rates, participants, histories and technical characteristics; no calibration to observed Cyprus operation or actual invoices.",
        "D02/D03 historical/weighting conventions, conditional fixed-discrete LP pricing, synthetic DST/financial cycles and documented exceptional-mode conventions are not certified as the TSOC software implementation.",
        "Exact DAM block enumeration supports at most ten groups; larger books fail validation rather than silently using an unverified heuristic.",
        "ISP uses declared half-hour thermal trajectories; RTBM and delivery use 30-second ramp/hold nodes. Dynamic frequency simulation and internal network constraints are outside scope.",
        "The selected day is physically simulated. Other settlement months use complete imported synthetic meter/trade/award/telemetry inputs, not additional claimed dispatch simulations.",
        "Negative cash less modelled resource costs is reported as a diagnostic. No generic make-whole or official nonconvex welfare-protection equivalence is asserted."]


def coverage(ledger,monthly,conditional=None,later=None,storage_capacity_enabled=True):
    result={}
    no_event={"S10":"No above-registered-capacity emergency event in this day",
              "S12":"No contingency-energy instruction in this day"}
    for id,name in CATALOGUE.items():
        entries=[e for e in ledger.entries if e.component==id]
        monthly_entries=[e for e in monthly["ledger"] if e["component"]==id] if monthly is not None else []
        status="complete" if entries or monthly_entries else "missing_input"
        if id in no_event and not entries:
            status="not_applicable"
        if id=="S13" and not entries:
            status="not_applicable";no_event[id]="No instructed startup in this day; cold/soak fixture supplied"
        if id in ("S29","S30","S31","S32","S33"):
            status="complete" if later and later["passed"] else "failed_validation"
        if conditional and not conditional["passed"]:status="failed_validation"
        result[id]=dict(name=name,status=status,postings=len(entries),
                        cash_eur=str(sum((D(e.cash_eur) for e in entries),D(0))),
                        monthly_postings=len(monthly_entries),
                        conditional_fixture=(conditional or {}).get("component_fixtures",{}).get(id),
                        reason=no_event.get(id,"Computed from declared inputs; formula and revision lineage retained"))
    if monthly is not None:
        result["S29"].update(status="complete" if monthly["status"]=="complete_input_recalculation" and later and later["passed"] else "failed_validation",
                              reason="Full month input recalculation, monthly statement and financial clearing; one day physically simulated")
    else:
        for id in ("S23","S29","S30","S31"):
            result[id].update(status="deferred_by_scope",reason="Requires complete-period accounting; deliberately skipped in daily-only mode")
        result["S23"]["reason"]="Day-observable activation failures assessed; monthly statistical penalties and redistribution deferred"
        for id in ("S32","S33"):
            result[id].update(status="complete",reason="Daily accrual clearing and conditional checks only; later-cycle clearing is deferred")
    if not storage_capacity_enabled:
        result['S27'].update(status='not_applicable',reason='Storage balancing capacity participation disabled by ruleset')
    return result


def render_report(manifest,audit,catalogue):
    m=manifest["metrics"]
    lines=["# v0.3 test-day audit",f"\nDelivery day: {manifest['day']} (synthetic, isolated zone).",
           f"\nImplemented-path audit: {'PASS' if audit['passed'] else 'FAIL'}; {audit['checks']} checks; {len(audit['failures'])} failures.",
           f"\nFull specification calculation complete: **{manifest['calculation_complete']}**. Current-rules conformance verified: **False**.",
           f"\nSelected scope: **{manifest['calculation_scope']}**; selected-scope calculation complete: **{manifest['selected_scope_calculation_complete']}**. Daily results are accruals, not final monthly settlement.",
           f"\n{m['periods']} trading periods; {m['rtus']} RTUs; {m['isp_runs']} ISP runs (initial, 11:00 and any declared exceptional reruns).",
           f"\nDemand {m['demand_mwh']:.6f} MWh; RES {m['res_mwh']:.6f} MWh; thermal {m['thermal_mwh']:.6f} MWh.",
           f"\nDAM price {m['dam_price_min']:.2f}â€“{m['dam_price_max']:.2f} EUR/MWh; RTBM {m['rtbm_price_min']:.2f}â€“{m['rtbm_price_max']:.2f} EUR/MWh.",
           "\n## Independent stage checks\n","| Stage | Checks | Failures |","|---|---:|---:|"]
    for stage,s in audit["stages"].items():
        lines.append(f"| {stage} | {s['checks']} | {s['failures']} |")
    lines += ["\n## Settlement coverage\n","| ID | Component | Status | Postings |","|---|---|---|---:|"]
    for id,row in catalogue.items():
        lines.append(f"| {id} | {row['name']} | {row['status']} | {row['postings']} |")
    lines += ["\n## Model conventions and calibration limits\n"]+[f"- {v}" for v in manifest["limitations"]]
    if audit["failures"]:
        lines += ["\n## Failures\n"]+[str(f) for f in audit["failures"][:30]]
    return "\n".join(lines)+"\n"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--day",type=date.fromisoformat,default=date(2026,11,5))
    parser.add_argument("--case",choices=("ordinary","trip","high_res"),default="ordinary")
    parser.add_argument("--output",type=Path,default=Path("output/test_day"))
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--price-ranges",action="store_true",help="Optional reserve-price ranges and finite-difference diagnostics; settlement still uses LP shadow prices")
    group.add_argument("--skip-price-ranges",dest="price_ranges",action="store_false",help="Compatibility alias for the default: LP shadow prices without range diagnostics")
    parser.set_defaults(price_ranges=False)
    parser.add_argument("--accounting-cycles",action="store_true",help="Include full monthly, cumulative and reconciliation calculations and their financial clearing")
    parser.add_argument("--require-complete",action="store_true",help="Exit 2 if the selected calculation scope is incomplete")
    args=parser.parse_args()
    manifest,audit=run_day(args.day,args.output,args.price_ranges,synthetic_case(args.day,args.case),accounting_cycles=args.accounting_cycles)
    if not audit["passed"]:
        raise SystemExit(1)
    if args.require_complete and not manifest["selected_scope_calculation_complete"]:
        raise SystemExit(2)


if __name__=="__main__":
    main()
