"""Participant-independent ISP/RTBM with the shared thermal trajectory kernel."""
import math
from copy import deepcopy
import pulp as lp
from .optimization import solve,freeze_discrete,dual_face,primal_violation
from .solver import stage,purpose,SolveError
from .model import PRODUCTS,UP,DOWN,RESPONSE_HOURS
from .physics import thermal_path
from . import configurations as config
from .offers import asset_energy_prices
from .relaxation import build as build_slacks,quantities as slack_quantities,used as slack_used
from .phases import build as build_phases,bind_average_power,row_phase,canonical,PHASES
from . import onoff


@stage(lambda *args,**kwargs: 'RTBM_projection' if kwargs.get('projection',False) else 'RTBM' if kwargs.get('rtbm',False) else 'ISP')
def dispatch(assets,states,intervals,demand,res,commercial,requirements,rules,*,commitment=None,reference=None,rtbm=False,price_ranges=False,storage_terminal_soc=None,res_forecasts=None,mandatory_on=None,terminal_time=None,projection=False,immediate_ramping=False,warm_start_seed=None,initial_shutdown_allowed=None,delivery_interval_minutes=None,res_dispatch_inputs=None):
    thermal=[a for a in assets if a.technology=="thermal"]
    storage=[a for a in assets if a.technology=="storage"]
    renewables=[a for a in assets if a.technology=='res']
    m=lp.LpProblem("library_RTBM" if rtbm else "library_ISP",lp.LpMinimize)
    p={};end={};paths={};on={};start={};stop={};charge={};discharge={};soc={};reserve={};terms=[];up={};down={};activation_modes={};physical_modes={}
    shortage,surplus,reserve_shortage,slack_costs=build_slacks(intervals,rules.get('slack'),include_capacity=not rtbm)
    curt={j:0 for j in range(len(intervals))};res_base={};res_available={};commercial_curt={j:0. for j in range(len(intervals))}
    rb=rules['storage'];capacity=rb['participation_balancing_capacity'];energy=rb['participation_balancing_energy']
    phase_data={};fixed_phases={};configuration_data={}
    for i,a in enumerate(thermal):
        v=a.parameters;initial=states[a.id];initial_phase=canonical(initial['stage'])
        if config.configured(a):
            for j,t in enumerate(intervals):
                k=a.id,j
                up[k]=lp.LpVariable(f'plant_up_{i}_{j}',0);down[k]=lp.LpVariable(f'plant_down_{i}_{j}',0)
                p[k]=(commercial[a.id][j]+up[k]-down[k])/t.hours
                offer_up,offer_down=asset_energy_prices(a,rules)
                if rtbm:
                    bid=a.strategy.price('thermal',v['marginal_cost'],rules);penalty=rules['schedule_deviation_cost_eur_mwh']
                    offer_up,offer_down=bid+penalty,bid-penalty
                terms.extend([offer_up*up[k],-offer_down*down[k]])
            configuration_data[a.id]=config.build(m,a,initial,intervals,p,end,paths,on,start,stop,reserve,terms,commercial[a.id],rules,
                rtbm=rtbm,reference=(reference or {}).get(a.id),immediate=immediate_ramping,
                mandatory=(mandatory_on or {}).get(a.id,[0]*len(intervals)))
            continue
        if rtbm:
            phase=reference[a.id].get('phase','dispatch' if commitment[a.id] else 'offline')
            fixed_phases[a.id]=phase
        elif onoff.enabled(a):
            phase_data[a.id]=onoff.build(m,f'thermal_{i}',intervals,initial,v,delivery_interval_minutes)
            if (initial_shutdown_allowed or {}).get(a.id,True) is False:m+=phase_data[a.id]['stop'][0]==0
        else:
            phase_data[a.id]=build_phases(m,f'thermal_{i}',len(intervals),initial_phase,initial['duration_minutes'],
                v['phase_profiles_mw'],v['minimum_up_minutes'],v['minimum_down_minutes'])
        for j,t in enumerate(intervals):
            dt=t.hours;k=a.id,j;q=commercial[a.id][j]
            up[k]=lp.LpVariable(f'up_{i}_{j}',0);down[k]=lp.LpVariable(f'down_{i}_{j}',0)
            p[k]=(q+up[k]-down[k])/dt
            if rtbm:
                flags={phase_name:int(phase==phase_name) for phase_name in PHASES}
                on[k]=int(phase!='offline');start[k]=int(j==0 and initial_phase=='offline' and phase!='offline')
                stop[k]=int(j==0 and initial_phase!='offline' and phase=='offline')
                dispatch_flag=flags['dispatch']
            else:
                data=phase_data[a.id]
                on[k]=data['on'][j];start[k]=data['start'][j];stop[k]=data['stop'][j]
                dispatch_flag=data['flags']['dispatch',j]
            required=(mandatory_on or {}).get(a.id,[0]*len(intervals))[j]
            if required:
                if rtbm and phase!='dispatch':raise ValueError('Must-run award conflicts with RTBM dispatch phase')
                if not rtbm:m+=dispatch_flag==1
                m+=p[k]>=v['minimum_mw']
            m+=p[k]>=0;m+=p[k]<=v['capacity_mw']*on[k]
            prev_p=initial['power_mw'] if j==0 else (end[a.id,j-1] if rtbm else p[a.id,j-1])
            minimum=v['minimum_mw']*dispatch_flag
            rup=rdn=0
            if not rtbm:
                for product in PRODUCTS:
                    reserve[a.id,j,product]=lp.LpVariable(f'reserve_{i}_{j}_{product}',0,v['reserve_limit_mw'])
                    m+=reserve[a.id,j,product]<=v['reserve_limit_mw']*dispatch_flag
                    ramp=v.get('ramp_up_mw_hour' if product in UP else 'ramp_down_mw_hour',v['ramp_mw_hour'])
                    m+=reserve[a.id,j,product]<=ramp*RESPONSE_HOURS[product]
                    terms.append(reserve[a.id,j,product]*v.get('reserve_prices',{}).get(product,v['reserve_cost'])*dt/.5)
                rup=lp.lpSum(reserve[a.id,j,x] for x in UP)
                rdn=lp.lpSum(reserve[a.id,j,x] for x in DOWN)
            if not rtbm:
                if onoff.enabled(a):
                    onoff.bind(m,data,j,p[k],prev_p,initial,v,[reserve[a.id,j,x] for x in PRODUCTS])
                else:
                    m+=p[k]-prev_p<=v['ramp_mw_hour']*dt, f'average_ramp_up_{i}_{j}'
                    m+=prev_p-p[k]<=v['ramp_mw_hour']*dt, f'average_ramp_down_{i}_{j}'
                m+=p[k]-rdn>=minimum
                m+=p[k]+rup<=v['capacity_mw']*on[k]
                if not onoff.enabled(a):bind_average_power(m,data,j,p[k],v['capacity_mw'],v['minimum_mw'],initial['power_mw'])
            elif onoff.enabled(a):
                end[k]=lp.LpVariable(f'onoff_power_{i}_{j}',0,v['capacity_mw'])
                m+=end[k]==p[k];m+=p[k]>=minimum
                paths[k]=[end[k],end[k]]
                if reference[a.id].get('boundary_minimum',False):m+=p[k]==v['minimum_mw']*on[k]
                if (j>0 or initial_phase=='dispatch') and phase=='dispatch':
                    m+=p[k]-prev_p<=v['ramp_up_mw_hour']*dt
                    m+=prev_p-p[k]<=v['ramp_down_mw_hour']*dt
                until=reference[a.id].get('next_boundary_minimum_minutes')
                if until is not None and phase=='dispatch':
                    m+=p[k]<=v['minimum_mw']+v['ramp_down_mw_hour']*max(0,until-j*t.minutes)/60
                if initial_phase=='dispatch' and phase=='offline' and initial['power_mw']>v['minimum_mw']+1e-7:
                    raise ValueError('ON/OFF shutdown requires preceding executed power at technical minimum')
            elif immediate_ramping:
                end[k]=lp.LpVariable(f'instant_power_{i}_{j}',0,v['capacity_mw'])
                m+=end[k]==p[k]
                m+=p[k]>=minimum
                paths[k]=[end[k],end[k]]
                if phase in ('sync','soak','desync'):
                    index=int(reference[a.id]['phase_elapsed_before_minutes']//30)
                    m+=p[k]==v['phase_profiles_mw'][phase][index]
            else:
                entering_dispatch=phase=='dispatch' and initial_phase!='dispatch' and j==0
                node_minimum=0 if entering_dispatch else minimum
                paths[k],integrated=thermal_path(m,f'thermal_path_{i}_{j}',prev_p,dt,t.minutes*2,
                    v['capacity_mw'],on[k],node_minimum,v['ramp_mw_hour'])
                end[k]=paths[k][-1]
                m+=p[k]*dt==integrated
                m+=p[k]>=node_minimum
                m+=end[k]>=minimum
                if phase in ('sync','soak','desync'):
                    index=int(reference[a.id]['phase_elapsed_before_minutes']//30)
                    target=v['phase_profiles_mw'][phase][index]
                    for n,node in enumerate(paths[k][1:],1):
                        fraction=(j+n/(t.minutes*2))/len(intervals)
                        m+=node==initial['power_mw']+(target-initial['power_mw'])*fraction
            bid=a.strategy.price('thermal',v['marginal_cost'],rules)
            if rtbm:
                penalty=rules['schedule_deviation_cost_eur_mwh']
                terms += [(bid+penalty)*up[k]+(penalty-bid)*down[k],start[k]*v['startup_cost'],on[k]*v['no_load_cost_per_hour']*dt]
            else:
                offer_up,offer_down=asset_energy_prices(a,rules)
                terms += [offer_up*up[k]-offer_down*down[k],start[k]*v['startup_cost']]
                if onoff.enabled(a):terms.append(on[k]*v['no_load_cost_per_hour']*dt)
    for i,a in enumerate(storage):
        v=a.parameters;initial=states[a.id]
        for j,t in enumerate(intervals):
            k=a.id,j;dt=t.hours
            charge[k]=lp.LpVariable(f"charge_{i}_{j}",0,v['power_mw']);discharge[k]=lp.LpVariable(f"discharge_{i}_{j}",0,v['power_mw'])
            mode=lp.LpVariable(f"storage_mode_{i}_{j}",cat="Binary")
            physical_modes[k]=mode
            m+=charge[k]<=v['power_mw']*mode;m+=discharge[k]<=v['power_mw']*(1-mode)
            p[k]=discharge[k]-charge[k]
            prev_p=initial['power_mw'] if j==0 else p[a.id,j-1]
            if not (rtbm and immediate_ramping):
                m+=p[k]-prev_p<=v['ramp_mw_hour']*dt;m+=prev_p-p[k]<=v['ramp_mw_hour']*dt
            previous=initial['soc_mwh'] if j==0 else soc[a.id,j-1]
            soc[k]=lp.LpVariable(f"soc_{i}_{j}",0,v['capacity_mwh'])
            m+=soc[k]==previous+charge[k]*dt*v['efficiency_charge']-discharge[k]*dt/v['efficiency_discharge']
            if not energy:m+=p[k]*dt==commercial[a.id][j]
            if not rtbm:
                for product in PRODUCTS:
                    reserve[a.id,j,product]=lp.LpVariable(f"storage_reserve_{i}_{j}_{product}",0,v['power_mw'] if capacity else 0)
                    m+=reserve[a.id,j,product]<=v['ramp_mw_hour']*RESPONSE_HOURS[product]
                    terms.append(reserve[a.id,j,product]*v.get('reserve_prices',{}).get(product,v['reserve_cost'])*dt/.5)
                rup=lp.lpSum(reserve[a.id,j,x] for x in UP);rdn=lp.lpSum(reserve[a.id,j,x] for x in DOWN)
                m+=p[k]+rup<=v['power_mw'];m+=p[k]-rdn>=-v['power_mw']
                hold=rules['reserve_sustain_hours']
                for available in (previous,soc[k]):
                    m+=rup*hold/v['efficiency_discharge']<=available
                    m+=rdn*hold*v['efficiency_charge']<=v['capacity_mwh']-available
            q=commercial[a.id][j]
            # Separate directional activations; physical and activation signs
            # coincide only when the accepted commercial schedule is zero.
            up_limit=max(0.,v['power_mw']*dt-q) if energy else 0.
            down_limit=max(0.,v['power_mw']*dt+q) if energy else 0.
            up[k]=lp.LpVariable(f'storage_up_{i}_{j}',0,up_limit)
            down[k]=lp.LpVariable(f'storage_down_{i}_{j}',0,down_limit)
            if up_limit>0 and down_limit>0:
                activation_modes[k]=(1-mode if q==0 else
                    lp.LpVariable(f'storage_activation_mode_{i}_{j}',cat='Binary'))
                m+=up[k]<=up_limit*activation_modes[k]
                m+=down[k]<=down_limit*(1-activation_modes[k])
            m+=p[k]*dt==q+up[k]-down[k]
            sell,buy=asset_energy_prices(a,rules)
            terms.append(sell*up[k]-buy*down[k])
            if rtbm:terms.append((charge[k]+discharge[k])*dt*v['degradation_cost'])
        if storage_terminal_soc is not None and a.id in storage_terminal_soc:
            target=storage_terminal_soc[a.id]
            if not rtbm or terminal_time is None:
                m+=soc[a.id,len(intervals)-1]==target
            else:
                for j,t in enumerate(intervals):
                    remaining=max(0.,(terminal_time-t.end).total_seconds()/3600)
                    m+=soc[a.id,j]>=target-v['power_mw']*v['efficiency_charge']*remaining
                    m+=soc[a.id,j]<=target+v['power_mw']/v['efficiency_discharge']*remaining
    for j,t in enumerate(intervals):
        weights={a.id:max(0,(res_forecasts or {}).get(a.id,[1.]*len(intervals))[j]) for a in renewables}
        if renewables and sum(weights.values())<=1e-12:
            weights={a.id:(a.parameters.get('capacity_mw') or 1.) for a in renewables}
        total=sum(weights.values())
        for i,a in enumerate(renewables):
            k=a.id,j
            available=res[j]*weights[a.id]/total
            data=(res_dispatch_inputs or {}).get(a.id)
            fixed_curtailment=None
            if data is not None:
                output=data['output_mw'][j]
                fixed_curtailment=data.get('known_curtailment_mw',[0.]*len(intervals))[j] if data['kind']=='metered' else None
                available=output+(fixed_curtailment or 0.)
            baseline=max(0.,available)
            res_available[k]=available;res_base[k]=baseline
            commercial_curt[j]+=available-baseline
            up[k]=lp.LpVariable(f'RES_up_{i}_{j}',0,0)
            m+=up[k]==0
            lower=0. if fixed_curtailment is None else fixed_curtailment*t.hours
            upper=(baseline*t.hours if rules.get('res',{}).get('participation_balancing_energy',True) else 0.) if fixed_curtailment is None else lower
            down[k]=lp.LpVariable(f'RES_down_{i}_{j}',lower,upper)
            p[k]=baseline-down[k]/t.hours
            if not rtbm:
                for product in PRODUCTS:
                    reserve[a.id,j,product]=lp.LpVariable(f'RES_capacity_{i}_{j}_{product}',0,0)
                    m+=reserve[a.id,j,product]==0
            curt[j]+=down[k]/t.hours
            terms.append(-a.parameters['energy_down_price']*down[k])
        m+=lp.lpSum(p[a.id,j] for a in thermal+storage)+lp.lpSum(res_base[a.id,j] for a in renewables)-curt[j]+(res[j] if not renewables else 0)+(shortage[j]-surplus[j])/t.hours==demand[j],f"energy_{j}"
        if not rtbm:
            for product in PRODUCTS:
                m+=lp.lpSum(reserve[a.id,j,product] for a in thermal+storage)+reserve_shortage[product,j]>=requirements[product][j],f"requirement_{product}_{j}"
    terms.extend(slack_costs.values())
    m+=lp.lpSum(terms)
    if not rtbm:
        from .warm_start import prepare
        prepare(m,assets,states,intervals,commercial,warm_start_seed)
    binary_count=sum(v.isBinary() for v in m.variables())
    milp=solve(m);frozen=freeze_discrete(m)
    with purpose('fixed_discrete_pricing'):fixed=solve(m,False)
    if abs(milp-fixed)>1e-3:raise RuntimeError("Fixed-discrete dispatch LP changed the optimum")
    prices=[float(m.constraints[f'energy_{j}'].pi)/t.hours for j,t in enumerate(intervals)]
    reserve_prices=[{} for _ in intervals] if rtbm else [{x:float(m.constraints[f'requirement_{x}_{j}'].pi) if requirements[x][j]>1e-9 else 0. for x in PRODUCTS} for j in range(len(intervals))]
    bounds={}
    if price_ranges and not rtbm:
        names=[f'requirement_{x}_{j}' for x in PRODUCTS for j in range(len(intervals)) if requirements[x][j]>1e-9]
        _,bounds=dual_face(m,names,True,select=False)
    # Select smooth physical trajectories on the primary optimum face. Prices
    # above remain the primary fixed-discrete LP duals, never smoothing duals.
    smoothing=dict(status='not_required')
    if rtbm and paths:
        primary=m.objective
        primary_variables=list(m.variables());primary_constraints=list(m.constraints.values())
        saved={v:v.varValue for v in primary_variables}
        if primal_violation(primary_variables,primary_constraints)>1e-5:
            raise RuntimeError('Primary RTBM solution violates physical constraints before smoothing')
        tolerance=max(1e-5,abs(fixed)*1e-9)
        m+=primary<=fixed+tolerance
        m+=primary>=fixed-tolerance
        smooth=[]
        for (aid,j),nodes in paths.items():
            for n,(left,right) in enumerate(zip(nodes,nodes[1:])):
                variation=lp.LpVariable(f'smooth_{aid}_{j}_{n}',0)
                m+=variation>=right-left;m+=variation>=left-right
                smooth.append(.01*variation)
            terminal=lp.LpVariable(f'terminal_smooth_{aid}_{j}',0)
            m+=terminal>=nodes[-1]-p[aid,j];m+=terminal>=p[aid,j]-nodes[-1]
            smooth.append(terminal)
        m.setObjective(lp.lpSum(smooth))
        smoothing=dict(status='succeeded',primary_objective_eur=fixed,objective_tolerance_eur=tolerance)
        try:
            with purpose('trajectory_smoothing'):solve(m,False)
            violation=primal_violation(primary_variables,primary_constraints)
            deviation=abs(float(lp.value(primary))-fixed)
            if violation>1e-5 or deviation>tolerance+1e-7:
                raise RuntimeError('Smoothed RTBM solution failed primal/economic validation')
        except (SolveError,RuntimeError) as error:
            for variable,value in saved.items():variable.varValue=value
            violation=primal_violation(primary_variables,primary_constraints)
            deviation=abs(float(lp.value(primary))-fixed)
            if violation>1e-5 or deviation>tolerance+1e-7:raise RuntimeError('Certified primary RTBM fallback failed validation') from error
            smoothing.update(status='fallback_primary',reason=str(error),
                failed_solve=error.record if isinstance(error,SolveError) else None)
        smoothing.update(max_primary_constraint_violation=violation,primary_objective_deviation_eur=deviation,
            prices_source='fixed_discrete_pricing')
    rows=[]
    for j,t in enumerate(intervals):
        group={}
        for a in thermal+storage+renewables:
            power=float(lp.value(p[a.id,j]));q=power*t.hours
            row=dict(asset=a.id,participant=a.participant,power_mw=power,energy_mwh=q,
                     up_mwh=up[a.id,j].value(),down_mwh=down[a.id,j].value(),
                     reserve_mw={} if rtbm else {x:reserve[a.id,j,x].value() for x in PRODUCTS},
                     reserve_offer_eur_mw_period={} if rtbm else {x:a.parameters.get('reserve_prices',{}).get(x,a.parameters.get('reserve_cost',0))*t.hours/.5 for x in PRODUCTS})
            if a.technology=='thermal':
                bid=a.strategy.price('thermal',a.parameters['marginal_cost'],rules)
                offer_up,offer_down=((bid+rules['schedule_deviation_cost_eur_mwh'],bid-rules['schedule_deviation_cost_eur_mwh']) if rtbm else asset_energy_prices(a,rules))
                row.update(up_offer_eur_mwh=offer_up,down_offer_eur_mwh=offer_down)
            elif a.technology=='storage':row.update(up_offer_eur_mwh=float(a.strategy.parameters.get('balancing_up_price',100)),down_offer_eur_mwh=float(a.strategy.parameters.get('balancing_down_price',0)))
            else:row.update(up_offer_eur_mwh=0.,down_offer_eur_mwh=a.parameters['energy_down_price'],forecast_available_mwh=res_available[a.id,j]*t.hours,reference_energy_mwh=res_base[a.id,j]*t.hours,commercial_curtailment_mwh=(res_available[a.id,j]-res_base[a.id,j])*t.hours,price_forming=True,reason='RES_down_offer')
            if a.technology=='thermal':
                row.update(on=round(lp.value(on[a.id,j])),start=round(lp.value(start[a.id,j])),stop=round(lp.value(stop[a.id,j])))
                if rtbm:
                    row.update(initial_mw=float(lp.value(paths[a.id,j][0])),end_mw=end[a.id,j].value(),
                        nodes_mw=[float(lp.value(x)) for x in paths[a.id,j]],trajectory_step_hours=t.hours/(len(paths[a.id,j])-1),ramping_mode='immediate' if immediate_ramping else 'trajectory')
                else:
                    row['ramp_reference_power_mw']=states[a.id]['power_mw'] if j==0 else float(lp.value(p[a.id,j-1]))
            elif a.technology=='storage':row.update(physical_charging_mode=round(lp.value(physical_modes[a.id,j])),
                activation_up_mode=round(lp.value(activation_modes[a.id,j])) if (a.id,j) in activation_modes else None,
                activation_mode_reused=(a.id,j) in activation_modes and commercial[a.id][j]==0,charge_mwh=charge[a.id,j].value()*t.hours,discharge_mwh=discharge[a.id,j].value()*t.hours,soc_mwh=soc[a.id,j].value())
            if a.technology=='storage':
                raw_soc=row['soc_mwh'];limit=a.parameters['capacity_mwh']
                if raw_soc < -1e-7 or raw_soc > limit+1e-7:raise RuntimeError('Storage SOC violates its physical bounds')
                row['soc_mwh']=max(0.,min(limit,raw_soc))  # Keep boundary states valid within LP feasibility precision.
            if a.technology=='thermal':
                if config.configured(a):
                    row.update(config.row(configuration_data[a.id],j,a,t,rtbm))
                    row['configuration_endpoint_mw']=float(lp.value(end[a.id,j]))
                    cost_delta=row['effective_marginal_cost']-a.parameters['marginal_cost']
                    row['up_offer_eur_mwh']+=cost_delta;row['down_offer_eur_mwh']+=cost_delta
                elif onoff.enabled(a) and not rtbm:
                    row.update(onoff.row(phase_data[a.id],j));phase=row['phase'];flags=row['phase_binaries'];age=row['phase_elapsed_before_minutes']
                elif rtbm:
                    phase=fixed_phases[a.id];flags={q:int(phase==q) for q in PHASES}
                    age=states[a.id]['duration_minutes'] if canonical(states[a.id]['stage'])==phase else 0
                    age+=j*t.minutes
                else:phase,flags,age=row_phase(phase_data[a.id],j)
                if not config.configured(a):
                    row.update(phase=phase,phase_binaries=flags,phase_elapsed_before_minutes=age,
                        startup_cost_eur=lp.value(start[a.id,j])*a.parameters['startup_cost'])
                    if onoff.enabled(a):
                        row.update(commitment_model='on_off',power_convention='constant_interval_MW',boundary_power_policy='technical_minimum',boundary_reserve_policy='none')
                        if rtbm:row['boundary_minimum']=reference[a.id].get('boundary_minimum',False)
                        if rtbm:row['ramping_mode']='rectangular_on_off'
                if rtbm:
                    row.update(phase_initial_mw=float(lp.value(paths[a.id,j][0])),phase_terminal_mw=end[a.id,j].value())
            group[a.id]=row
        rows.append(dict(period=t.period,at=t.start.isoformat(),hours=t.hours,assets=group,
                         res_curtailment_mw=float(lp.value(curt[j])),res_commercial_curtailment_mw=commercial_curt[j],
                         res_dispatch_reference_mw=sum(res_base[a.id,j] for a in renewables) if renewables else res[j],slack=slack_quantities(j,shortage,surplus,reserve_shortage),
                         price_eur_mwh=prices[j],reserve_prices=reserve_prices[j],smoothing=deepcopy(smoothing)))
    objective_components=None
    if not rtbm:
        objective_components=dict(
            energy_up_cost=sum(asset_energy_prices(a,rules)[0]*up[a.id,j].value() for a in thermal+storage+renewables for j in range(len(intervals))),
            energy_down_credit=sum(asset_energy_prices(a,rules)[1]*down[a.id,j].value() for a in thermal+storage+renewables for j in range(len(intervals))),
            reserve_capacity_cost=sum(reserve[a.id,j,x].value()*a.parameters.get('reserve_prices',{}).get(x,a.parameters['reserve_cost'])*intervals[j].hours/.5 for a in thermal+storage for j in range(len(intervals)) for x in PRODUCTS),
            startup_cost=sum(lp.value(start[a.id,j])*a.parameters['startup_cost'] for a in thermal for j in range(len(intervals))))
        if configuration_data:
            objective_components['configuration_transition_cost']=sum(float(lp.value(v) or 0) for d in configuration_data.values() for v in d['transition_cost'].values())
            objective_components['configuration_operating_adjustment']=sum(float(lp.value(v) or 0) for d in configuration_data.values() for v in d['operating_adjustment'].values())
        if any(onoff.enabled(a) for a in thermal):
            objective_components['no_load_cost']=sum(lp.value(on[a.id,j])*a.parameters['no_load_cost_per_hour']*t.hours for a in thermal if onoff.enabled(a) for j,t in enumerate(intervals))
        objective_components.update({k:float(lp.value(v)) for k,v in slack_costs.items()})
        objective_components['total']=objective_components['energy_up_cost']-objective_components['energy_down_credit']+objective_components['reserve_capacity_cost']+objective_components['startup_cost']+sum(objective_components[k] for k in slack_costs)+objective_components.get('configuration_transition_cost',0)+objective_components.get('configuration_operating_adjustment',0)+objective_components.get('no_load_cost',0)
        if abs(objective_components['total']-fixed)>1e-3:raise RuntimeError('ISP offer objective reconciliation failed')
    return dict(**({'binary_variable_count':binary_count,'effective_interval_minutes':intervals[0].minutes} if any(onoff.enabled(a) for a in thermal) else {}),
                **({'warm_start':deepcopy(m._emst_warm_start)} if hasattr(m,'_emst_warm_start') else {}),solution_class="relaxed_with_slack" if slack_used([r["slack"] for r in rows]) else "feasible_without_slack",
                smoothing=smoothing,objective_components_eur=objective_components,physical_security_feasible=not slack_used([r["slack"] for r in rows]),
                slack_by_period=[r["slack"] for r in rows],status="Optimal",milp_objective_eur=milp,fixed_lp_objective_eur=fixed,
                frozen_discrete=frozen,rows=rows,pricing_method="fixed_discrete_lp_shadow_prices",
                reserve_dual_ranges=bounds,price_range_diagnostics_performed=price_ranges and not rtbm,capacity_products=[] if rtbm else list(PRODUCTS),
                demand_mw=list(demand),res_available_mw=list(res),requirements_mw={} if rtbm else deepcopy(requirements),initial_state=deepcopy(states),
                thermal_physics=('asset_specific_thermal_models' if any(onoff.enabled(a) for a in thermal) else 'RTBM_thermal_path' if rtbm else 'MTU_average_power_ramps'),commercial_mwh=deepcopy(commercial),
                storage_activation_formulation='separate_up_down_with_binary_exclusivity',storage_activation_binary_count=sum(name.startswith('storage_activation_mode_') for name in frozen),
                thermal_state_binary_count=(sum((1 if onoff.enabled(a) else 4)*len(intervals) for a in thermal if not config.configured(a))+sum(d['commitment_binary_count'] for d in configuration_data.values())) if not rtbm else 0,
                **({key:sum(d[key] for d in configuration_data.values()) for key in ('occupancy_binary_count','transition_start_binary_count','component_event_binary_count','commitment_binary_count')} if configuration_data else {}),
                **({'configuration_state_binary_count':sum(d['occupancy_binary_count'] for d in configuration_data.values())} if configuration_data else {}),
                power_definition='(MS_mwh + energy_up_mwh - energy_down_mwh) / hours')
