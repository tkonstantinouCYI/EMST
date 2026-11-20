"""Public synthetic ISP benchmark. No private data files are read."""
import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import itertools
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

import highspy
from emst import Market, ThermalState, PlantState
from emst.calendar import day_intervals
from emst.configurations import boundary
import emst.dispatch as dispatch_module

REFERENCE_REVISION = 'aff3574f50915f3bc25ad21cbdd456628cdf1b20'
DAY = '2026-02-03'  # Arbitrary non-DST synthetic date.
DT = 0.5
DEMAND_HOURLY = [910,880,850,840,860,920,1020,1130,1250,1350,1450,1510,
                 1540,1530,1510,1490,1470,1450,1430,1390,1310,1200,1080,980]
SOLAR_HOURLY = [0,0,0,0,0,5,45,150,350,600,850,1050,
               1130,1080,920,690,430,200,55,0,0,0,0,0]
WIND_HOURLY = [70,68,65,62,60,58,55,50,45,40,35,35,
              40,50,60,70,80,90,95,100,100,95,85,75]

# id, maximum MW, minimum MW, ramp MW/hour, min-up minutes,
# min-down minutes, startup minutes, marginal EUR/MWh, startup EUR.
# Every number below is a synthetic benchmark assumption.
ORDINARY = [
 ('T01',44,6,260,0,180,30,240,420),
 ('T02',44,6,260,0,180,30,242,430),
 ('T03',44,6,260,0,180,30,244,440),
 ('T04',44,6,260,0,180,30,246,450),
 ('T05',44,6,260,0,180,30,248,460),
 ('T06',22,16,50,60,120,60,175,140),
 ('T07',22,16,50,60,120,60,176,145),
 ('T08',22,16,50,60,120,60,177,150),
 ('T09',22,16,65,60,120,30,178,155),
 ('T10',22,16,65,60,120,30,179,160),
 ('T11',22,16,65,60,120,30,180,165),
 ('T12',140,70,320,480,360,630,155,1000),
 ('T13',140,70,320,480,360,630,156,1050),
 ('T14',140,70,320,480,360,630,157,1100),
 ('T15',24,8,550,0,0,30,100,130),
 ('T16',24,8,550,0,0,30,101,135),
 ('T17',24,8,550,0,0,30,102,140),
 ('T18',24,8,550,0,0,30,103,145),
 ('T19',24,8,550,0,0,30,104,150),
 ('T20',24,8,550,0,0,30,106,155),
]
# id, GT contributions, maximum/contribution, minimum/contribution,
# ramp/contribution, marginal EUR/MWh, incremental start EUR/contribution.
PLANTS = [
 ('C01',2,125,80,400,110,9500),
 ('C02',2,125,80,400,114,9200),
 ('C03',2,95,48,340,125,7000),
 ('C04',3,100,55,330,105,7800),
]
MANDATORY = {'T06','T07','T12','T13','T14'}

def half(hourly):
    assert len(hourly)==24
    return [float(v) for v in hourly for _ in range(2)]

def plant_data(row):
    name,n,maximum,minimum,ramp,cost,start_cost = row
    components = {f'G{i+1}':dict(minimum_up_minutes=360,minimum_down_minutes=360) for i in range(n)}
    components['S'] = dict(minimum_up_minutes=360,minimum_down_minutes=360)
    subsets = [s for k in range(n+1) for s in itertools.combinations(range(n),k)]
    def label(s):
        return '_'.join(f'G{i+1}' for i in s)+'_S' if s else 'OFF'
    configurations = {}
    for s in subsets:
        configurations[label(s)] = dict(
            components=[f'G{i+1}' for i in s]+(['S'] if s else []),
            minimum_mw=minimum*len(s),maximum_mw=maximum*len(s),
            marginal_cost=cost,no_load_cost_per_hour=0,
            ramp_mw_hour=ramp*len(s) if s else 1,
            reserve_limit_mw=0.30*maximum*len(s))
    transitions = []
    for a in subsets:
        for b in subsets:
            if len(set(a)^set(b))!=1:
                continue
            adding = len(b)>len(a)
            steps = 13 if adding else 1
            pa,pb = minimum*len(a),minimum*len(b)
            transitions.append(dict(
                id=label(a)+'_to_'+label(b),source=label(a),target=label(b),
                profile_mw=[pa+(pb-pa)*(k+1)/steps for k in range(steps)],
                components_by_step=[list(configurations[label(b)]['components']) for _ in range(steps)],
                cost_eur=start_cost if adding else 0,
                ramp_mw_hour=max(configurations[label(a)]['ramp_mw_hour'],configurations[label(b)]['ramp_mw_hour'])))
    return components,configurations,transitions,label(tuple(range(n)))

def build_inputs():
    m = Market()
    load,solar,wind = map(half,(DEMAND_HOURLY,SOLAR_HOURLY,WIND_HOURLY))
    # All four plants start in their full configuration; mandatory ordinary units
    # start in dispatch. All other ordinary units start offline, with 24h histories.
    full_min = sum(r[3]*r[1] for r in PLANTS)+sum(r[2] for r in ORDINARY if r[0] in MANDATORY)
    full_max = sum(r[2]*r[1] for r in PLANTS)+sum(r[1] for r in ORDINARY if r[0] in MANDATORY)
    target = max(full_min,load[0]-solar[0]-wind[0])
    assert target<=full_max
    def initial_power(low,high):
        return low+(target-full_min)*(high-low)/(full_max-full_min)
    for name,maximum,minimum,ramp,up,down,start,cost,start_cost in ORDINARY:
        m.add_participant('P_'+name)
        online = name in MANDATORY
        m.add_thermal(name,'P_'+name,capacity_mw=maximum,minimum_mw=minimum,
            ramp_mw_hour=ramp,minimum_up_minutes=up,minimum_down_minutes=down,
            startup_minutes=start,startup_cost=start_cost,marginal_cost=cost,
            no_load_cost_per_hour=0,reserve_cost=0,reserve_limit_mw=0.30*maximum,
            initial_state=ThermalState('dispatch' if online else 'offline',initial_power(minimum,maximum) if online else 0,1440),
            strategy='marginal_cost')
    for row in PLANTS:
        name,n,maximum,minimum,ramp,cost,start_cost = row
        components,configurations,transitions,full = plant_data(row)
        m.add_participant('P_'+name)
        m.add_configured_thermal(name,'P_'+name,components=components,
            configurations=configurations,transitions=transitions,
            initial_state=PlantState(full,initial_power(n*minimum,n*maximum),
                {c:dict(on=True,duration_minutes=1440) for c in components}),
            reserve_cost=0,strategy='marginal_cost')
    for name,profile,cap in [('R01',solar,1350),('R02',wind,220)]:
        m.add_participant('P_'+name)
        m.add_res(name,'P_'+name,generation_forecast=profile,
            actual_generation=profile,actual_generation_kind='metered',capacity_mw=cap)

    assets = list(m.assets.values())
    ordinary = {r[0]:r for r in ORDINARY}
    thermal = [a for a in assets if a.technology=='thermal']
    # Explicit synthetic commercial schedules; no DAM, fuel workbook, supplier
    # allocation, must-run auction or settlement reconstruction is needed.
    commercial = {a.id:[0.]*48 for a in assets}
    for t in range(48):
        must = sum(ordinary[u][2] for u in MANDATORY)
        res_total = solar[t]+wind[t]
        accepted_res = min(res_total,max(0.,load[t]-must))
        for name,values in [('R01',solar),('R02',wind)]:
            commercial[name][t] = DT*accepted_res*values[t]/res_total if res_total else 0.
        remaining = load[t]-accepted_res
        for name in sorted(MANDATORY):
            commercial[name][t] = DT*ordinary[name][2]
            remaining -= ordinary[name][2]
        for a in sorted(thermal,key=lambda a:(a.parameters['marginal_cost'],a.id)):
            headroom = a.parameters['capacity_mw']-commercial[a.id][t]/DT
            take = min(max(0.,remaining),headroom)
            commercial[a.id][t] += DT*take
            remaining -= take
        assert abs(remaining)<1e-7
        assert abs(sum(v[t] for v in commercial.values())-DT*load[t])<1e-7

    requirements = {
        'FCR_up':[45.]*48,'FCR_down':[28.]*48,
        'aFRR_up':[12.]*48,'aFRR_down':[0.]*48,
        'mFRR_up':[0.45*w+0.18*s for w,s in zip(wind,solar)],
        'mFRR_down':[0.]*48,'RR_up':[0.]*48,
    }
    states = {}
    for a in thermal:
        s = a.initial_state
        states[a.id] = boundary(s) if isinstance(s,PlantState) else dict(
            stage=s.stage,on=int(s.stage!='offline'),power_mw=s.power_mw,duration_minutes=s.time_in_stage_minutes)
    periods = day_intervals(date.fromisoformat(DAY),30)
    mandatory = {a.id:[int(a.id in MANDATORY)]*48 for a in thermal}
    rules = m.ruleset.to_dict()
    rules['slack'] = dict(enabled=True,energy_shortage_eur_mwh=10000.,energy_surplus_eur_mwh=10000.,
        reserve_shortage_eur_mw_mtu={p:10000. for p in requirements})
    # Expanded JSON records every input consumed by the dispatch builder.
    snapshot = dict(schema_version=1,synthetic=True,reference_revision=REFERENCE_REVISION,
        day=DAY,interval_minutes=30,interval_starts=[p.start.isoformat() for p in periods],
        assets=[dict(id=a.id,participant=a.participant,technology=a.technology,
                     parameters=a.parameters,initial_state=asdict(a.initial_state) if a.initial_state is not None else None) for a in assets],
        states=states,demand_forecast_mw=load,res_forecast_mw=[s+w for s,w in zip(solar,wind)],
        individual_res_forecasts_mw={'R01':solar,'R02':wind},commercial_mwh=commercial,
        reserve_requirements_mw=requirements,mandatory_on=mandatory,rules=rules,
        rtbm=False,price_ranges=False,storage_terminal_soc=None,
        reference=None,commitment=None,terminal_time=None,projection=False,immediate_ramping=False)
    kwargs = dict(assets=assets,states=states,intervals=periods,demand=load,
        res=snapshot['res_forecast_mw'],commercial=commercial,requirements=requirements,rules=rules,
        mandatory_on=mandatory,res_forecasts=snapshot['individual_res_forecasts_mw'],price_ranges=False)
    return kwargs,snapshot

def export_model(out):
    started = time.perf_counter()
    inputs,snapshot = build_inputs()
    (out/'inputs.json').write_text(json.dumps(snapshot,indent=2,allow_nan=False),encoding='utf-8')
    class Captured(Exception):
        pass
    stats = {}
    def capture(problem,mip=True):
        assert mip and problem.name=='library_ISP'
        stats.update(variables=len(problem.variables()),binaries=sum(v.isBinary() for v in problem.variables()),
            constraints=len(problem.constraints),build_seconds=time.perf_counter()-started)
        problem.writeMPS(str(out/'initial_ISP.mps'))
        raise Captured()
    with patch.object(dispatch_module,'solve',side_effect=capture):
        try:
            dispatch_module.dispatch(**inputs)
        except Captured:
            pass
    assert stats, 'No initial ISP captured; check library compatibility.'
    stats.update(mps_sha256=hashlib.sha256((out/'initial_ISP.mps').read_bytes()).hexdigest(),
        inputs_sha256=hashlib.sha256((out/'inputs.json').read_bytes()).hexdigest(),
        export_completed_seconds=time.perf_counter()-started)
    (out/'model_size.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
    return stats

def solve_model(out,seconds,threads,presolve):
    h = highspy.Highs()
    for key,value in dict(threads=threads,time_limit=seconds,presolve=presolve,mip_rel_gap=0.,mip_abs_gap=0.,
                          log_file=str((out/'solver.log').resolve()),log_to_console=False,random_seed=0).items():
        assert h.setOptionValue(key,value)==highspy.HighsStatus.kOk, key
    started = time.perf_counter()
    assert h.readModel(str(out/'initial_ISP.mps'))==highspy.HighsStatus.kOk
    read_seconds = time.perf_counter()-started
    started = time.perf_counter();h.run();wall = time.perf_counter()-started
    info = h.getInfo();solution = h.getSolution()
    def finite(v):
        import math
        return float(v) if math.isfinite(v) else None
    incumbent = info.primal_solution_status==2 and solution.value_valid
    result = dict(backend='HiGHS',version=h.version(),threads=threads,requested_seconds=seconds,
        presolve=presolve,relative_gap_target=0,absolute_gap_target=0,random_seed=0,
        read_seconds=read_seconds,solve_wall_seconds=wall,status=str(h.getModelStatus()),
        incumbent_available=incumbent,objective=finite(info.objective_function_value) if incumbent else None,
        best_bound=finite(info.mip_dual_bound),gap=finite(info.mip_gap),nodes=info.mip_node_count,
        lp_iterations=info.simplex_iteration_count,
        max_primal_infeasibility=finite(info.max_primal_infeasibility),
        max_integrality_violation=finite(info.max_integrality_violation),
        proven_optimal=h.getModelStatus()==highspy.HighsModelStatus.kOptimal)
    if incumbent:
        h.writeSolution(str(out/'solution.txt'),0)
        result['primal_validated'] = info.max_primal_infeasibility<=1e-5 and info.max_integrality_violation<=1e-5
    (out/'solve_result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=120)
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--presolve',choices=['choose','on','off'],default='choose')
    p.add_argument('--build-only',action='store_true')
    args = p.parse_args()
    if args.seconds<=0 or args.threads<=0:
        p.error('Positive time limit and thread count required.')
    args.output.mkdir(parents=True,exist_ok=False)
    stats = export_model(args.output)
    print(json.dumps(dict(model=stats)),flush=True)
    if not args.build_only:
        print(json.dumps(dict(solve=solve_model(args.output,args.seconds,args.threads,args.presolve))),flush=True)

if __name__=='__main__':
    main()
