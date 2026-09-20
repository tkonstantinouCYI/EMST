"""Reproduce the multi-participant case and transparent synthetic sensitivities.

Run from the repository root: python experiments/medpower2026/run_experiments.py
Inputs are a value-only JSON snapshot of the original workbook (SHA256 recorded).
No changes to the library or original workbook are made.
"""
from __future__ import annotations
import csv
import math
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
import warnings
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pulp
from emst.core.bids import Bid, BlockBid
from emst.core.contracts import ForwardContract
from emst.core.participants import Participant
from emst.core.time import MarketTime
from emst.markets.forward import ForwardMarket
from emst.markets.dam import DAMMarket
from emst.markets.intraday import IntradayAuctionMarket

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / 'case_inputs.json').read_text())
STAGES = ('DAM', 'IDA1', 'IDA2', 'IDA3')
# name, demand multiplier, RES multiplier, forecast-revision multiplier
SCENARIOS = [('Baseline',1,1,1), ('Low RES',1,.7,1), ('High RES',1,1.3,1),
             ('High demand',1.15,1,1), ('No revision',1,1,0),
             ('Double revision',1,1,2), ('Reverse revision',1,1,-1)]
UNITS = sorted([r for r in DATA['Assets'] if r['asset_type']=='thermal'],
               key=lambda r:r['marginal_cost_eur_mwh'])
FOCAL = ('IND','COM','RES','PV_C','PV_A','W_C','W_A')
PARTICIPANTS = [Participant(u['participant_id'],u['participant_id'],'producer',[]) for u in UNITS]
PARTICIPANTS += [Participant(pid,pid,'supplier' if pid in FOCAL[:3] else 'producer',[]) for pid in FOCAL]
# Fixed baseline delivery profiles; shares refer to ONE half-size RES portfolio.
CONTRACTS = [('PV_COM','PV_C','COM','pv',.60,85.),
             ('PV_RES','PV_C','RES','pv',.10,90.),
             ('W_IND','W_C','IND','wind',.60,100.),
             ('W_RES','W_C','RES','wind',.10,105.)]

def demand_shares():
    mean=sum(r['demand_dam_mwh'] for r in DATA['TimeSeries'])/48
    shares={pid:{} for pid in FOCAL[:3]}
    for r in DATA['TimeSeries']:
        p=int(r['mtu_id']); hour=(p-.5)/2
        industrial=.30*mean/r['demand_dam_mwh']
        assert 0<industrial<1
        commercial=.15+.85*math.exp(-.5*((hour-13)/3.5)**2)
        residential=.35+.65*math.exp(-.5*((hour-20)/3)**2)
        shares['IND'][p]=industrial
        shares['COM'][p]=(1-industrial)*commercial/(commercial+residential)
        shares['RES'][p]=(1-industrial)*residential/(commercial+residential)
        assert abs(sum(shares[pid][p] for pid in shares)-1)<1e-12
    return shares
SHARES=demand_shares()

def resample(values, minutes):
    """Integrate piecewise-constant half-hour power over each target MTU."""
    out = {}
    for p,start in enumerate(range(0,1440,minutes),1):
        out[p]=sum(values[j+1]*max(0,min(start+minutes,(j+1)*30)-max(start,j*30))/30
                   for j in range(48))
    assert abs(sum(out.values())-sum(values.values())) < 1e-7
    return out

def inputs(minutes, dm, rm, revision):
    forecasts={}
    for market,factor in zip(STAGES,(0,1,.75,.5)):
        forecasts[market]={}
        for pid,key,mult,sign in [('SUPPLIER_1','demand',dm,-1),('PV_AGG','pv',rm,1),('WIND_AGG','wind',rm,1)]:
            values={int(r['mtu_id']):sign*max(0,mult*(r[key+'_dam_mwh']+
                    factor*revision*(r[key+'_ida1_mwh']-r[key+'_dam_mwh']))) for r in DATA['TimeSeries']}
            forecasts[market][pid]=resample(values,minutes)
    # Split each aggregate AFTER scenario transformation, preserving every MTU.
    split={}
    for market,aggregate in forecasts.items():
        split[market]={}
        # Split on the original 30-minute grid before resampling.
        factor=dict(zip(STAGES,(0,1,.75,.5)))[market]
        demand={int(r['mtu_id']):-max(0,dm*(r['demand_dam_mwh']+factor*revision*(r['demand_ida1_mwh']-r['demand_dam_mwh']))) for r in DATA['TimeSeries']}
        for pid in FOCAL[:3]:
            split[market][pid]=resample({p:q*SHARES[pid][p] for p,q in demand.items()},minutes)
        for source,pids in [('PV_AGG',('PV_C','PV_A')),('WIND_AGG',('W_C','W_A'))]:
            for pid in pids: split[market][pid]={p:q/2 for p,q in aggregate[source].items()}
        for p in aggregate['SUPPLIER_1']:
            assert abs(sum(split[market][pid][p] for pid in FOCAL[:3])-aggregate['SUPPLIER_1'][p])<1e-8
    forecasts=split
    contracts=[]
    for cid,seller,buyer,key,share,price in CONTRACTS:
        values={int(r['mtu_id']):.5*share*r[key+'_dam_mwh'] for r in DATA['TimeSeries']}
        contracts.append(ForwardContract.from_period_quantities(contract_id=cid,
            delivery_participant_id=seller,offtake_participant_id=buyer,
            period_quantities=resample(values,minutes),price=price))
    return forecasts,contracts

def run(minutes=30, dm=1, rm=1, revision=1):
    forecasts,contracts=inputs(minutes,dm,rm,revision)
    mt=MarketTime.single_day(date(2025,1,1),mtu_minutes=minutes)
    started=time.perf_counter()
    fm=ForwardMarket(mt).clear(contracts=contracts,participants=PARTICIPANTS)
    cumulative={pid:dict(pos) for pid,pos in fm.participant_positions.items()}
    results={'FM':fm}; rows=[]; checks=[]; stage_times={}; ledger=[]
    def record(stage,pid,p,side,q,price,order):
        if q>1e-10:
            ledger.append(dict(stage=stage,participant=pid,period=p,side=side,quantity_mwh=q,
                               price_eur_mwh=price,cash_eur=q*price*(1 if side=='sell' else -1),order=order))
    for c in contracts:
        for p,q in c.period_quantities.items():
            record('FM',c.delivery_participant_id,p,'sell',q,c.price,c.contract_id)
            record('FM',c.offtake_participant_id,p,'buy',q,c.price,c.contract_id)
    for market in STAGES:
        stage_start=time.perf_counter(); bids=[]; blocks=[]
        pre_gap=sum(abs(values[p]-cumulative.get(pid,{}).get(p,0))
                    for pid,values in forecasts[market].items() for p in mt.periods)
        for p in mt.periods:
            reference=results['DAM'].prices[p] if market!='DAM' else 0
            for pid,values in forecasts[market].items():
                delta=values[p]-cumulative.get(pid,{}).get(p,0)
                if abs(delta)>1e-9:
                    price=(0 if delta>0 else 500) if market=='DAM' else reference*(.9 if delta>0 else 1.1)
                    bids.append(Bid(f'{market}_{pid}_{p}',pid,market,p,'sell' if delta>0 else 'buy',abs(delta),price))
            for u in UNITS:
                pid=u['participant_id']; cap=u['capacity_mw']*mt.mtu_hours
                cost=u['marginal_cost_eur_mwh']; current=cumulative.get(pid,{}).get(p,0)
                if market=='DAM':
                    bids.append(Bid(f'{market}_{pid}_FLEX_{p}',pid,market,p,'sell',.7*cap,cost))
                else:
                    if current>1e-9: bids.append(Bid(f'{market}_{pid}_BUYBACK_{p}',pid,market,p,'buy',current,cost))
                    if cap-current>1e-9: bids.append(Bid(f'{market}_{pid}_UP_{p}',pid,market,p,'sell',cap-current,1.1*reference))
        if market=='DAM':
            n=360//minutes
            for u in UNITS:
                for start in range(1,len(mt.periods)+1,n):
                    periods=tuple(range(start,start+n)); pid=u['participant_id']
                    blocks.append(BlockBid(f'DAM_BLOCK_{pid}_{start}',pid,market,periods,
                        (.3*u['capacity_mw']*mt.mtu_hours,)*n,(u['marginal_cost_eur_mwh'],)*n))
            result=DAMMarket(market_time=mt).clear(bids=bids,block_bids=blocks)
        else:
            result=IntradayAuctionMarket(name=market,market_time=mt).clear(bids=bids)
        stage_times[market]=time.perf_counter()-stage_start
        results[market]=result
        # Independent reconstruction from bid acceptances checks signed position accounting.
        recon=defaultdict(lambda:defaultdict(float))
        for b in bids:
            q=result.accepted_bids.get(b.bid_id,0)
            assert -1e-4<=q<=b.quantity+1e-4
            recon[b.participant_id][b.period]+=q*(1 if b.side=='sell' else -1)
            record(market,b.participant_id,b.period,b.side,q,result.prices[b.period],b.bid_id)
        for b in blocks:
            a=result.accepted_bids.get(b.bid_id,0)
            assert min(abs(a),abs(a-1))<1e-6
            for p,q in zip(b.periods,b.quantities):
                recon[b.participant_id][p]+=a*q
                record(market,b.participant_id,p,'sell',a*q,result.prices[p],b.bid_id)
        for participant in PARTICIPANTS:
            pid=participant.participant_id; cumulative.setdefault(pid,{})
            for p in mt.periods:
                accepted=result.participant_positions.get(pid,{}).get(p,0)
                assert abs(accepted-recon[pid][p])<1e-6
                cumulative[pid][p]=cumulative[pid].get(p,0)+accepted
        balance=max(abs(sum(pos.get(p,0) for pos in result.participant_positions.values())) for p in mt.periods)
        cumulative_balance=max(abs(sum(pos.get(p,0) for pos in cumulative.values())) for p in mt.periods)
        cap_violation=max(max(-cumulative[u['participant_id']][p],
                      cumulative[u['participant_id']][p]-u['capacity_mw']*mt.mtu_hours,0)
                      for u in UNITS for p in mt.periods)
        assert balance<1e-3 and cumulative_balance<1e-3 and cap_violation<1e-3
        exposure=sum(abs(forecasts[market][pid][p]-cumulative[pid][p]) for pid in forecasts[market] for p in mt.periods)
        volume=sum(result.accepted_bids.get(b.bid_id,0) for b in bids if b.side=='buy')
        checks.append({'stage':market,'balance_mwh':balance,'cumulative_balance_mwh':cumulative_balance,
                       'capacity_violation_mwh':cap_violation,'target_gap_mwh':exposure})
        period_volumes={p:sum(result.accepted_bids.get(b.bid_id,0) for b in bids if b.side=='buy' and b.period==p) for p in mt.periods}
        active=[p for p in mt.periods if period_volumes[p]>1e-4]
        for p in mt.periods:
            rows.append({'stage':market,'period':p,'price':result.prices[p],'buy_volume_mwh':period_volumes[p],
                **{pid:pos.get(p,0) for pid,pos in cumulative.items()}})
        result.metadata.update(volume_mwh=volume,target_gap_mwh=exposure,pre_gap_mwh=pre_gap,
            active_periods=len(active),active_price_mean=statistics.mean(result.prices[p] for p in active) if active else None,
            bid_count=len(bids),block_count=len(blocks))
    metrics=[]
    for p in mt.periods:
        cash=sum(r['cash_eur'] for r in ledger if r['period']==p)
        assert abs(cash)<.20, ('cash balance',p,cash)
    for participant in PARTICIPANTS:
        pid=participant.participant_id
        for p in mt.periods:
            signed=sum(r['quantity_mwh']*(1 if r['side']=='sell' else -1) for r in ledger if r['participant']==pid and r['period']==p)
            assert abs(signed-cumulative[pid][p])<1e-6
        for stage in ('ALL','FM')+STAGES:
            trades=[r for r in ledger if r['participant']==pid and (stage=='ALL' or r['stage']==stage)]
            buys=[r for r in trades if r['side']=='buy']; sells=[r for r in trades if r['side']=='sell']
            bq=sum(r['quantity_mwh'] for r in buys); sq=sum(r['quantity_mwh'] for r in sells)
            spend=-sum(r['cash_eur'] for r in buys); revenue=sum(r['cash_eur'] for r in sells)
            focal=pid in forecasts['IDA3']
            metrics.append(dict(participant=pid,stage=stage,buy_mwh=bq,sell_mwh=sq,
                purchase_eur=spend,sales_eur=revenue,net_cash_eur=revenue-spend,
                buy_vwap=spend/bq if bq>1e-4 else None,sell_vwap=revenue/sq if sq>1e-4 else None,
                final_target_mwh=sum(abs(q) for q in forecasts['IDA3'][pid].values()) if focal else None,
                final_gap_mwh=sum(abs(forecasts['IDA3'][pid][p]-cumulative[pid][p]) for p in mt.periods) if focal else None))
    results['FM'].metadata.update(ledger=ledger,participant_metrics=metrics,
        max_cash_residual_eur=max(abs(sum(r['cash_eur'] for r in ledger if r['period']==p)) for p in mt.periods))
    elapsed=time.perf_counter()-started
    return results,rows,checks,elapsed,stage_times

def write_csv(name,rows):
    with (HERE/'results'/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def main():
    warnings.filterwarnings('ignore',category=DeprecationWarning)
    (HERE/'results').mkdir(exist_ok=True)
    summary=[]; traces=[]; verification=[]; ledger=[]; metrics=[]
    for name,dm,rm,rev in SCENARIOS:
        results,rows,checks,elapsed,_=run(30,dm,rm,rev)
        print(name,round(elapsed,3),flush=True)
        ledger.extend(dict(scenario=name,**r) for r in results['FM'].metadata['ledger'])
        metrics.extend(dict(scenario=name,**r) for r in results['FM'].metadata['participant_metrics'])
        for r in checks: r['max_cash_residual_eur']=results['FM'].metadata['max_cash_residual_eur']
        for stage in STAGES:
            r=results[stage]
            summary.append(dict(scenario=name,stage=stage,price_min=min(r.prices.values()),
                price_mean=statistics.mean(r.prices.values()),price_max=max(r.prices.values()),
                volume_mwh=r.metadata['volume_mwh'],target_gap_mwh=r.metadata['target_gap_mwh'],
                pre_gap_mwh=r.metadata['pre_gap_mwh'],active_periods=r.metadata['active_periods'],active_price_mean=r.metadata['active_price_mean'],
                social_welfare=r.social_welfare,rejected_blocks=len(r.metadata.get('rejected_paradoxical_blocks',[]))))
        traces.extend(dict(scenario=name,**r) for r in rows)
        verification.extend(dict(scenario=name,**r) for r in checks)
    no_revision=[r for r in summary if r['scenario']=='No revision']
    assert sum(r['volume_mwh'] for r in no_revision if r['stage']!='DAM')<1e-3
    assert no_revision[-1]['target_gap_mwh']<1e-3
    write_csv('trade_ledger.csv',ledger); write_csv('participant_metrics.csv',metrics)
    forecast_rows=[]
    contract_rows=[]
    for name,dm,rm,rev in SCENARIOS:
        forecasts,contracts=inputs(30,dm,rm,rev)
        forecast_rows.extend(dict(scenario=name,stage=stage,participant=pid,period=p,target_mwh=q)
                             for stage,profiles in forecasts.items() for pid,profile in profiles.items() for p,q in profile.items())
        contract_rows.extend(dict(scenario=name,contract=c.contract_id,seller=c.delivery_participant_id,
            buyer=c.offtake_participant_id,period=p,quantity_mwh=q,price_eur_mwh=c.price)
            for c in contracts for p,q in c.period_quantities.items())
    write_csv('forecasts.csv',forecast_rows); write_csv('contracts.csv',contract_rows)
    timings=[]
    for minutes in (60,30,15):
        run(minutes) # unrecorded warm-up
        for repeat in range(1,6):
            _,_,checks,elapsed,st=run(minutes)
            timings.append(dict(mtu_minutes=minutes,periods=1440//minutes,repeat=repeat,total_seconds=elapsed,**st))
    write_csv('scenario_summary.csv',summary); write_csv('positions_prices.csv',traces)
    write_csv('verification.csv',verification); write_csv('timings.csv',timings)
    cpu=platform.processor() or 'unavailable'
    if sys.platform=='win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            cpu=winreg.QueryValueEx(key,'ProcessorNameString')[0].strip()
    cbc=subprocess.run([pulp.PULP_CBC_CMD().path,'-stop'],capture_output=True,text=True).stdout
    cbc='\n'.join(line for line in cbc.splitlines() if not line.startswith('command line - '))
    environment=dict(python=sys.version,pulp=pulp.__version__,platform=platform.platform(),cpu=cpu,
        cbc=cbc,solver_settings='PULP_CBC_CMD defaults; sequential runs; one unrecorded warm-up and five repeats per resolution',
        input_sha256=hashlib.sha256((HERE/'case_inputs.json').read_bytes()).hexdigest(),
        workbook_sha256=DATA['workbook_sha256'],script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        timed_scope='FM, bid construction, four clears including pricing/rejection, trade-ledger accounting and consistency checks; excludes input construction and file export')
    (HERE/'results'/'environment.json').write_text(json.dumps(environment,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
