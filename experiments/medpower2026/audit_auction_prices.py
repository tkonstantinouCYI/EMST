"""Audit auction VWAPs and distinguish supplier shortfalls from other gaps."""
import csv
from pathlib import Path
P=Path(__file__).parent/'results'
def read(n): return list(csv.DictReader((P/n).open()))
def write(n,rs):
 with (P/n).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
t=read('positions_prices.csv');ledger=read('trade_ledger.csv');forecasts=read('forecasts.csv')
f={(r['scenario'],r['participant'],r['period']):float(r['target_mwh']) for r in forecasts if r['stage']=='IDA3'}
vwaps=[];gaps=[]
for scenario in dict.fromkeys(r['scenario'] for r in t):
 for stage in ['DAM','IDA1','IDA2','IDA3']:
  rows=[r for r in t if r['scenario']==scenario and r['stage']==stage]
  active=[r for r in rows if float(r['buy_volume_mwh'])>1e-4]
  vol=sum(float(r['buy_volume_mwh']) for r in active)
  vwap=sum(float(r['price'])*float(r['buy_volume_mwh']) for r in active)/vol if vol else None
  if vwap is not None:
   periods={r['period'] for r in active}
   for side in ['buy','sell']:
    trades=[r for r in ledger if r['scenario']==scenario and r['stage']==stage and r['period'] in periods and r['side']==side]
    q=sum(float(r['quantity_mwh']) for r in trades)
    check=sum(float(r['quantity_mwh'])*float(r['price_eur_mwh']) for r in trades)/q
    assert abs(vwap-check)<1e-4,(scenario,stage,side,vwap,check)
  vwaps.append(dict(scenario=scenario,stage=stage,vwap_eur_mwh=vwap,active_volume_mwh=vol,active_mtus=len(active)))
 for pid in ['IND','COM','RES','PV_C','PV_A','W_C','W_A']:
  rows=[r for r in t if r['scenario']==scenario and r['stage']=='IDA3']
  differences=[float(r[pid])-f[scenario,pid,r['period']] for r in rows]
  supplier=pid in ['IND','COM','RES']
  gaps.append(dict(scenario=scenario,participant=pid,absolute_gap_mwh=sum(abs(x) for x in differences),supplier_shortfall_mwh=sum(max(0,x) for x in differences) if supplier else '',supplier_excess_purchase_mwh=sum(max(0,-x) for x in differences) if supplier else ''))
write('auction_vwaps.csv',vwaps);write('position_gap_audit.csv',gaps)
print('PASS: auction VWAPs checked against both ledger sides; supplier gaps separated.')
