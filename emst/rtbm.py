"""Five-minute energy awards, physical endpoint feasibility and actual delivery."""
import pulp as lp
from .settlement import D
from .optimization import solve,lp_optimal_value
from .operations import priority_curtailment
from .physics import thermal_path
from .relaxation import build as build_slacks,quantities as slack_quantities,used as slack_used
from .phases import unit_profiles,boundary_state,canonical


def _actual_delivery(units,state,isp,instructed,targets,dt,segments,required_energy,failures):
    model=lp.LpProblem("actual_delivery",lp.LpMinimize)
    step=dt/segments;nodes={};energies={};deviations=[]
    for i,u in enumerate(units):
        row=isp[u.id];active=row["on"]
        starting=active and not state[u.id]["on"]
        operating=active or state[u.id]["on"]
        capacity=row.get("available_mw",u.maximum_mw)
        nodes[u.id]=[state[u.id]["end_mw"]]+[lp.LpVariable(f"actual_node_{i}_{j}",0,capacity*operating) for j in range(1,segments+1)]
        energy=lp.LpVariable(f"actual_energy_{i}",0)
        energies[u.id]=energy
        n=nodes[u.id]
        model += energy==step*(.5*n[0]+lp.lpSum(n[1:-1])+.5*n[-1])
        qualified=row["agc"] and active and u.id not in failures
        baseline=targets[u.id]-failures.get(u.id,0)
        if not qualified:model += energy==baseline
        for j in range(1,segments+1):
            model += n[j]-n[j-1] <= u.ramp_mw_hour*step
            model += n[j-1]-n[j] <= u.ramp_mw_hour*step
            model += n[j] <= capacity*operating
            minimum=u.minimum_mw*active*(not starting)*(not row.get("soaking",False))
            model += n[j] >= minimum
            v=lp.LpVariable(f"actual_deviation_{i}_{j}",0)
            model += v>=n[j]-instructed[u.id][j]
            model += v>=instructed[u.id][j]-n[j]
            deviations.append(v)
        if not active:model += n[-1]==0
        elif not row.get("soaking",False):model += n[-1]>=u.minimum_mw
    model += lp.lpSum(energies.values())==required_energy
    model += lp.lpSum(deviations)
    solve(model,False)
    values={e:v.value() for e,v in energies.items()}
    trajectories={e:[float(lp.value(v)) for v in n] for e,n in nodes.items()}
    agc={u.id:values[u.id]-(targets[u.id]-failures.get(u.id,0)) for u in units}
    return values,trajectories,agc


def clear_rtu(interval, units, state, isp_rows, commercial_mwh, demand_mw, res_mw,
              demand_actual_mw, res_actual_mw, non_delivery_mwh=None,emergency_resources=None,emergency_price=None,res_portfolios=None,seed=0,high_res_reference_price=None,delivery_equals_dispatch=False,slack_parameters=None,res_energy_offers=None,reserve_requirements=None):
    dt = interval.hours
    m = lp.LpProblem(f"RTBM_{interval.id.replace(':','_')}",lp.LpMinimize)
    energy, endpoint, up, down, trajectory = {},{},{},{},{}
    segments=10
    step=dt/segments
    smooth=[]
    # reserve_requirements is a retired compatibility argument, never an RTBM constraint.
    short,surplus,reserve_shortage,slack_costs=build_slacks([interval],slack_parameters,include_capacity=False)
    emergency={}
    for i,resource in enumerate(emergency_resources or []):
        if resource["available_mw"]<0:raise ValueError("Invalid emergency capability")
        emergency[resource["id"]]=lp.LpVariable(f"contingency_energy_{i}",0,resource["available_mw"]*dt)
    for i,u in enumerate(units):
        row = isp_rows[u.id]
        active = row["on"]
        starting=bool(active and not state[u.id]["on"])
        stopping=bool(not active and state[u.id]["on"])
        operating=int(bool(active or stopping))
        capacity=row.get("available_mw",u.maximum_mw)
        energy[u.id] = lp.LpVariable(f"energy_{i}",0,u.maximum_mw*dt)
        minimum = u.minimum_mw*active*(not starting)*(not row.get("soaking",False))
        trajectory[u.id], integrated = thermal_path(m,f'unit_{i}',state[u.id]['end_mw'],dt,segments,
            capacity,operating,minimum,u.ramp_mw_hour)
        endpoint[u.id]=trajectory[u.id][-1]
        up[u.id] = lp.LpVariable(f"up_MWh_{i}",0)
        down[u.id] = lp.LpVariable(f"down_MWh_{i}",0)
        reference=0. if capacity==0 else commercial_mwh[u.id]
        m += energy[u.id]-reference == up[u.id]-down[u.id]
        prior = state[u.id]["end_mw"]
        nodes=trajectory[u.id]
        m += energy[u.id] == integrated
        for j in range(1,segments+1):
            variation=lp.LpVariable(f"variation_{i}_{j}",0)
            m += variation >= nodes[j]-nodes[j-1]
            m += variation >= nodes[j-1]-nodes[j]
            smooth.append(.01*variation)
            if row.get("soaking",False):
                remaining=(30-interval.local.minute%30)/5
                profile=unit_profiles(u)[row.get('phase','soak')]
                index=int(row.get('phase_elapsed_before_minutes',0)//30)
                terminal=profile[index] if profile else row.get('phase_terminal_mw',u.minimum_mw)
                target=state[u.id]['end_mw']+(terminal-state[u.id]['end_mw'])*(j/segments)/remaining
                m += nodes[j]==target
        terminal_deviation=lp.LpVariable(f"terminal_deviation_{i}",0)
        m += terminal_deviation >= endpoint[u.id]-energy[u.id]/dt
        m += terminal_deviation >= energy[u.id]/dt-endpoint[u.id]
        smooth.append(terminal_deviation)
        if stopping:
            m += endpoint[u.id]==0
        elif not row.get("soaking",False):
            m += endpoint[u.id]>=u.minimum_mw*active
        m += energy[u.id]/dt >= minimum
        m += energy[u.id]/dt <= capacity*operating
        was = state[u.id]["on"]
        ramp_up = u.startup_limit_mw if active and not was else u.ramp_mw_hour*dt
        ramp_down = u.shutdown_limit_mw if was and not active else u.ramp_mw_hour*dt
        m += endpoint[u.id]-prior <= ramp_up
        m += prior-endpoint[u.id] <= ramp_down
    for resource in emergency_resources or []:
        if resource.get("kind")=="above_registered":
            host=resource["host_entity"]
            unit=next(u for u in units if u.id==host)
            if abs(state[host]["end_mw"]-unit.maximum_mw)>1e-6:
                raise ValueError("Above-capacity eligibility requires an already fully loaded host")
            m += energy[host]==unit.maximum_mw*dt
            maximum_endpoint=min(resource["available_mw"],resource["extra_ramp_mw_hour"]*dt)
            m += emergency[resource["id"]] <= .5*maximum_endpoint*dt
    commercial_curt=0.;market_down={}
    if res_energy_offers is None:
        curtail_cap=res_mw*dt if res_portfolios is None else sum(
            p['available_mwh']-p['technical_minimum_mwh'] for p in res_portfolios
            if p['category'] in (2,3) and not p['zero_injection_self_consumption'])
        curtail=lp.LpVariable('res_curtail_mwh',0,curtail_cap)
        res_terms=curtail*.001
    else:
        for i,offer in enumerate(res_energy_offers):
            baseline=min(offer['available_mwh'],offer['commercial_mwh'])
            commercial_curt+=offer['available_mwh']-baseline
            market_down[offer['id']]=lp.LpVariable(f'RES_down_mwh_{i}',0,baseline)
        curtail=lp.lpSum(market_down.values())
        res_terms=lp.lpSum(-o['down_price']*market_down[o['id']] for o in res_energy_offers)
    m += lp.lpSum(energy.values())+lp.lpSum(emergency.values())+res_mw*dt-commercial_curt-curtail+short[0]-surplus[0] == demand_mw*dt,'energy_balance_MWh'
    m += lp.lpSum((u.energy_eur_mwh+5)*up[u.id]-(u.energy_eur_mwh-5)*down[u.id] for u in units)+res_terms+lp.lpSum(slack_costs.values())+lp.lpSum(emergency[r['id']]*r['contract_rate'] for r in (emergency_resources or []))
    objective = solve(m,False)
    objective = lp_optimal_value(m)
    marginal = m.constraints["energy_balance_MWh"].pi
    primary=m.objective
    # CBC's text exports round primal/dual values. Preserve the first objective
    # within 0.00001 EUR there, rather than impose a falsely exact rounded face.
    primary_tolerance=1e-5 if isinstance(m.solver,lp.PULP_CBC_CMD) else 0.
    if primary_tolerance:
        m += primary <= objective+primary_tolerance,"primary_optimum_upper"
        m += primary >= objective-primary_tolerance,"primary_optimum_lower"
    else:
        m += primary == objective,"primary_optimum"
    m.setObjective(lp.lpSum(smooth))
    solve(m,False)
    emergency_rows=[{**r,"delivered_mwh":emergency[r["id"]].value(),
                     "ordinary_balancing_excluded":True} for r in (emergency_resources or [])]
    for r in emergency_rows:
        if r.get("kind")=="above_registered":
            r.update(extra_initial_mw=0,extra_terminal_mw=2*r["delivered_mwh"]/dt,
                     trajectory="linear_ramp_energy_integral")
    emergency_total=sum(r["delivered_mwh"] for r in emergency_rows)
    pools=res_portfolios or [dict(id="res",category=3,available_mwh=res_mw*dt,
                                  technical_minimum_mwh=0,zero_injection_self_consumption=False)]
    curtailment=priority_curtailment(pools,lp.value(curtail),seed) if res_energy_offers is None and lp.value(curtail)>1e-8 else []
    slack_snapshot=slack_quantities(0,short,surplus,reserve_shortage)
    if slack_snapshot['energy_shortage_mwh']>1e-6 or slack_snapshot['energy_surplus_mwh']>1e-6:
        pass  # Preserve the energy LP marginal at the configured slack penalty.
    elif emergency_total>1e-8:
        if emergency_price is None:raise ValueError("Emergency regime requires a dated administrative price")
        marginal=emergency_price
    elif curtailment:
        if high_res_reference_price is None:
            raise ValueError("Non-price-forming RES control requires its historical reference price")
        marginal=high_res_reference_price
    # Simulated forecast error and instructed non-delivery are separately metered.
    delivery = {u.id:energy[u.id].value() for u in units}
    instructed_nodes={u.id:[float(lp.value(v)) for v in trajectory[u.id]] for u in units}
    failures = non_delivery_mwh or {}
    market_rows=[];market_instructions=[];entity_down={};actual_commercial_curt=0.
    actual_res=max(0,res_actual_mw*dt-lp.value(curtail))
    if res_energy_offers is not None:
        forecast_pool=sum(o['available_mwh'] for o in res_energy_offers)
        exogenous=max(0,res_mw*dt-forecast_pool)
        actual_pool=max(0,res_actual_mw*dt-exogenous)
        delivered_total=0.
        for o in res_energy_offers:
            share=o['available_mwh']/forecast_pool if forecast_pool>1e-12 else 1/max(1,len(res_energy_offers))
            available=actual_pool*share;baseline=min(available,o['commercial_mwh'])
            actual_commercial_curt+=available-baseline
            awarded=market_down[o['id']].value()
            if awarded < -1e-5:raise RuntimeError('RES downward award violates nonnegative bound')
            awarded=max(0.,awarded)  # CBC text-solution precision may report sub-micro-MWh negatives.
            delivered=min(awarded,baseline)
            delivered_total+=delivered;entity_down[o['id']]=delivered
            market_rows.append(dict(entity=o['id'],up_mwh=0.,down_mwh=awarded,
                up_offer_eur_mwh=0.,down_offer_eur_mwh=o['down_price'],price_forming=True,reason='RES_down_offer'))
            if awarded>1e-7:market_instructions.append(dict(entity=o['id'],down_mwh=awarded,delivered_mwh=delivered,
                price_forming=True,external_control=False,systematic_penalty_exempt=False,reason='RES_down_offer'))
        actual_res=max(0,res_actual_mw*dt-actual_commercial_curt-delivered_total)
    if delivery_equals_dispatch:
        if failures:raise ValueError('Delivery equals dispatch mode does not accept non-delivery events')
        actual_nodes={e:list(v) for e,v in instructed_nodes.items()};regulation={e:0. for e in delivery}
    else:
        delivery,actual_nodes,regulation=_actual_delivery(units,state,isp_rows,instructed_nodes,delivery,
            dt,segments,demand_actual_mw*dt-actual_res-emergency_total,failures)
    residual = demand_actual_mw*dt-actual_res-sum(delivery.values())-emergency_total
    if abs(residual)>1e-5 and not delivery_equals_dispatch:
        raise RuntimeError(f"Unserved actual demand in {interval.id}: {residual} MWh")
    rows = []
    for u in units:
        old = state[u.id]
        end = actual_nodes[u.id][-1]
        active = isp_rows[u.id]["on"]
        duration = old["duration_rtus"]+1 if active==old["on"] else 1
        phase=isp_rows[u.id].get('phase','dispatch' if active else 'offline')
        old_phase=old.get('stage','dispatch' if old['on'] else 'offline')
        elapsed=old.get('phase_elapsed_minutes',old['duration_rtus']*5)+5 if canonical(old_phase)==phase else 5
        next_phase,next_age=boundary_state(phase,elapsed,unit_profiles(u))
        next_on=int(next_phase!='offline')
        state[u.id] = {'on':next_on,'end_mw':end,'duration_rtus':duration if next_on==active else 0,
                      'stage':next_phase,'phase_elapsed_minutes':next_age}
        rows.append({"entity":u.id,"rtu":interval.id,"period":interval.period,
            "market_schedule_mwh":commercial_mwh[u.id],"up_mwh":up[u.id].value(),
            "dispatch_reference_mwh":0. if isp_rows[u.id].get("available_mw",u.maximum_mw)==0 else commercial_mwh[u.id],
            "forced_unavailable":isp_rows[u.id].get("available_mw",u.maximum_mw)==0,
            "down_mwh":down[u.id].value(),"instructed_energy_mwh":energy[u.id].value(),
            "agc_energy_mwh":regulation[u.id],"actual_mwh":delivery[u.id],
            "initial_mw":old["end_mw"],"instructed_terminal_mw":endpoint[u.id].value(),
            "actual_terminal_mw":end,"on":active,"agc":isp_rows[u.id]["agc"],
            "phase":phase,"phase_binaries":isp_rows[u.id].get("phase_binaries",{}),
            "price_eur_mwh":marginal,"up_offer_eur_mwh":u.energy_eur_mwh+5,
            "down_offer_eur_mwh":u.energy_eur_mwh-5,
            "price_forming":True,"reason":"ordinary_market",
            "non_delivery_mwh":failures.get(u.id,0)})
        rows[-1]["instructed_nodes_mw"]=instructed_nodes[u.id]
        rows[-1]["actual_nodes_mw"]=actual_nodes[u.id]
        rows[-1]["trajectory_step_hours"]=step
        rows[-1]["qualifying_startup"]=bool(phase in ("sync","soak") or (active and not old["on"]))
        rows[-1]["operating_capacity_mw"]=isp_rows[u.id].get("available_mw",u.maximum_mw)*int(active or old["on"])
        rows[-1]["minimum_average_mw"]=u.minimum_mw*active*int(not isp_rows[u.id].get("soaking",False) and not rows[-1]["qualifying_startup"])
        if curtailment:
            rows[-1].update(price_forming=False,reason="system_RES_priority_control")
    return {"rtu":interval.id,"period":interval.period,"issued_at":interval.start.isoformat(),
        "effective_at":interval.start.isoformat(),"duration_hours":dt,"status":"Optimal","capacity_products":[],
        "objective_eur":objective,"primary_objective_tolerance_eur":primary_tolerance,"price_eur_mwh":marginal,"rows":rows,
        "demand_prediction_mwh":demand_mw*dt,"demand_actual_mwh":demand_actual_mw*dt,
        "res_prediction_mwh":res_mw*dt,"res_actual_mwh":actual_res,
        "res_commercial_curtail_mwh":commercial_curt,"res_offer_rows":market_rows,"entity_down":entity_down,
        "slack":slack_snapshot,"physical_security_feasible":not slack_used([slack_snapshot]),
        "res_curtail_mwh":sum(r["down_mwh"] for r in market_rows) if res_energy_offers is not None else float(lp.value(curtail)),"actual_balance_residual_mwh":residual,
        "emergency_rows":emergency_rows,"emergency_mwh":emergency_total,
        "regime":"relaxed_with_slack" if slack_used([slack_snapshot]) else "emergency" if emergency_total>1e-8 else "high_RES_control" if curtailment else "normal",
        "RES_instructions":curtailment+market_instructions,"delivery_equals_dispatch":delivery_equals_dispatch}
