"""Independent checks against exported decisions, not solver constraint slacks."""
from decimal import Decimal
from datetime import datetime
import math
from .phases import check_rows,unit_profiles
from .model import PRODUCTS,UP,DOWN


class Audit:
    def __init__(self):
        self.checks=[]

    def check(self,stage,name,residual=0,tolerance=1e-5,ok=None,details=None):
        passed = abs(residual)<=tolerance if ok is None else bool(ok)
        self.checks.append(dict(stage=stage,name=name,passed=passed,residual=residual,
                                tolerance=tolerance,details=details))

    def report(self):
        stages={}
        for row in self.checks:
            s=stages.setdefault(row["stage"],dict(checks=0,failures=0,maximum_residual=0))
            s["checks"]+=1
            s["failures"]+=int(not row["passed"])
            s["maximum_residual"]=max(s["maximum_residual"],abs(row["residual"]))
        return dict(passed=all(c["passed"] for c in self.checks),
                    checks=len(self.checks),failures=[r for r in self.checks if not r["passed"]],stages=stages,details=self.checks)


def audit_day(scenario,dam,schedules,runs,rtus,bridges,ledger,revision_result,financial):
    a=Audit()
    units={u.id:u for u in scenario["units"]}
    regs=scenario["registrations"]
    n=len(scenario["periods"])
    a.check("calendar","six RTUs per period",len(rtus)-n*6,tolerance=0)
    for run in runs:
        if run["id"]!="ISP_1100":continue
        projection=run["handover_projection"]
        cutoff=datetime.fromisoformat(run["cutoff"])
        effective_at=datetime.fromisoformat(run["effective_at"])
        a.check("scheduled_ISP_handover","11:00 solve, noon activation",ok=
            (cutoff.hour,cutoff.minute,effective_at.hour,effective_at.minute)==(11,0,12,0))
        expected=[p.period for p in scenario["periods"] if p.start>=effective_at]
        a.check("scheduled_ISP_handover","only noon-onward ISP rows",ok=
            sorted({r["period"] for r in run["rows"]})==expected)
        prior=[r for r in rtus if datetime.fromisoformat(r["issued_at"])<cutoff][-1]
        bridge=[r for r in rtus if cutoff<=datetime.fromisoformat(r["issued_at"])<effective_at]
        a.check("scheduled_ISP_handover","initial awards remain through 11:55",ok=
            len(bridge)==12 and all(r["isp_run"]==prior["isp_run"] for r in bridge))
        a.check("scheduled_ISP_handover","new awards apply from noon",ok=
            all(r["isp_run"]==run["id"] for r in rtus if datetime.fromisoformat(r["issued_at"])>=effective_at))
        a.check("scheduled_ISP_handover","forecast bridge covers exactly 11:00 to noon",ok=
            len(projection["trace"])==12 and datetime.fromisoformat(projection["trace"][0]["start"])==cutoff
            and datetime.fromisoformat(projection["trace"][-1]["end"])==effective_at)
        for r in prior["rows"]:
            e=r["entity"]
            a.check("scheduled_ISP_handover",f"{e} uses actual state available at 11:00",
                projection["observed_state"][e]["end_mw"]-r["actual_terminal_mw"])
            a.check("scheduled_ISP_handover",f"{e} uses forecast noon state for planning",
                run["initial_state"][e]["end_mw"]-projection["projected_state"][e]["end_mw"])
    for t in range(n):
        total=0
        for order in scenario["orders"]:
            if order.period!=t:
                continue
            q=dam["accepted_mwh"][order.id]
            a.check("DAM",f"quantity bounds {order.id}",max(-q,q-order.energy_mwh,0))
            total+=q*(1 if order.side=="sell" else -1)
        a.check("DAM",f"cleared MWh {t}",total)
        a.check("forward_and_schedules",f"commercial balance {t}",
                sum(schedules[e][t]*(1 if r.role=="production" else -1) for e,r in regs.items()))
    for run in runs:
        grouped={}
        for r in run["rows"]:
            grouped.setdefault(r["period"],{})[r["entity"]]=r
        for offset,t in enumerate(sorted(grouped)):
            group=grouped[t]
            a.check("ISP",f"{run['id']} energy {t}",
                sum(r["power_mw"] for r in group.values())+run.get("emergency_generation_mw",[0]*len(grouped))[offset]+run["res_available_mw"][offset]-run.get("res_commercial_curtailment_mw",[0]*len(grouped))[offset]-run["res_curtailment_mw"][offset]+(run["slack_by_period"][offset]["energy_shortage_mwh"]-run["slack_by_period"][offset]["energy_surplus_mwh"])/.5-run["demand_mw"][offset],tolerance=1e-4)
            for p in PRODUCTS:
                delivered=sum(r["reserve_mw"][p] for r in group.values())
                a.check("ISP",f"{run['id']} requirement {t} {p}",max(0,run["requirements_mw"][p][offset]-delivered-run["slack_by_period"][offset]["reserve_shortage_mw"][p]))
            for e,r in group.items():
                u=units[e]
                previous=run["initial_state"][e] if offset==0 else grouped[t-1][e]
                prior_power=previous["end_mw"] if offset==0 else previous["power_mw"]
                a.check("ISP",f"{run['id']} average energy {e} {t}",
                    r["power_mw"]*.5-run["commercial_mwh"][e][offset]-r["up_mwh"]+r["down_mwh"],tolerance=1e-4)
                flags=r.get('phase_binaries')
                if flags is not None:
                    a.check('ISP',f"{run['id']} one-hot phases {e} {t}",ok=sum(flags.values())<=1 and bool(r['on'])==bool(sum(flags.values())))
                    a.check('ISP',f"{run['id']} dispatch-only reserve {e} {t}",max(r['reserve_mw'].values()) if not flags['dispatch'] else 0)
                up=sum(r["reserve_mw"][p] for p in UP)
                down=sum(r["reserve_mw"][p] for p in DOWN)
                a.check("ISP",f"{run['id']} headroom {e} {t}",max(0,r["power_mw"]+up-u.maximum_mw*(r["on"]+r["stop"]),u.minimum_mw*(r["on"]-int(r.get("soaking",False) or r["start"]))+down-r["power_mw"]),tolerance=1e-4)
                a.check("ISP",f"{run['id']} average ramps {e} {t}",
                    max(0,abs(r["power_mw"]-prior_power)-u.ramp_mw_hour*.5),tolerance=1e-4)
                prev_on=previous["on"]
                a.check("ISP",f"{run['id']} transition {e} {t}",r["on"]-prev_on-r["start"]+r["stop"],tolerance=0)
                recent=[grouped[k][e] for k in range(max(min(grouped),t-u.min_up+1),t+1)]
                a.check("ISP",f"{run['id']} minimum up {e} {t}",max(0,sum(x["start"] for x in recent)-r["on"]))
                recent=[grouped[k][e] for k in range(max(min(grouped),t-u.min_down+1),t+1)]
                a.check("ISP",f"{run['id']} minimum down {e} {t}",max(0,sum(x["stop"] for x in recent)-(1-r["on"])))
        for entity,u in units.items():
            init=run['initial_state'][entity]
            phase=init.get('stage','soak' if init['on'] and init['duration_periods']<u.soak_periods else 'dispatch' if init['on'] else 'offline')
            age=init.get('phase_elapsed_minutes',init['duration_periods']*30)
            selected=[r for r in run['rows'] if r['entity']==entity]
            for name,passed in check_rows(selected,phase,age,unit_profiles(u),u.minimum_mw,init["end_mw"]).items():
                a.check('ISP_phases',f"{run['id']} {entity} {name}",ok=passed)
        a.check("ISP_pricing",f"{run['id']} MILP/LP objective",run["milp_objective_eur"]-run["fixed_lp_objective_eur"],tolerance=.001)
        costs=run['objective_components_eur']
        reconstructed=costs['energy_up_cost']-costs['energy_down_credit']+costs['reserve_capacity_cost']+costs['startup_cost']+sum(costs.get(k,0) for k in ('energy_shortage_penalty','energy_surplus_penalty','reserve_shortage_penalty'))
        a.check('ISP_pricing',f"{run['id']} offer objective components",reconstructed-run['fixed_lp_objective_eur'],tolerance=.001)
        for row in run["rows"]:
            offset=row["period"]-run["first_period"]
            for p,price in row["reserve_price_eur_mw_period"].items():
                name=f"requirement_{p}_{offset}"
                if name not in run["lp_reserve_shadow_prices"]:continue
                a.check("ISP_pricing",f"{run['id']} {row['entity']} {name} LP shadow-price settlement",
                    ok=math.isfinite(price) and price>=-1e-6 and abs(price-run["lp_reserve_shadow_prices"][name])<1e-8)
        for d in run["perturbations"]:
            a.check("ISP_pricing",f"{run['id']} {d['constraint']} finite difference",ok=d["inside_dual_range"],details=d)
        for name,(lo,hi) in run["reserve_dual_ranges"].items():
            a.check("ISP_pricing",f"{run['id']} valid range {name}",max(0,-lo,lo-hi))
            price=run["lp_reserve_shadow_prices"][name]
            a.check("ISP_pricing",f"{run['id']} shadow price inside range {name}",ok=lo-2e-6<=price<=hi+2e-6)
    preceding={e:u.initial_mw for e,u in units.items()}
    for rtu in rtus:
        dt=rtu["duration_hours"]
        prediction=rtu.get('system_prediction')
        if prediction is not None:
            i=rtus.index(rtu)
            previous=rtus[i-1]['system_observation'] if i else scenario.get('previous_system_observation')
            a.check('system_observations',f"latest completed measurement {rtu['rtu']}",ok=prediction['measurement_index']==(i-1 if i else None))
            for key in ('demand','res'):
                error=0 if previous is None else previous[key+'_mw']-previous['forecast_'+key+'_mw']
                expected=max(0,prediction['baseline_'+key+'_mw']+error)
                a.check('system_observations',f"causal {key} prediction {rtu['rtu']}",prediction[key+'_prediction_mw']-expected)
            if i:a.check('system_observations',f"measurement available {rtu['rtu']}",ok=datetime.fromisoformat(rtus[i-1]['system_observation_available_at'])<=datetime.fromisoformat(rtu['issued_at']))
        a.check("RTBM",f"prediction balance {rtu['rtu']}",sum(r["instructed_energy_mwh"] for r in rtu["rows"])+rtu.get("emergency_mwh",0)+rtu["res_prediction_mwh"]-rtu.get("res_commercial_curtail_mwh",0)-rtu["res_curtail_mwh"]+rtu["slack"]["energy_shortage_mwh"]-rtu["slack"]["energy_surplus_mwh"]-rtu["demand_prediction_mwh"])
        residual=rtu['actual_balance_residual_mwh'] if rtu.get('delivery_equals_dispatch',False) else 0.
        a.check("delivery",f"actual balance and explicit residual {rtu['rtu']}",sum(r["actual_mwh"] for r in rtu["rows"])+rtu.get("emergency_mwh",0)+rtu["res_actual_mwh"]-rtu["demand_actual_mwh"]+residual)
        if rtu.get('delivery_equals_dispatch',False):
            for row in rtu['rows']:a.check('delivery',f"dispatch equals actual {rtu['rtu']} {row['entity']}",row['actual_mwh']-row['instructed_energy_mwh'])
        for r in rtu["rows"]:
            e=r["entity"]
            u=units[e]
            a.check("RTBM",f"MWh award bridge {rtu['rtu']} {e}",r["instructed_energy_mwh"]-r.get("dispatch_reference_mwh",r["market_schedule_mwh"])-r["up_mwh"]+r["down_mwh"])
            nodes=r["instructed_nodes_mw"]
            integral=r["trajectory_step_hours"]*(.5*nodes[0]+sum(nodes[1:-1])+.5*nodes[-1])
            a.check("RTBM",f"instruction trajectory {rtu['rtu']} {e}",r["instructed_energy_mwh"]-integral)
            a.check("delivery",f"state continuity {rtu['rtu']} {e}",r["initial_mw"] if e in rtu.get("forced_trip_entities",[]) else r["initial_mw"]-preceding[e])
            a.check("delivery",f"physical endpoint {rtu['rtu']} {e}",max(0,-r["actual_terminal_mw"],r["actual_terminal_mw"]-u.maximum_mw),tolerance=1e-4)
            a.check("delivery",f"actual ramp {rtu['rtu']} {e}",max(0,abs(r["actual_terminal_mw"]-r["initial_mw"])-u.ramp_mw_hour*dt),tolerance=1e-4)
            nodes=r["actual_nodes_mw"]
            integral=r["trajectory_step_hours"]*(.5*nodes[0]+sum(nodes[1:-1])+.5*nodes[-1])
            a.check("delivery",f"meter trajectory {rtu['rtu']} {e}",r["actual_mwh"]-integral)
            a.check("delivery",f"subinterval ramps {rtu['rtu']} {e}",
                max(0,max(abs(b-a) for a,b in zip(nodes,nodes[1:]))-u.ramp_mw_hour*r["trajectory_step_hours"]),tolerance=1e-4)
            a.check("delivery",f"physical terminal limits {rtu['rtu']} {e}",
                    max(0,r["actual_terminal_mw"]-u.maximum_mw*r["on"],
                        (0 if r.get("qualifying_startup",False) or r.get("phase")=="desync" else u.minimum_mw*r["on"])-r["actual_terminal_mw"]),tolerance=1e-4)
            average=r["instructed_energy_mwh"]/dt
            a.check("RTBM",f"physical average limits {rtu['rtu']} {e}",max(0,
                average-r.get("operating_capacity_mw",u.maximum_mw*r["on"]),
                r.get("minimum_average_mw",u.minimum_mw*r["on"])-average),tolerance=1e-4)
            preceding[e]=r["actual_terminal_mw"]
    for b in bridges:
        if regs[b["entity"]].role=="production":
            residual=b["MQ"]-b["MSQ"]-b["INSTQ"]-b["UNINSTQ"]
        else:
            residual=b["MQ"]-b["adjusted_MSQ"]-b["INSTQ"]+b["UNINSTQ"]
        a.check("metering_settlement",f"energy bridge {b['entity']} {b['period']}",residual)
    balances=ledger.balances()
    a.check("ledger","double entry",float(sum(balances.values(),Decimal(0))),tolerance=1e-18)
    for account in ("A-A","A-B","A-C","MM","MS"):
        a.check("ledger",f"account recovery {account}",float(balances.get(account,0)),
                tolerance=dam.get("cash_balance_tolerance_eur",1e-6) if account=="A-A" else 1e-15)
    if revision_result is not None:
        a.check("revisions","idempotent replay",float(revision_result["replay_delta_eur"]),tolerance=0)
    a.check("clearing","default loss closure",float(financial["default"]["uncovered_loss_eur"]),tolerance=0)
    a.check("clearing","cash completion",ok=all(i["state"]=="paid" for i in financial["invoices"]))
    return a.report()
