"""Single-zone commitment and product-specific reserves, then fixed-discrete LP."""
from dataclasses import asdict
import pulp as lp
from .model import PRODUCTS, UP, DOWN, RESPONSE_HOURS
from .phases import build as build_phases,bind_average_power,row_phase,unit_profiles,canonical
from .relaxation import build as build_slacks,quantities as slack_quantities,used as slack_used,configuration as slack_configuration
from .optimization import solve, freeze_discrete, dual_face, perturbation


def schedule(units, periods, demand_mw, res_mw, commercial_mwh, requirements,
             initial_state, cutoff, run_id, price_ranges=False, requirement_bump=None,availability_mw=None,emergency_resources=None,reserve_shortage_cap=None,rr_awards=None,rr_prices=None,mandatory_on=None,slack_parameters=None,res_energy_offers=None):
    if not periods:
        raise ValueError("Empty ISP horizon")
    count = len(periods)
    if any(len(v) != count for v in (demand_mw,res_mw)):
        raise ValueError("ISP forecast horizon mismatch")
    if set(requirements) != set(PRODUCTS) or any(len(v)!=count for v in requirements.values()):
        raise ValueError("Product-specific reserve requirements are mandatory")
    if len({u.participant for u in units})!=len(units):raise ValueError('Each thermal unit requires a different participant')
    for unit in units:
        unit.validate()
    if rr_awards is not None and (rr_prices is None or len(rr_prices)!=count):
        raise ValueError("Dated monthly RR quotes are required with fixed awards")
    m = lp.LpProblem(run_id,lp.LpMinimize)
    power, on, start, stop, agc, up, down, reserve = {},{},{},{},{},{},{},{}
    age,hot,warm,cold={},{},{},{}
    phase_data={}
    for i,u in enumerate(units):
        init=initial_state[u.id];profile=unit_profiles(u)
        phase=init.get('stage','soak' if init['on'] and init['duration_periods']<u.soak_periods else 'dispatch' if init['on'] else 'offline')
        elapsed=init.get('phase_elapsed_minutes',init['duration_periods']*30)
        phase_data[u.id]=build_phases(m,f'phase_{i}',count,phase,elapsed,profile,u.min_up*30,u.min_down*30)
    for i,u in enumerate(units):
        for j,t in enumerate(periods):
            key = (u.id,j)
            data=phase_data[u.id]
            on[key]=data['on'][j];start[key]=data['start'][j];stop[key]=data['stop'][j]
            agc[key]=data['flags']['dispatch',j]
            up[key]=lp.LpVariable(f'up_mwh_{i}_{j}',0);down[key]=lp.LpVariable(f'down_mwh_{i}_{j}',0)
            power[key]=(commercial_mwh[u.id][j]+up[key]-down[key])/t.hours
            m+=power[key]>=0
            m+=power[key]<=u.maximum_mw*on[key]
            age[key]=lp.LpVariable(f"offline_hours_{i}_{j}",0)
            for store,label in ((hot,"hot"),(warm,"warm"),(cold,"cold")):
                store[key]=lp.LpVariable(f"{label}_start_{i}_{j}",cat="Binary")
            for product in PRODUCTS:
                reserve[u.id,j,product] = lp.LpVariable(f"r_{i}_{j}_{product}",0,u.reserve_limit_mw)
    curtailed = {j:lp.LpVariable(f"res_curtail_{j}",0,res_mw[j]) for j in range(count)}
    slack_config=slack_configuration(slack_parameters)
    if reserve_shortage_cap is not None:slack_config['reserve_shortage_eur_mw_mtu']={p:reserve_shortage_cap for p in PRODUCTS}
    energy_shortage,energy_surplus,shortage,slack_costs=build_slacks(periods,slack_config)
    terms=list(slack_costs.values());res_down={};res_baseline={};commercial_curt=[0.]*count
    if res_energy_offers is not None:
        for offer in res_energy_offers:
            for j,t in enumerate(periods):
                available=offer['forecast_mw'][j]
                base=min(available,offer['commercial_mwh'][j]/t.hours)
                res_baseline[offer['id'],j]=base
                commercial_curt[j]+=available-base
                res_down[offer['id'],j]=lp.LpVariable(f"RES_down_{offer['id']}_{j}",0,base*t.hours)
                terms.append(-offer['down_price']*res_down[offer['id'],j])
        curtailed={j:lp.lpSum(res_down[o['id'],j] for o in res_energy_offers)/periods[j].hours for j in range(count)}
    contingency={}
    for r in emergency_resources or []:
        for j,t in enumerate(periods):
            bound=r["availability_mw"][j]
            contingency[r["id"],j]=lp.LpVariable(f"contract_{r['id']}_{j}",0,bound)
            terms.append(contingency[r["id"],j]*r["contract_rate"]*t.hours)
    for u in units:
        init = initial_state[u.id]
        for j,t in enumerate(periods):
            key = (u.id,j)
            dt = t.hours
            prior_on = init["on"] if j==0 else on[u.id,j-1]
            prior_power = init["end_mw"] if j==0 else power[u.id,j-1]
            available=u.maximum_mw if availability_mw is None else availability_mw[u.id][j]
            if not 0<=available<=u.maximum_mw:raise ValueError("Invalid declared availability")
            m += on[key] <= int(available>=u.minimum_mw and available>0)
            if mandatory_on is not None:m += on[key]>=mandatory_on[u.id][j]
            m += power[key] <= available*(on[key]+stop[key])
            m += on[key]-prior_on == start[key]-stop[key]
            m += start[key]+stop[key] <= 1
            soaking=on[key]-agc[key]
            inherited_soak=0
            big_age=100000.
            previous_age=(0 if init["on"] else init["duration_periods"]*dt) if j==0 else age[u.id,j-1]
            m += age[key] <= big_age*(1-on[key])
            m += age[key] >= previous_age+dt-big_age*on[key]
            m += age[key] <= previous_age+dt+big_age*on[key]
            m += hot[key]+warm[key]+cold[key]==start[key]
            m += previous_age <= u.hot_start_hours+big_age*(1-hot[key])
            m += previous_age >= u.hot_start_hours-big_age*(1-warm[key])
            m += previous_age <= u.warm_start_hours+big_age*(1-warm[key])
            m += previous_age >= u.warm_start_hours-big_age*(1-cold[key])
            m += power[key] >= u.minimum_mw*agc[key]
            bind_average_power(m,phase_data[u.id],j,power[key],u.maximum_mw,u.minimum_mw,init["end_mw"])
            m += power[key]-prior_power <= u.ramp_mw_hour*dt
            m += prior_power-power[key] <= u.ramp_mw_hour*dt
            m += lp.lpSum(start[u.id,k] for k in range(max(0,j-u.min_up+1),j+1)) <= on[key]
            m += lp.lpSum(stop[u.id,k] for k in range(max(0,j-u.min_down+1),j+1)) <= 1-on[key]
            residual = max(0,(u.min_up if init["on"] else u.min_down)-init["duration_periods"])
            if j < residual:
                m += on[key] == init["on"]
            m += power[key] >= u.agc_min_mw*agc[key]
            m += power[key] <= u.agc_max_mw + u.maximum_mw*(1-agc[key])
            r_up = lp.lpSum(reserve[u.id,j,p] for p in UP)
            r_down = lp.lpSum(reserve[u.id,j,p] for p in DOWN)
            m += power[key]+r_up <= u.maximum_mw*(on[key]+stop[key])
            m += power[key]+r_up <= available*(on[key]+stop[key])
            m += power[key]-r_down >= u.minimum_mw*(on[key]-soaking-inherited_soak)
            for p in PRODUCTS:
                r = reserve[u.id,j,p]
                m += r <= u.reserve_limit_mw*agc[key]
                if p=="RR_up" and rr_awards is not None:
                    m += r==rr_awards[u.id][j]
                if p.startswith("aFRR"):
                    m += r <= u.reserve_limit_mw*agc[key]
                response = RESPONSE_HOURS[p]
                m += r <= u.ramp_mw_hour*response
                terms.append(r*(rr_prices[j] if p=="RR_up" and rr_awards is not None else u.reserve_eur_mw_period[p]))
            market = commercial_mwh[u.id][j]
            offer_up=u.energy_eur_mwh if u.energy_up_eur_mwh is None else u.energy_up_eur_mwh
            offer_down=u.energy_eur_mwh if u.energy_down_eur_mwh is None else u.energy_down_eur_mwh
            terms += [offer_up*up[key]-offer_down*down[key],
                      u.startup_eur*(hot[key]+u.warm_start_multiplier*warm[key]+u.cold_start_multiplier*cold[key])]
    for j,t in enumerate(periods):
        m += lp.lpSum(power[u.id,j] for u in units)+lp.lpSum(contingency[r["id"],j] for r in emergency_resources or [])+res_mw[j]-commercial_curt[j]-curtailed[j]+(energy_shortage[j]-energy_surplus[j])/t.hours == demand_mw[j], f"energy_{j}"
        for p in PRODUCTS:
            value = requirements[p][j]
            if value < 0:
                raise ValueError("Negative reserve requirement")
            if requirement_bump and requirement_bump[:2] == (p,j):
                value += requirement_bump[2]
            m += lp.lpSum(reserve[u.id,j,p] for u in units)+shortage[p,j] >= value, f"requirement_{p}_{j}"
    m += lp.lpSum(terms)
    milp_objective=solve(m)
    print(f"  {run_id}: MILP optimal; solving fixed-discrete LP",flush=True)
    frozen = freeze_discrete(m)
    lp_objective = solve(m,False)
    print(f"  {run_id}: LP optimal; extracting reserve shadow prices",flush=True)
    if abs(milp_objective-lp_objective)>1e-3:
        raise RuntimeError("Fixed-discrete LP changed MILP objective")
    names = [f"requirement_{p}_{j}" for p in PRODUCTS for j in range(count) if requirements[p][j]>1e-9 and not (p=="RR_up" and rr_awards is not None)]
    prices={name:float(m.constraints[name].pi) for name in names}
    shadow_prices=dict(prices)
    bounds={}
    if price_ranges:
        print(f"  {run_id}: computing optional price-range diagnostics",flush=True)
        _,bounds=dual_face(m,names,True,select=False)
    for p in PRODUCTS:
        for j in range(count):
            prices.setdefault(f"requirement_{p}_{j}",0. if p!="RR_up" or rr_awards is None else rr_prices[j])
    # A right derivative may differ from the selected LP shadow price at a kink.
    # Diagnostics never replace the original fixed-LP price vector.
    diagnostics = []
    for p in (PRODUCTS if price_ranges else ()):
        name = f"requirement_{p}_{min(2,count-1)}"
        if requirements[p][min(2,count-1)]<=1e-9 or (p=="RR_up" and rr_awards is not None):
            diagnostics.append(dict(constraint=name,epsilon_mw=0,price=None,right_derivative=None,
                status="monthly_contract_no_ISP_price" if p=="RR_up" and rr_awards is not None else "not_applicable_zero_requirement",matches=True,inside_dual_range=True))
            continue
        d = perturbation(m,name,prices[name])
        d["inside_dual_range"] = (d["right_derivative"] is not None and (not price_ranges or
            bounds[name][0]-.05 <= d["right_derivative"] <= bounds[name][1]+.05)
            )
        diagnostics.append(d)
    rows = []
    shortages={p:[shortage[p,j].value() if (p,j) in shortage else 0 for j in range(count)] for p in PRODUCTS}
    for j,t in enumerate(periods):
        for u in units:
            key = u.id,j
            phase,flags,phase_age=row_phase(phase_data[u.id],j)
            soaking_active=phase in ('sync','soak','desync')
            prior_power=initial_state[u.id]["end_mw"] if j==0 else power[u.id,j-1].value()
            rows.append({"period":t.period,"entity":u.id,"power_mw":power[key].value(),
                "ramp_reference_power_mw":prior_power,"on":round(on[key].value()),
                "up_mwh":up[key].value(),"down_mwh":down[key].value(),
                "start":round(start[key].value()),"stop":round(stop[key].value()),
                "agc":round(agc[key].value()),
                "phase":phase,"phase_binaries":flags,"phase_elapsed_before_minutes":phase_age,
                "up_offer_eur_mwh":u.energy_eur_mwh if u.energy_up_eur_mwh is None else u.energy_up_eur_mwh,
                "down_offer_eur_mwh":u.energy_eur_mwh if u.energy_down_eur_mwh is None else u.energy_down_eur_mwh,
                "startup_class":"hot" if hot[key].value()>.5 else "warm" if warm[key].value()>.5 else "cold" if cold[key].value()>.5 else None,
                "startup_cost_eur":u.startup_eur*(hot[key].value()+u.warm_start_multiplier*warm[key].value()+u.cold_start_multiplier*cold[key].value()),
                "offline_hours":age[key].value(),
                "available_mw":u.maximum_mw if availability_mw is None else availability_mw[u.id][j],
                "soaking":soaking_active,
                "reserve_mw":{p:reserve[u.id,j,p].value() for p in PRODUCTS},
                "reserve_offer_eur_mw_period":{p:rr_prices[j] if p=="RR_up" and rr_awards is not None else u.reserve_eur_mw_period[p] for p in PRODUCTS},
                "reserve_price_eur_mw_period":{p:prices[f"requirement_{p}_{j}"] for p in PRODUCTS}})
            rows[-1]["reserve_price_status"]={p:("monthly_contract" if p=="RR_up" and rr_awards is not None else "not_applicable_zero_requirement" if requirements[p][j]<=1e-9 else
                "administrative_shortfall_cap" if shortages[p][j]>1e-6 else "conditional_fixed_discrete") for p in PRODUCTS}
    components=dict(
        energy_up_cost=sum((u.energy_eur_mwh if u.energy_up_eur_mwh is None else u.energy_up_eur_mwh)*up[u.id,j].value() for u in units for j in range(count))+sum(v.value()*next(r['contract_rate'] for r in emergency_resources if r['id']==rid)*periods[j].hours for (rid,j),v in contingency.items()),
        energy_down_credit=sum((u.energy_eur_mwh if u.energy_down_eur_mwh is None else u.energy_down_eur_mwh)*down[u.id,j].value() for u in units for j in range(count))+sum(o['down_price']*res_down[o['id'],j].value() for o in res_energy_offers or [] for j in range(count)),
        reserve_capacity_cost=sum(reserve[u.id,j,p].value()*(rr_prices[j] if p=='RR_up' and rr_awards is not None else u.reserve_eur_mw_period[p]) for u in units for j in range(count) for p in PRODUCTS),
        startup_cost=sum(r['startup_cost_eur'] for r in rows),
        exceptional_shortfall_cost=0.)
    components.update({k:float(lp.value(v)) for k,v in slack_costs.items()})
    components['total']=components['energy_up_cost']-components['energy_down_credit']+components['reserve_capacity_cost']+components['startup_cost']+sum(components[k] for k in slack_costs)
    if abs(components['total']-lp_objective)>1e-3:raise RuntimeError('ISP offer objective reconciliation failed')
    slacks=[slack_quantities(j,energy_shortage,energy_surplus,shortage) for j in range(count)]
    res_rows=[dict(entity=o['id'],participant=o['participant'],period=t.period,reference_energy_mwh=res_baseline[o['id'],j]*t.hours,
        energy_mwh=res_baseline[o['id'],j]*t.hours-res_down[o['id'],j].value(),up_mwh=0.,down_mwh=res_down[o['id'],j].value(),
        up_offer_eur_mwh=0.,down_offer_eur_mwh=o['down_price'],reserve_mw={p:0. for p in PRODUCTS},price_forming=True,reason='RES_down_offer') for o in res_energy_offers or [] for j,t in enumerate(periods)]
    return {"solution_class":"relaxed_with_slack" if slack_used(slacks) else "feasible_without_slack","objective_components_eur":components,"slack_by_period":slacks,"physical_security_feasible":not slack_used(slacks),"res_offer_rows":res_rows,"res_commercial_curtailment_mw":commercial_curt,"id":run_id,"cutoff":cutoff.isoformat(),"first_period":periods[0].period,
            "status":"Optimal","milp_objective_eur":milp_objective,
            "fixed_lp_objective_eur":lp_objective,"frozen_discrete":frozen,
            "initial_state":initial_state,"rows":rows,"commercial_mwh":commercial_mwh,
            "thermal_state_binary_count":4*count*len(units),
            "power_definition":"(MS_mwh + energy_up_mwh - energy_down_mwh) / hours","demand_mw":list(demand_mw),
            "res_available_mw":list(res_mw),"res_curtailment_mw":[float(lp.value(curtailed[j])) for j in range(count)],
            "requirements_mw":requirements,"reserve_dual_ranges":bounds,
            "emergency_generation_mw":[sum(contingency[r["id"],j].value() for r in emergency_resources or []) for j in range(count)],
            "pricing_method":"fixed_discrete_lp_shadow_prices_v2",
            "lp_reserve_shadow_prices":shadow_prices,
            "price_range_diagnostics_performed":price_ranges,
            "dual_tie_convention":"solver_optimal_dual_vector",
            "lexicographic_tolerance_eur_mw":None,
            "perturbations":diagnostics,"physical_approximation":"MTU_average_dispatch_and_ramps",
            "reserve_shortfalls_mw":shortages,
            "shortage":any(q>1e-6 for values in shortages.values() for q in values),"optimality_gap":0}
