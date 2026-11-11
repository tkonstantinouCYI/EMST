"""Participant-independent daily orchestration for the public Market API."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date,datetime,time
from pathlib import Path
import hashlib,math,platform
import pulp as lp
from .calendar import day_intervals,CYPRUS
from .series import timestamps,mw_values
from .relaxation import used as slack_used
from .system import reserve_series,system_series,observation_update
from .model import Order,PRODUCTS
from .phases import canonical,boundary_state,check_rows
from .commercial import clear_dam
from .dispatch import dispatch
from .optimization import solve
from .settlement import Ledger,D,production_bridge,res_bridge,offtake_bridge,instructed_cash,period_price
from .accounting import post_tariffs,period_recovery


def _check_storage_profile(asset,buy,sell):
    p=asset.parameters;soc=asset.initial_state.soc_mwh;prior=asset.initial_state.power_mw
    for c,d in zip(buy,sell):
        if min(c,d)<-1e-8 or (c>1e-7 and d>1e-7):raise ValueError('Invalid simultaneous storage bid profile')
        power=(d-c)/.5
        if abs(power)>p['power_mw']+1e-5 or abs(power-prior)>p['ramp_mw_hour']*.5+1e-5:raise ValueError('Storage bid power/ramp infeasible')
        soc+=c*p['efficiency_charge']-d/p['efficiency_discharge']
        if not -1e-5<=soc<=p['capacity_mwh']+1e-5:raise ValueError('Storage bid SOC infeasible')
        prior=power


def _storage_profile(asset,index,prices,rules):
    p=asset.parameters;s=asset.strategy.parameters;n=len(index);dt=.5
    if asset.strategy.name=='fixed_circular':
        buy=mw_values(s.get('buy_mwh',[0.]*n),index,asset.id+' buy MWh')
        sell=mw_values(s.get('sell_mwh',[0.]*n),index,asset.id+' sell MWh')
    else:
        if prices is None:raise ValueError("forecast_arbitrage requires the market price_forecast input, not future cleared prices")
        m=lp.LpProblem('storage_strategy',lp.LpMinimize);c={};d={};soc={}
        for j in range(n):
            c[j]=lp.LpVariable(f'charge_{j}',0,p['power_mw']);d[j]=lp.LpVariable(f'discharge_{j}',0,p['power_mw'])
            z=lp.LpVariable(f'mode_{j}',cat='Binary');m+=c[j]<=p['power_mw']*z;m+=d[j]<=p['power_mw']*(1-z)
            previous=asset.initial_state.soc_mwh if j==0 else soc[j-1]
            soc[j]=lp.LpVariable(f'soc_{j}',0,p['capacity_mwh'])
            m+=soc[j]==previous+c[j]*dt*p['efficiency_charge']-d[j]*dt/p['efficiency_discharge']
            prior=asset.initial_state.power_mw if j==0 else d[j-1]-c[j-1]
            m+=d[j]-c[j]-prior<=p['ramp_mw_hour']*dt;m+=prior-d[j]+c[j]<=p['ramp_mw_hour']*dt
        m+=soc[n-1]==float(s.get('terminal_soc_mwh',asset.initial_state.soc_mwh))
        m+=lp.lpSum((prices[j]*(c[j]-d[j])+p['degradation_cost']*(c[j]+d[j]))*dt for j in range(n))
        solve(m);buy=[c[j].value()*dt for j in range(n)];sell=[d[j].value()*dt for j in range(n)]
    _check_storage_profile(asset,buy,sell)
    return buy,sell


def _advance(assets,state,rows,minutes):
    for a in assets:
        if a.id not in rows:continue
        r=rows[a.id];old=state[a.id]
        if a.technology=='storage':state[a.id]=dict(power_mw=r['power_mw'],soc_mwh=r['soc_mwh'])
        else:
            stage=r['phase'];age=old['duration_minutes']+minutes if canonical(old['stage'])==stage else minutes
            stage,age=boundary_state(stage,age,a.parameters['phase_profiles_mw'])
            state[a.id]=dict(on=int(stage!='offline'),power_mw=r['end_mw'],stage=stage,duration_minutes=age)



def run_market(market,time_index,reserve_requirements,price_forecast,historical_rtbm_prices=None,price_ranges=False,
               system_forecasts=None,system_observations=None,previous_system_observation=None):
    from .market import MarketResults
    index=timestamps(time_index)
    if not index:raise ValueError('Empty market time index')
    day=index[0].astimezone(CYPRUS).date();periods=day_intervals(day,30,mode='verified_rules_only');rtus=day_intervals(day,5,mode='verified_rules_only')
    if index!=[p.start for p in periods] or len(index)!=48:raise ValueError('Provide a complete normal-day half-hour time index; official DST handling is not yet available in the public API')
    parameters=market.settlement_parameters
    if not all(k in parameters for k in ('id','tariffs')):raise ValueError('Incomplete daily settlement parameter bundle')
    if any(D(parameters.get(key, 0)) for key in ('reserve_cost_per_day_eur', 'reconciliation_cost_per_day_eur')):
        raise ValueError('Public settlement excludes UA-3 and UA-4')
    rules=market.ruleset.to_dict();assets=list(market.assets.values());participants=market.participants
    if not assets:raise ValueError('No registered assets')
    for p in participants.values():
        if p.brp is not None and p.brp not in participants:raise ValueError(f'Unknown BRP participant {p.brp}')
    forecast={};actual={}
    for a in assets:
        if a.technology not in ('supplier','res'):continue
        forecast[a.id]=mw_values(a.forecast,index,a.id+' forecast')
        raw=a.actual
        if raw is None:raise ValueError('Actual RES generation and consumption are required')
        actual_index=[t.start for t in rtus] if len(raw)==len(rtus) else index
        values=mw_values(raw,actual_index,a.id+' actual')
        actual[a.id]=values if len(values)==len(rtus) else [v for v in values for _ in range(6)]
        if a.technology=='res' and a.parameters['capacity_mw'] is not None:
            if max(forecast[a.id]+actual[a.id])>a.parameters['capacity_mw']+1e-6:raise ValueError('RES time series exceeds registered capacity')
    if not any(a.technology=='supplier' for a in assets):raise ValueError('At least one demand portfolio is required')
    requirements=reserve_series(reserve_requirements,index)
    if any(requirements['RR_up']):raise NotImplementedError('Public API RR procurement needs a contract-award adapter; use the existing fixture runner for monthly RR')
    prices=None
    if price_forecast is not None:
        # Price forecasts may be negative; they are not physical MW arrays.
        if hasattr(price_forecast,'to_numpy'):
            if timestamps(price_forecast.index)!=index:raise ValueError('Price forecast index mismatch')
            raw=price_forecast.to_numpy().reshape(-1)
        else:raw=price_forecast
        prices=[float(v) for v in raw]
        if len(prices)!=48 or not all(math.isfinite(v) for v in prices):raise ValueError('Invalid market price forecast')
    fm={a.id:[0.]*48 for a in assets};private=[]
    if len({c.id for c in market.forwards})!=len(market.forwards):raise ValueError('Duplicate forward contract IDs')
    for c in market.forwards:
        if not rules['markets']['forward']:raise ValueError('Forward market disabled by ruleset')
        if c.asset not in market.assets or market.assets[c.asset].participant!=c.seller:raise ValueError('Invalid forward seller/allocation')
        if market.assets[c.asset].technology not in ('thermal','res'):raise NotImplementedError('Public API forward supply currently supports generation assets')
        if c.version<1 or (c.price_eur_mwh is not None and not math.isfinite(c.price_eur_mwh)):raise ValueError('Invalid forward version or price')
        eligible=[a for a in assets if a.participant==c.buyer and a.technology=='supplier']
        target=c.buyer_entity or (eligible[0].id if len(eligible)==1 else None)
        if target not in {a.id for a in eligible}:raise ValueError('Invalid or ambiguous buyer_entity allocation')
        q=mw_values(c.energy_mwh,index,c.id+' forward MWh')
        for t,value in enumerate(q):fm[c.asset][t]+=value;fm[target][t]+=value
        private.append(c)
    orders=[];margins={};profiles={}
    for a in assets:
        p=a.parameters
        if a.technology=='storage':
            if not rules['storage']['dam']['enabled']:profiles[a.id]=([0.]*48,[0.]*48)
            else:profiles[a.id]=_storage_profile(a,index,prices,rules)
        for t in range(48):
            if a.technology=='thermal':
                q=p['capacity_mw']*.5-fm[a.id][t];price=a.strategy.price('thermal',p['marginal_cost'],rules)
                margins[a.id,t]=q;side='sell'
            elif a.technology=='res':
                q=forecast[a.id][t]*.5-fm[a.id][t];price=a.strategy.price('res',0,rules);side='sell';margins[a.id,t]=q
            elif a.technology=='supplier':
                q=forecast[a.id][t]*.5-fm[a.id][t];price=a.strategy.price('supplier',0,rules);side='buy'
            else:
                buy,sell=profiles[a.id]
                for label,q,price in (('buy',buy[t],float(a.strategy.parameters.get('buy_price',rules['dam_price_cap']))),
                                      ('sell',sell[t],float(a.strategy.parameters.get('sell_price',rules['dam_price_floor'])))):
                    block=a.id+':cycle' if q and rules['storage']['dam']['circular_blocks'] else None
                    orders.append(Order(f'{a.id}:{label}:{t}',a.participant,a.id,t,label,q,price,block,1 if block else 0,circular=bool(block)))
                margins[a.id,t]=p['power_mw']*.5
                continue
            if q<-1e-7:raise ValueError('Forward allocations exceed eligible generation/demand position')
            orders.append(Order(f'{a.id}:{side}:{t}',a.participant,a.id,t,side,max(0,q),price))
    # Storage has separate directional legs, so its gross sell bound remains
    # valid even when the accepted net schedule is withdrawal.
    dam=clear_dam(orders,48,rules['dam_price_floor'],rules['dam_price_cap'],margins)
    schedules=deepcopy(fm)
    for o in orders:
        q=dam['accepted_mwh'][o.id]
        if market.assets[o.asset].technology=='storage':schedules[o.asset][o.period]+=q*(1 if o.side=='sell' else -1)
        else:schedules[o.asset][o.period]+=q
    for a in assets:
        if a.technology=='storage':
            q=schedules[a.id];p=a.parameters
            _check_storage_profile(a,[max(0,-x) for x in q],[max(0,x) for x in q])
    state={}
    for a in assets:
        if a.technology=='thermal':
            v=a.initial_state;state[a.id]=dict(stage=v.stage,on=int(v.stage!='offline'),power_mw=v.power_mw,duration_minutes=v.time_in_stage_minutes)
        elif a.technology=='storage':state[a.id]=asdict(a.initial_state)
    controllable=[a for a in assets if a.technology in ('thermal','storage')]
    load=[sum(forecast[a.id][t] for a in assets if a.technology=='supplier') for t in range(48)]
    renewable=[sum(forecast[a.id][t] for a in assets if a.technology=='res') for t in range(48)]
    participant_load=load;participant_res=renewable
    tso_mtu,tso_rtu,forecast_source=system_series(system_forecasts,index,[t.start for t in rtus],
        name='system_forecasts',fallback={'demand':participant_load,'res':participant_res})
    observed_mtu,observed_rtu,observation_source=system_series(system_observations,index,[t.start for t in rtus],
        name='system_observations',fallback={
            'demand':[sum(actual[a.id][i] for a in assets if a.technology=='supplier') for i in range(288)],
            'res':[sum(actual[a.id][i] for a in assets if a.technology=='res') for i in range(288)]})
    load=tso_mtu['demand'];renewable=tso_mtu['res']
    if previous_system_observation is not None:
        observation_update(load[0],renewable[0],previous=previous_system_observation)
    def plan(start,initial):
        return dispatch(assets,initial,periods[start:],load[start:],renewable[start:],
            {a.id:schedules[a.id][start:] for a in assets},{p:v[start:] for p,v in requirements.items()},rules,price_ranges=price_ranges,res_forecasts={a.id:forecast[a.id][start:] for a in assets if a.technology=='res'})
    first=plan(0,state);first.update(id='ISP_initial',cutoff=datetime.combine(day,time(16),CYPRUS).isoformat(),first_period=0)
    # Initial cutoff is on the previous calendar day.
    from datetime import timedelta
    first['cutoff']=(datetime.combine(day,time(16),CYPRUS)-timedelta(days=1)).isoformat()
    runs=[first];effective={r['period']:r for r in first['rows']};active='ISP_initial';pending=None;rtbm=[]
    def clear_one(interval,current,rows,previous=None,measurement_index=None):
        t=interval.period
        position=t*6+(interval.local.minute%30)//5
        horizon=rtus[position:(t+1)*6];n=len(horizon)
        predictions=[observation_update(tso_rtu['demand'][position+j],tso_rtu['res'][position+j],previous=previous) for j in range(n)]
        result=dispatch(assets,current,horizon,[x['demand_prediction_mw'] for x in predictions],
            [x['res_prediction_mw'] for x in predictions],
            {a.id:[schedules[a.id][t]/6]*n for a in assets},
            {},rules,
            commitment={a.id:rows['assets'][a.id]['on'] for a in controllable if a.technology=='thermal'},reference=rows['assets'],rtbm=True,res_forecasts={a.id:[forecast[a.id][t]]*n for a in assets if a.technology=='res'})['rows'][0]
        result['system_prediction']=dict(predictions[0],measurement_index=measurement_index)
        return result
    previous=deepcopy(previous_system_observation)
    for i,interval in enumerate(rtus):
        if interval.local.hour==11 and interval.local.minute==0:
            projected=deepcopy(state);trace=[]
            for future in rtus[i:i+12]:
                r=clear_one(future,projected,effective[future.period],previous,i-1 if i else None);_advance(controllable,projected,r['assets'],5)
                trace.append(deepcopy(projected))
            second=plan(24,projected);second.update(id='ISP_1100',cutoff=interval.start.isoformat(),first_period=24,
                effective_at=periods[24].start.isoformat(),observed_state=deepcopy(state),forecast_noon_state=deepcopy(projected),forecast_bridge=trace)
            runs.append(second);pending=second
        if pending and interval.period==24:
            effective.update({r['period']:r for r in pending['rows']});active='ISP_1100';pending=None
        r=clear_one(interval,state,effective[interval.period],previous,i-1 if i else None);r.update(isp_run=active,rtu=interval.id)
        _advance(controllable,state,r['assets'],5)
        actual_load=sum(actual[a.id][i] for a in assets if a.technology=='supplier')
        actual_res=sum(actual[a.id][i] for a in assets if a.technology=='res')
        r['RES_delivery_mwh']={};r['RES_delivered_down_mwh']={};r['RES_available_mwh']={}
        r['RES_commercial_curtailment_mwh']={};r['RES_instructions']=[]
        for a in assets:
            if a.technology!='res':continue
            instruction=r['assets'][a.id]['down_mwh'];meter=actual[a.id][i]/12
            if a.parameters['actual_generation_kind']=='available':
                available=meter;scheduled=min(available,schedules[a.id][interval.period]/6)
                commercial_curt=available-scheduled;delivered_down=min(instruction,scheduled)
                meter=scheduled-delivered_down
            else:
                available=None;commercial_curt=r['assets'][a.id]['commercial_curtailment_mwh'];delivered_down=instruction
            r['RES_available_mwh'][a.id]=available
            r['RES_commercial_curtailment_mwh'][a.id]=commercial_curt
            r['RES_delivery_mwh'][a.id]=meter;r['RES_delivered_down_mwh'][a.id]=delivered_down
            if instruction>1e-7:
                r['RES_instructions'].append(dict(asset=a.id,participant=a.participant,direction='down',
                    energy_mwh=instruction,delivered_mwh=delivered_down,undelivered_mwh=max(0,instruction-delivered_down),
                    price_forming=True,reason='RES_down_offer'))
        delivered_res=sum(r['RES_delivery_mwh'].values())*12
        total_control=sum(r['assets'][a.id]['power_mw'] for a in controllable)
        r.update(actual_demand_mwh=actual_load/12,actual_RES_mwh=delivered_res/12,
            physical_imbalance_mwh=(actual_load-delivered_res-total_control)/12)
        for a in assets:
            if a.id not in r['assets']:continue
            r['assets'][a.id]['actual_mwh']=r['RES_delivery_mwh'][a.id] if a.technology=='res' else r['assets'][a.id]['energy_mwh']
        # Correct the AVAILABLE-RES baseline: add back known control, rather than
        # treating instructed curtailment as an unexplained weather error.
        observed_meter=observed_rtu['res'][i] if system_observations is not None else delivered_res
        observed_rtu['res'][i]=observed_meter
        control_mw=12*(sum(r['RES_delivered_down_mwh'].values())+sum(r['RES_commercial_curtailment_mwh'].values()))
        r['observed_system_res_metered_mw']=observed_meter
        r['system_observation']=dict(demand_mw=observed_rtu['demand'][i],res_mw=observed_meter+control_mw,
            forecast_demand_mw=tso_rtu['demand'][i],forecast_res_mw=tso_rtu['res'][i])
        r['system_observation_available_at']=interval.end.isoformat()
        r['observed_system_imbalance_mwh']=(observed_rtu['demand'][i]-observed_meter-total_control)/12
        previous=deepcopy(r['system_observation'])
        rtbm.append(r)
    ledger=Ledger();meters=[]
    for c in private:
        if c.price_eur_mwh is None:raise ValueError('Daily private cash requires a supplied forward price')
        for t,q in enumerate(c.energy_mwh):ledger.post(f'forward:{c.id}:{t}','S01',f'participant:{c.seller}:private',f'participant:{c.buyer}:private',q,c.price_eur_mwh,periods[t].id,'chapter6','forward_input')
    for o in orders:
        ledger.post('DAM:'+o.id,'S02',f'participant:{o.participant}:DAM','A-A',dam['accepted_mwh'][o.id]*(1 if o.side=='sell' else -1),dam['prices_eur_mwh'][o.period],periods[o.period].id,'13.3',o.id)
    residual_checks=[]
    for t,period in enumerate(periods):
        batch=rtbm[t*6:t*6+6]
        pricing_rows=[dict(price_eur_mwh=r['price_eur_mwh'],rows=[dict(x,price_forming=True,reason=x.get('reason','ordinary_market')) for x in r['assets'].values()]) for r in batch]
        weights=[abs(sum(x['up_mwh']-x['down_mwh'] for x in r['rows'] if x['price_forming'])) for r in pricing_rows]
        history=(historical_rtbm_prices.get(t) if isinstance(historical_rtbm_prices,dict) else historical_rtbm_prices[t]) if historical_rtbm_prices is not None else None
        scarcity_weights=[r['slack']['energy_shortage_mwh']+r['slack']['energy_surplus_mwh'] for r in batch]
        if history is None and sum(weights)<=1e-7 and sum(scarcity_weights)<=1e-7:raise ValueError(f'MTU {t}: no price-forming activation; supply three corresponding days of historical_rtbm_prices (6 RTU prices per day)')
        history=history if history is not None else [[0.]*6 for _ in range(3)]
        pricing=period_price(pricing_rows,history)
        if sum(weights)<=1e-7 and sum(scarcity_weights)>1e-7:
            pricing['price_eur_mwh']=sum(w*r['price_eur_mwh'] for w,r in zip(scarcity_weights,batch))/sum(scarcity_weights)
            pricing['price_source']='slack_penalty_when_no_price_forming_activation'
            pricing['historical_fallback']=False
            pricing['slack_penalty_fallback']=True
        price=pricing['price_eur_mwh'];net=sum(pricing['rtu_net_mwh'])
        for r in batch:r.update(settlement_price_eur_mwh=price,settlement_price_source=pricing.get('price_source','historical_fallback' if pricing['historical_fallback'] else 'net_activation_weighting'))
        last_up=max((x['up_offer_eur_mwh'] for r in batch for x in r['assets'].values() if x['up_mwh']>1e-7),default=price)
        last_down=min((x['down_offer_eur_mwh'] for r in batch for x in r['assets'].values() if x['down_mwh']>1e-7),default=price)
        native={};deviations={};owners={};tariff_rows=[]
        for a in assets:
            q=schedules[a.id][t];owner=participants[a.participant].brp or a.participant;owners[a.id]=owner
            if a.technology in ('thermal','storage'):
                delivery=sum(r['assets'][a.id]['actual_mwh'] for r in batch);inst=delivery-q;uninst=0.
            elif a.technology=='res':
                delivery=sum(r['RES_delivery_mwh'][a.id] for r in batch)
                down=sum(r['RES_delivered_down_mwh'][a.id] for r in batch)
                bridge=res_bridge(q,down,delivery,rules['settlement'],a.parameters.get('grant_res',False))
                inst=bridge['INSTQ'];uninst=bridge['UNINSTQ']
            else:
                delivery=sum(actual[a.id][t*6:(t+1)*6])/12
                b=offtake_bridge(q,delivery,tolerance=rules['settlement']['small_supplier_tolerance'] if a.parameters['small_supplier'] else None)
                inst=0.;uninst=b['UNINSTQ'];native[a.participant]=native.get(a.participant,0)+delivery
                tariff_rows.append(dict(id=f'{a.id}:{t}',participant=a.participant,period=period.id,energy_mwh=delivery,hours=.5,voltage=a.parameters['voltage'],purpose='consumption'))
            offset=offset_cash=0.
            if a.technology in ('thermal','storage','res'):
                for j,r in enumerate(batch):
                    if abs(pricing['rtu_net_mwh'][j])<1e-7:
                        x=r['assets'][a.id];offset+=x['up_mwh']-x['down_mwh']
                        offset_cash+=x['up_mwh']*x['up_offer_eur_mwh']-x['down_mwh']*x['down_offer_eur_mwh']
            cash=0. if a.parameters.get('grant_res') else offset_cash+instructed_cash(inst-offset,'short' if net>=0 else 'long',price,price,last_up,last_down)
            ledger.post(f'inst:{a.id}:{t}','S03',f'participant:{a.participant}:nonDAM','A-B',inst,price,period.id,'13.4.6','dispatch_and_meter',cash=cash)
            ledger.post(f'uninst:{a.id}:{t}','S04',f'participant:{owner}:nonDAM','A-B',uninst,price,period.id,'13.4.7-8','meter')
            deviations[a.id]=uninst
            meters.append(dict(asset=a.id,participant=a.participant,period=t,MSQ_mwh=q,actual_mwh=delivery,INSTQ_mwh=inst,UNINSTQ_mwh=uninst))
        selected=effective[t]
        if rules['settlement']['pay_instructed_startups']:
            for a in controllable:
                if a.technology!='thermal':continue
                starts=sum(r['assets'][a.id]['start'] for r in batch)
                if starts:ledger.post(f'startup:{a.id}:{t}','S13',f'participant:{a.participant}:nonDAM','A-C',starts,a.parameters['startup_cost'],period.id,'13.5.2.6','instructed_start')
        for a in controllable:
            for product,award in selected['assets'][a.id]['reserve_mw'].items():
                if not award:continue
                component='S05' if product.startswith('FCR') else 'S06' if product.startswith('aFRR') else 'S07' if product.startswith('mFRR') else 'S08'
                ledger.post(f'reserve:{a.id}:{t}:{product}',component,f'participant:{a.participant}:nonDAM','A-C',award,selected['reserve_prices'][product],period.id,'13.5','ISP_award')
        post_tariffs(ledger,tariff_rows,parameters['tariffs'],include_levies=False)
        period_recovery(ledger,period.id,native,deviations,owners,'qualified_consumption_fallback')
    checks={
        'ISP_energy_balances':all(abs(sum(row['assets'][a.id]['power_mw'] for a in controllable)+row['res_dispatch_reference_mw']-row['res_curtailment_mw']+(row['slack']['energy_shortage_mwh']-row['slack']['energy_surplus_mwh'])/row['hours']-run['demand_mw'][j])<1e-5 for run in runs for j,row in enumerate(run['rows'])),
        'ISP_directional_reserve_requirements':all(sum(x['reserve_mw'][product] for x in row['assets'].values())+row['slack']['reserve_shortage_mw'][product]+1e-5>=run['requirements_mw'][product][j] for run in runs for j,row in enumerate(run['rows']) for product in PRODUCTS),
        'ISP_offer_objective_reconciliation':all(abs(run['objective_components_eur']['total']-run['fixed_lp_objective_eur'])<1e-3 for run in runs),
        'ISP_fixed_LP_optimum':all(abs(run['milp_objective_eur']-run['fixed_lp_objective_eur'])<1e-3 for run in runs),
        '48_DAM_balances':all(abs(sum((1 if o.side=='sell' else -1)*dam['accepted_mwh'][o.id] for o in orders if o.period==t))<1e-5 for t in range(48)),
        'forecast_RTBM_balance':all(abs(sum(r['assets'][a.id]['power_mw'] for a in controllable)+r['res_dispatch_reference_mw']-r['res_curtailment_mw']+(r['slack']['energy_shortage_mwh']-r['slack']['energy_surplus_mwh'])/r['hours']-r['system_prediction']['demand_prediction_mw'])<1e-5 for r in rtbm),
        'observation_causality':all(r['system_prediction']['measurement_index']==(i-1 if i else None) for i,r in enumerate(rtbm)),
        'observation_prediction_formula':all(abs(r['system_prediction']['demand_prediction_mw']-max(0,tso_rtu['demand'][i]+r['system_prediction']['demand_error_mw']))<1e-7 and abs(r['system_prediction']['res_prediction_mw']-max(0,tso_rtu['res'][i]+r['system_prediction']['res_error_mw']))<1e-7 for i,r in enumerate(rtbm)),
        'dispatch_equals_actual':all(r['assets'][a.id]['actual_mwh']==r['assets'][a.id]['energy_mwh'] for r in rtbm for a in controllable),
        'initial_ISP_through_1155':all(r['isp_run']=='ISP_initial' for r in rtbm[:144]),
        'new_ISP_from_noon':all(r['isp_run']=='ISP_1100' for r in rtbm[144:]),
        'double_entry':abs(sum(ledger.balances().values(),D(0)))<D('1e-15'),
        'no_capacity_for_ineligible_storage':all(not any(r['assets'][a.id]['reserve_mw'].values()) for r in rtbm for a in controllable if a.technology=='storage' and not rules['storage']['participation_balancing_capacity']),
        'storage_SOC_bounds':all(-1e-7<=r['assets'][a.id]['soc_mwh']<=a.parameters['capacity_mwh']+1e-7 for r in rtbm for a in controllable if a.technology=='storage'),
        'no_storage_energy_activation_when_disabled':all(abs(r['assets'][a.id]['energy_mwh']-schedules[a.id][r['period']]/6)<1e-6 for r in rtbm for a in controllable if a.technology=='storage' and not rules['storage']['participation_balancing_energy']),
    }
    checks['thermal_four_phase_constraints']=True
    checks['thermal_power_from_market_schedule_and_energy']=True
    for run in runs:
        for a in controllable:
            if a.technology!='thermal':continue
            rows=[r['assets'][a.id] for r in run['rows']]
            initial=run['initial_state'][a.id]
            checks['thermal_four_phase_constraints'] &= all(check_rows(rows,initial['stage'],initial['duration_minutes'],a.parameters['phase_profiles_mw'],a.parameters['minimum_mw'],initial['power_mw']).values())
            checks['thermal_power_from_market_schedule_and_energy'] &= all(abs(r['power_mw']-(run['commercial_mwh'][a.id][j]+r['up_mwh']-r['down_mwh'])/.5)<1e-5 for j,r in enumerate(rows))
    checks['ISP_average_power_ramps']=True
    for run in runs:
        for asset in controllable:
            previous=run['initial_state'][asset.id]['power_mw']
            for row in run['rows']:
                power=row['assets'][asset.id]['power_mw']
                checks['ISP_average_power_ramps'] &= abs(power-previous)<=asset.parameters['ramp_mw_hour']*row['hours']+1e-5
                previous=power
    renewable_assets=[a for a in assets if a.technology=='res']
    checks['RES_down_only_no_capacity']=all(abs(r['assets'][a.id]['up_mwh'])<1e-8 and all(abs(v)<1e-8 for v in r['assets'][a.id]['reserve_mw'].values()) for r in rtbm for a in renewable_assets)
    checks['RES_reference_minus_instruction']=all(abs(r['assets'][a.id]['energy_mwh']-r['assets'][a.id]['reference_energy_mwh']+r['assets'][a.id]['down_mwh'])<1e-6 for r in rtbm for a in renewable_assets)
    checks['RES_available_energy_conserved']=all(abs(r['RES_available_mwh'][a.id]-r['RES_delivery_mwh'][a.id]-r['RES_commercial_curtailment_mwh'][a.id]-r['RES_delivered_down_mwh'][a.id])<1e-6 for r in rtbm for a in renewable_assets if a.parameters['actual_generation_kind']=='available')
    checks['RES_instructed_settlement_quantity']=all(abs(m['INSTQ_mwh']-res_bridge(m['MSQ_mwh'],sum(r['RES_delivered_down_mwh'][m['asset']] for r in rtbm[m['period']*6:(m['period']+1)*6]),m['actual_mwh'],rules['settlement'],market.assets[m['asset']].parameters.get('grant_res',False))['INSTQ'])<1e-6 for m in meters if m['asset'] in {a.id for a in renewable_assets})
    checks['slack_nonnegative']=all(s['energy_shortage_mwh']>=-1e-7 and s['energy_surplus_mwh']>=-1e-7 and min(s['reserve_shortage_mw'].values(),default=0)>=-1e-7 for s in [r['slack'] for r in rtbm]+[s for run in runs for s in run['slack_by_period']])
    checks['latest_observed_error']=True
    for i,r in enumerate(rtbm):
        previous=previous_system_observation if i==0 else rtbm[i-1]['system_observation']
        expected=observation_update(tso_rtu['demand'][i],tso_rtu['res'][i],previous=previous)
        checks['latest_observed_error'] &= all(abs(r['system_prediction'][k]-expected[k])<1e-7 for k in ('demand_prediction_mw','res_prediction_mw','demand_error_mw','res_error_mw'))
        if i:checks['latest_observed_error'] &= datetime.fromisoformat(rtbm[i-1]['system_observation_available_at'])<=datetime.fromisoformat(r['at'])
    prior={a.id:deepcopy(asdict(a.initial_state)) for a in controllable}
    checks['intertemporal_power_ramps']=True;checks['storage_SOC_bridge']=True;checks['physical_power_limits']=True;checks['thermal_energy_integrals']=True
    for r in rtbm:
        for a in controllable:
            x=r['assets'][a.id];p=a.parameters;previous=prior[a.id]
            checks['intertemporal_power_ramps'] &= (all(abs(y-z)<=p['ramp_mw_hour']*x['trajectory_step_hours']+1e-5 for z,y in zip(x['nodes_mw'],x['nodes_mw'][1:])) and abs(x['nodes_mw'][0]-previous['power_mw'])<1e-5 if a.technology=='thermal' else abs(x['power_mw']-previous['power_mw'])<=p['ramp_mw_hour']*r['hours']+1e-5)
            if a.technology=='storage':
                expected=previous['soc_mwh']+x['charge_mwh']*p['efficiency_charge']-x['discharge_mwh']/p['efficiency_discharge']
                checks['storage_SOC_bridge'] &= abs(expected-x['soc_mwh'])<1e-5
                checks['physical_power_limits'] &= abs(x['power_mw'])<=p['power_mw']+1e-5
                prior[a.id]=dict(power_mw=x['power_mw'],soc_mwh=x['soc_mwh'])
            else:
                nodes=x['nodes_mw'];integral=x['trajectory_step_hours']*(.5*nodes[0]+sum(nodes[1:-1])+.5*nodes[-1])
                checks['thermal_energy_integrals'] &= abs(integral-x['energy_mwh'])<1e-6
                checks['physical_power_limits'] &= all(-1e-5<=y<=p['capacity_mw']+1e-5 for y in nodes[1:])
                prior[a.id]=dict(power_mw=x['end_mw'])
    ending={a.id:(dict(power_mw=state[a.id]['power_mw'],soc_mwh=state[a.id]['soc_mwh']) if a.technology=='storage' else
        dict(stage=state[a.id]['stage'],power_mw=state[a.id]['power_mw'],time_in_stage_minutes=state[a.id]['duration_minutes'])) for a in controllable}
    participant_results={p:{'central_cash_eur':str(sum((v for k,v in ledger.balances().items() if k.startswith('participant:'+p+':') and not k.endswith(':private')),D(0))),
                            'private_cash_eur':str(ledger.balances().get('participant:'+p+':private',D(0)))} for p in participants}
    manifest=dict(library_version='0.3.1',ruleset=rules,parameter_id=parameters['id'],day=str(day),
        runtime=dict(python=platform.python_version(),pulp=lp.__version__,solver='HiGHS' if lp.HiGHS().available() else 'CBC'),
        scope='daily_accrual',excluded_uplifts=['UA-3','UA-4','UA-5','UA-6','UA-7'],current_rules_conformance_verified=False,synthetic_parameters=parameters.get('synthetic',False),
        physical_model='ISP_MTU_average_dispatch_and_30_second_RTBM_trajectories',
        actual_delivery='equal_to_RTBM_dispatch',forecast_revisions=False,
        rtbm_forecast_method='TSO_baseline_plus_latest_completed_RTU_error',
        system_forecast_source=forecast_source,system_observation_source=observation_source,
        price_range_diagnostics_performed=price_ranges,
        actual_RES_semantics={a.id:a.parameters['actual_generation_kind'] for a in assets if a.technology=='res'},
        physical_security_feasible=all(run['physical_security_feasible'] for run in runs) and not slack_used([r['slack'] for r in rtbm]),
        ISP_slack_by_run={run['id']:run['slack_by_period'] for run in runs},
        RTBM_slack_used=slack_used([r['slack'] for r in rtbm]),
        solution_class='relaxed_with_slack' if any(not run['physical_security_feasible'] for run in runs) or slack_used([r['slack'] for r in rtbm]) else 'feasible_without_slack',
        RTBM_energy_shortage_mwh=sum(r['slack']['energy_shortage_mwh'] for r in rtbm),
        RTBM_energy_surplus_mwh=sum(r['slack']['energy_surplus_mwh'] for r in rtbm),
        RTBM_capacity_products=[],
        ISP_max_reserve_shortage_mw_by_run={run['id']:{p:max(s['reserve_shortage_mw'][p] for s in run['slack_by_period']) for p in PRODUCTS} for run in runs},
        physical_imbalance_mwh=sum(r['physical_imbalance_mwh'] for r in rtbm),
        max_absolute_RTU_imbalance_mwh=max(abs(r['physical_imbalance_mwh']) for r in rtbm),
        zero_activation_price_fallback='three_corresponding_historical_days_required',
        daily_calculation_complete=all(checks.values()),deferred_components=['monthly_penalties','monthly_account_close','cumulative_settlement','meter_reconciliation','invoice_clearing'],
        code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')})
    inputs=dict(time_index=[t.isoformat() for t in index],participants=[asdict(p) for p in participants.values()],
        assets=[dict(id=a.id,participant=a.participant,technology=a.technology,parameters=deepcopy(a.parameters),
            strategy=asdict(a.strategy),initial_state=asdict(a.initial_state) if a.initial_state is not None else None) for a in assets],
        forecast_mw=forecast,actual_mw=actual,reserve_requirements_mw=requirements,
        system_forecast_mw=tso_rtu,system_observation_mw=observed_rtu,
        previous_system_observation=deepcopy(previous_system_observation),
        storage_bid_profiles_mwh={k:{'buy':v[0],'sell':v[1]} for k,v in profiles.items()},
        price_forecast_eur_mwh=prices,historical_rtbm_prices=deepcopy(historical_rtbm_prices),settlement_parameters=deepcopy(parameters))
    return MarketResults(manifest,[asdict(o) for o in orders],dam,runs,rtbm,meters,ledger.export(),ending,participant_results,
                         dict(passed=all(checks.values()),checks=checks),inputs)
