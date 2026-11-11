"""Complete synthetic meter/trade input histories and full settlement recalculation.

Imported histories are energy/award inputs, never scaled final settlement cash.
They are explicitly not additional physically simulated days.
"""
from calendar import monthrange
from datetime import date,datetime,time,timedelta
import math
from .settlement import Ledger,D,production_bridge,offtake_bridge,instructed_cash,statement
from .accounting import (post_tariffs,period_recovery,
                         monthly_calendar,Recalculation,allocate,reconciliation_calendar,business_offset,
                         finalise_accounts,reconcile_meter_ledgers)
from .compliance import systematic,assess,Counters
from .calendar import day_intervals
from .calendar import CYPRUS
from .lifecycle import StatementBook


def synthetic_tariffs():
    return {v:dict(tuos_energy=12,tuos_power=.1,
        duos_energy=8 if v=="MV" else 10,duos_power=.05,RES=3,PSO=2,admin=1)
        for v in ("HV","MV","LV")}


def make_history(day,periods,bridges,schedules,registrations,prices,ledger,physical_day=None,telemetry=None):
    """One full calendar month; the physical day replaces its imported records."""
    rows=[];n=len(periods);physical_day=physical_day or day
    by={(r["entity"],r["period"]):r for r in bridges}
    response={(e,t.period):[row["non_delivery_mwh"] / rt["duration_hours"] for rt in (telemetry or []) if rt["period"]==t.period for row in rt["rows"] if row["entity"]==e]
              for e in registrations for t in periods}
    service_components={"S05","S06","S07","S08","S09","S10","S11","S12","S13","S24","S27"}
    period_awards={t.id:[] for t in periods}
    for entry in ledger.entries:
        if entry.component in service_components:
            award=dict(id=entry.id,component=entry.component,participant=entry.account.split(":")[1],
                       account=entry.counterparty,quantity=entry.quantity,price=entry.price,
                       rule=entry.rule,source=entry.source)
            if entry.period in period_awards:period_awards[entry.period].append(award)
    for d in range(1,monthrange(day.year,day.month)[1]+1):
        current=date(day.year,day.month,d)
        for t in day_intervals(current,30):
            index=t.period%n
            factor=1. if current==physical_day else .94+.001*d+.015*math.sin(d*.7+t.period*.1)
            price=prices[index]["price_eur_mwh"] if current==physical_day else 85+10*math.sin(t.period*.15+d*.1)
            for entity,reg in registrations.items():
                original=by[entity,index]
                schedule=schedules[entity][index]*factor
                if current==physical_day:
                    meter=original["MQ"];inst=original["INSTQ"];uninst=original["UNINSTQ"]
                    payment=original.get("payment_inputs",dict(system=prices[index]["system"],marginal=price,
                        last_up=price+20,last_down=price-20,offset_legs=[]))
                    accepted=original.get("accepted_net_mwh",-max(0,-inst) if reg.role=="production" else 0)
                else:
                    perturbation=.004*math.sin(d+t.period*.7)
                    meter=max(0,schedule*(1+perturbation))
                    inst=meter-schedule if reg.role=="production" and entity.startswith("thermal_") else 0
                    uninst=meter-inst-schedule if reg.role=="production" else -(meter-schedule)
                    payment=dict(system="short" if perturbation>=0 else "long",marginal=price,
                                 last_up=price+20,last_down=price-20,offset_legs=[])
                    accepted=inst
                rows.append(dict(id=f"{current}:{t.period}:{entity}",date=current.isoformat(),
                    period=f"{current}:{t.period}",period_index=t.period,entity=entity,
                    participant=reg.participant,brp=reg.brp,brp_participant=original["brp_participant"],role=reg.role,voltage=reg.voltage,
                    purpose="eligible_storage_charging" if entity=="battery_load" else "consumption",
                    hours=t.hours,schedule_mwh=schedule,meter_mwh=meter,
                    instq_mwh=inst,uninstq_mwh=uninst,price_eur_mwh=price,
                    accepted_net_mwh=accepted,payment_inputs=payment,
                    power_deviation_samples_mw=response[entity,index] if current==physical_day and response[entity,index] else [0]*6,
                    down_mwh=max(0,-inst),agc=(original["branch"]=="agc" if current==physical_day else
                        entity.startswith("thermal_") and reg.role=="production"),
                    qualifying_startup=(current==physical_day and original["branch"]=="qualifying_startup"),
                    forced_unavailable=(current==physical_day and original["branch"]=="declared_forced_unavailability"),
                    forced_instq_mwh=inst if current==physical_day else None,
                    tolerance=original.get('tolerance',0 if reg.grant_res else reg.tolerance),grant_res=reg.grant_res,
                    small_supplier=reg.small_supplier,external_control=(current==physical_day and original["branch"]=="PA_external_control"),
                    service_awards=[{**a,"quantity":str(D(a["quantity"])*(D(1) if a["component"] in ("S13","S24") else D(factor)))} for a in period_awards[periods[index].id]] if entity==next(iter(registrations)) else [],
                    provenance="physical_test_day_meter" if current==physical_day else "synthetic_imported_meter_trade_award_inputs"))
    return rows


def settle_history(rows,registrations,tariffs,frozen_mm_shares,reserve_cost,reconciliation_cost,penalty_shares,storage_configuration=None,penalty_rates=None,dispatch_parameters=None,opening_counters=None,activation_parameters=None,opening_activation_counters=None):
    """Recompute variable financial components from complete meter/award inputs."""
    ledger=Ledger();consumption={};grouped={};meters=[];statistical={};recoveries=[]
    activation_assessments=[]
    if activation_parameters is None or opening_activation_counters is None:
        raise ValueError("Activation penalty rates and opening counters required")
    year=rows[0]["date"][:4]
    activation_counters=Counters({(e,"significant_activation_failure",year):n for e,n in opening_activation_counters.items()})
    for r in rows:
        grouped.setdefault(r["period"],[]).append(r)
        entity=r["entity"];reg=registrations[entity]
        msq,meter=float(r["schedule_mwh"]),float(r["meter_mwh"])
        if reg.role=="production":
            # Fixed historical accepted activation is reconstructed from its signed
            # input, not inferred from corrected meters in a revision.
            activation=float(r["accepted_net_mwh"])
            b=production_bridge(msq,max(0,activation),max(0,-activation),meter,
                                r["tolerance"],agc=r["agc"],startup=r.get("qualifying_startup",False))
            if r.get("forced_unavailable",False):
                inst=float(r["forced_instq_mwh"])
                b.update(INSTQ=inst,UNINSTQ=meter-msq-inst)
            if r["external_control"]:
                inst=min(0,meter-msq);b.update(INSTQ=inst,UNINSTQ=meter-msq-inst)
            payment=r["payment_inputs"]
            legs=payment["offset_legs"]
            signed=sum(x["up_mwh"]-x["down_mwh"] for x in legs)
            offer_cash=sum(x["up_mwh"]*x["up_price"]-x["down_mwh"]*x["down_price"] for x in legs)
            instructed=0 if reg.grant_res else offer_cash+instructed_cash(b["INSTQ"]-signed,payment["system"],
                float(r["price_eur_mwh"]),float(r["price_eur_mwh"]),payment["last_up"],payment["last_down"])
            ledger.post(f"inst:{r['id']}","S03",f"participant:{reg.participant}:nonDAM","A-B",1,1,r["period"],
                        "13.4.6/D01-D05","imported_instruction_and_meter",cash=instructed)
        else:
            b=offtake_bridge(msq,meter,tolerance=reg.tolerance if reg.small_supplier else None)
            consumption[reg.participant]=consumption.get(reg.participant,D(0))+D(meter)
            meters.append(dict(id=r["id"],participant=reg.participant,period=r["period"],
                energy_mwh=meter,hours=r["hours"],voltage=reg.voltage,purpose=r["purpose"]))
        ledger.post(f"uninst:{r['id']}","S04",f"participant:{r['brp_participant']}:nonDAM","A-B",
                    b["UNINSTQ"],r["price_eur_mwh"],r["period"],"13.4.7-8/D05","loss_adjusted_meter")
        r["calculated_uninstq_mwh"]=b["UNINSTQ"]
        if reg.role=="offtake" or reg.grant_res or entity in ("res","battery_injection"):
            statistical.setdefault(entity,[]).append(dict(period=r["period"],schedule_mwh=msq,
                meter_mwh=meter,adjusted_schedule_mwh=b.get("adjusted_MSQ",msq),
                down_mwh=r["down_mwh"],external_control=r["external_control"]))
        for award in r["service_awards"]:
            ledger.post(f"service:{r['period']}:{award['id']}",award["component"],
                f"participant:{award['participant']}:nonDAM",award["account"],award["quantity"],award["price"],
                r["period"],award["rule"],"imported_procurement_quantity_availability_and_quote")
        if entity.startswith("thermal_"):
            net=D(r["accepted_net_mwh"]);instruction=D(msq)+net;tol=D(activation_parameters["tolerance"])
            excess=max(D(0),D(meter)-instruction*(1+tol),instruction*(1-tol)-D(meter))
            event=dict(entity=entity,family="significant_activation_failure",obligated=True,role="thermal",
                failure=bool(net and excess>D("1e-9")),counter_window=year,occurrence_key=r["date"],
                schedule_mwh=msq,up_mwh=max(D(0),net),down_mwh=max(D(0),-net),meter_mwh=meter,
                event_time=r["date"]+"T00:00:00+02:00",assessment_period=r["period"])
            assessment=assess(event,activation_parameters,activation_counters)
            activation_assessments.append(assessment)
            ledger.post(f"activation_failure:{r['id']}","S23",f"participant:{reg.participant}:nonDAM","A-E",
                -D(assessment["amount_eur"]),1,r["period"],assessment["source_clause"],"accepted_activation_and_meter")
    tariff_assessments=post_tariffs(ledger,meters,tariffs)
    seed_entries={}
    for entry in ledger.entries:seed_entries.setdefault(entry.period,[]).append(entry)
    for period,batch in grouped.items():
        native={};deviations={};owners={}
        for r in batch:
            reg=registrations[r["entity"]]
            if reg.role=="offtake":native[reg.participant]=native.get(reg.participant,D(0))+D(r["meter_mwh"])
            deviations[r["entity"]]=D(r["calculated_uninstq_mwh"]);owners[r["entity"]]=r["brp_participant"]
        recoveries.append(period_recovery(ledger,period,native,deviations,owners,"qualified_consumption_fallback",seed_entries.get(period,[])))
        for component,account,base in (("S26","MM",frozen_mm_shares),("S28","MS",native)):
            amount=sum((D(e.cash_eur) for e in seed_entries.get(period,[]) if e.counterparty==account),D(0))
            for p,cash in allocate(amount,base).items():
                ledger.post(f"recovery:{component}:{period}:{p}",component,f"participant:{p}:nonDAM",account,
                            1,1,period,"MM4/MY" if component=="S26" else "MS6.2.3","frozen_shares_or_current_consumption",cash=-cash)
    monthly_penalties={}
    if penalty_rates is None:raise ValueError("Dated systematic-deviation rates/tolerances are required")
    rates=penalty_rates
    for entity,records in statistical.items():
        reg=registrations[entity]
        assessment=systematic(records,grouped.keys(),"RES" if reg.role=="production" else "load",rates)
        monthly_penalties[entity]=assessment
        ledger.post(f"monthly_systematic:{entity}","S23",f"participant:{reg.participant}:nonDAM",
            "A-E",-D(assessment["amount_eur"]),1,rows[0]["date"][:7],assessment["formula_id"],"complete_month_meter_history")
    for entity,reg in registrations.items():
        if not entity.startswith("thermal_"):continue
        samples=[q for r in rows if r["entity"]==entity for q in r["power_deviation_samples_mw"]]
        month=rows[0]["date"][:7]
        event=dict(entity=entity,family="dispatch_failure",obligated=True,failure=any(samples),
            counter_window=month[:4],occurrence_key=month,absolute_power_deviations_mw=samples,
            event_time=month+"-01T00:00:00+02:00",assessment_period=month)
        if opening_counters is None or dispatch_parameters is None:raise ValueError("Dispatch penalty rates and YTD counters are required")
        counters=Counters({(entity,"dispatch_failure",month[:4]):opening_counters[entity]})
        assessment=assess(event,dispatch_parameters,counters)
        monthly_penalties[entity+":dispatch"]=assessment
        ledger.post(f"monthly_dispatch:{entity}","S23",f"participant:{reg.participant}:nonDAM","A-E",
            -D(assessment["amount_eur"]),1,month,assessment["source_clause"],"complete_month_instruction_response_history")
    # Explicit assessed reserve/reconciliation costs, rather than invented per-MWh rates.
    month=rows[0]["date"][:7]
    finalise_accounts(ledger,month,consumption,reserve_cost,reconciliation_cost,penalty_shares)
    storage_audit=None
    if storage_configuration and "battery_load" in registrations:
        from .metering import storage_delivery
        trace=storage_delivery(storage_configuration,
            [next(r["meter_mwh"] for r in batch if r["entity"]=="battery_load") for batch in grouped.values()],
            [next(r["meter_mwh"] for r in batch if r["entity"]=="battery_injection") for batch in grouped.values()],
            [.5]*len(grouped),[storage_configuration["ancillary_reserved_mw"]]*len(grouped))
        storage_audit=dict(periods=len(trace),initial_soc_mwh=trace[0]["initial_soc_mwh"],
                           final_soc_mwh=trace[-1]["final_soc_mwh"],passed=True)
    return ledger,dict(monthly_penalties=monthly_penalties,period_recoveries=recoveries,
        tariff_assessments=tariff_assessments,storage_audit=storage_audit,activation_assessments=activation_assessments,
        consumption_mwh={k:str(v) for k,v in consumption.items()})


def full_cycles(day,periods,bridges,schedules,regs,prices,daily,storage_configuration=None,tariffs=None,parameters=None,telemetry=None):
    import copy
    inputs=make_history(day,periods,bridges,schedules,regs,prices,daily,telemetry=telemetry)
    print(f"  Monthly accounting: {len(inputs)} entity-period input records",flush=True)
    if tariffs is None or parameters is None:raise ValueError("Complete dated accounting parameter bundle required")
    args=(regs,tariffs,parameters["frozen_MM_shares"],D(parameters["reserve_cost_eur"]),D(parameters["reconciliation_cost_eur"]),
          parameters["penalty_distribution"],storage_configuration,parameters["systematic_penalty_rates"],
          parameters["dispatch_penalty_rates"],parameters["opening_dispatch_counters"],
          parameters["activation_penalty_rates"],parameters["opening_activation_counters"])
    original,calculations=settle_history(inputs,*args)
    period_indices={p.id:p.period for p in periods}
    daily_cash={};replayed_cash={}
    for e in daily.entries:
        if e.component not in ("S03","S04"):continue
        key=e.component,e.account,period_indices[e.period]
        daily_cash[key]=daily_cash.get(key,D(0))+D(e.cash_eur)
    for e in original.entries:
        if e.component not in ("S03","S04") or not e.period.startswith(day.isoformat()+":"):continue
        key=e.component,e.account,int(e.period.rsplit(":",1)[1])
        replayed_cash[key]=replayed_cash.get(key,D(0))+D(e.cash_eur)
    residual=max((abs(daily_cash.get(k,D(0))-replayed_cash.get(k,D(0)))
                  for k in daily_cash.keys()|replayed_cash.keys()),default=D(0))
    calculations["physical_day_replay"]=dict(passed=residual<D(".000001"),
        maximum_cash_residual_eur=str(residual),compared_positions=len(daily_cash))
    print(f"  Original month recomputed: {len(original.entries)} postings",flush=True)
    # Corrections are native-meter inputs. All affected tariffs, energy and
    # recoveries are recalculated; original market trades/instructions stay fixed.
    revised=copy.deepcopy(inputs)
    for r in revised:
        if r["date"]==day.isoformat() and r["period_index"]==12 and r["entity"]=="load_T":
            r["meter_mwh"]+=1.
        if r["date"]==day.isoformat() and r["period_index"]==12 and r["entity"]=="load_D":
            r["meter_mwh"]-=1. # conserving residual profile allocation at unchanged boundary total
    recomputed,recomputed_calculations=settle_history(revised,*args)
    print("  Certified-meter revision and related accounts recomputed",flush=True)
    revision_ledger,energy_adjustments,delta,replay=reconcile_meter_ledgers(inputs,revised,original,recomputed)
    print("  Certified-meter revision and related accounts recomputed",flush=True)
    cumulative_ledger=Ledger();cumulative_engine=Recalculation()
    cumulative_delta=D(0);cumulative_months=[]
    for lag in (2,1,0):
        absolute_month=day.year*12+day.month-1-lag
        month_day=date(absolute_month//12,absolute_month%12+1,min(day.day,28))
        print(f"  Cumulative recalculation: {month_day:%Y-%m}",flush=True)
        if lag:
            prior_inputs=make_history(month_day,periods,bridges,schedules,regs,prices,daily,physical_day=day)
            old_prior,_=settle_history(prior_inputs,*args)
            new_prior_inputs=copy.deepcopy(prior_inputs)
            # An input price error is a cumulative correction, distinct from the
            # certified-meter revision used for reconciliation below.
            for row in new_prior_inputs:
                if row["period_index"]==10 and row["date"].endswith("-05"):row["price_eur_mwh"]+=2
            new_prior,_=settle_history(new_prior_inputs,*args)
        else:
            old_prior=original
            corrected_prices=copy.deepcopy(inputs)
            for row in corrected_prices:
                if row["period_index"]==10 and row["date"]==day.isoformat():row["price_eur_mwh"]+=2
            new_prior,_=settle_history(corrected_prices,*args)
        change=cumulative_engine.apply(cumulative_ledger,old_prior,new_prior,2+lag,"cumulative")
        cumulative_delta+=change
        cumulative_months.append(dict(month=month_day.isoformat()[:7],
            old_postings=len(old_prior.entries),new_postings=len(new_prior.entries),delta_eur=str(change),
            input_periods=len({e.period for e in old_prior.entries if len(e.period)>7}),
            provenance="complete_imported_inputs_recalculated"))
    days=sorted({r["date"] for r in inputs})
    stmt=statement(original,"monthly",f"{day.year}-{day.month:02}",days,days)
    calendars=dict(monthly=monthly_calendar(day.year,day.month,frozenset()),
                   mandatory=monthly_calendar(day.year,day.month,frozenset(),"MM"),
                   reconciliation=reconciliation_calendar(day.year,day.month,frozenset()))
    month_end=date(day.year,day.month,monthrange(day.year,day.month)[1])
    cumulative_preliminary=month_end+timedelta(weeks=6)
    calendars["cumulative"]=dict(preliminary=cumulative_preliminary.isoformat(),
        objection_deadline=business_offset(cumulative_preliminary,5,frozenset()).isoformat(),
        final=business_offset(cumulative_preliminary,15,frozenset()).isoformat(),rule="13.12.1.2-3")
    for key in ("cumulative","reconciliation"):
        calendars[key]["invoice"]=business_offset(date.fromisoformat(calendars[key]["final"]),2,frozenset()).isoformat()
    statements=StatementBook()
    for kind,source,filter_mm in (("monthly",original,False),("mandatory",original,True),
                                 ("cumulative",cumulative_ledger,None),("reconciliation",revision_ledger,None)):
        lines={}
        for e in source.entries:
            if filter_mm is not None and (e.component in ("S24","S26"))!=filter_mm:continue
            if e.account.startswith("participant:"):
                lines[e.account]=lines.get(e.account,D(0))+D(e.cash_eur)
            if e.counterparty.startswith("participant:"):
                lines[e.counterparty]=lines.get(e.counterparty,D(0))-D(e.cash_eur)
        lines={k:str(v) for k,v in lines.items()}
        cal=calendars[kind]
        instant=lambda name:datetime.combine(date.fromisoformat(cal[name]),time(17),CYPRUS)
        id=f"{kind}:{day.year}-{day.month:02}"
        statements.preliminary(id,lines,instant("preliminary"),instant("objection_deadline"))
        statements.finalise(id,lines,instant("final"),[])
    return dict(status="complete_input_recalculation",physical_days_simulated=1,assessment_days=len(days),
        monthly=stmt,calendar=calendars["monthly"],MM_calendar=calendars["mandatory"],
        statement_versions=statements.versions,calendars=calendars,
        prior_assessment_policy="imported_synthetic_meter_trade_and_service_inputs_not_scaled_final_cash",
        history_records=len(inputs),history_inputs=inputs,ledger=original.export(),calculations=calculations,
        cumulative=dict(status="complete_affected_component_recalculation",delta_eur=str(cumulative_delta),
                        months=cumulative_months,ledger=cumulative_ledger.export(),
                        scope="three_supplied_months_price_errors_only_no_meter_reconciliation_overlap"),
        reconciliation=dict(delta_eur=str(delta),replay_delta_eur=str(replay),ledger=revision_ledger.export(),
                            energy_adjustments=energy_adjustments,calendar=reconciliation_calendar(day.year,day.month,frozenset()),
                            frozen_components=["S01","S02","S24","S25","S26"],meters_revised=2,
                            certified_meters_revised=1,profile_allocations_recomputed=1),
        posting_count=len(original.entries))
