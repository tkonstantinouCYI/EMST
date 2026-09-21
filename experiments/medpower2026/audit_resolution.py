"""Compare baseline economic outputs across MTU resolutions without replacing timings."""
import csv
import warnings
from pathlib import Path
from run_experiments import run, inputs, FOCAL
warnings.filterwarnings('ignore', category=DeprecationWarning)
HERE=Path(__file__).parent
auction=[]
participants=[]
for minutes in (60,30,15):
 results, rows, checks, _, _ = run(minutes)
 for stage in ('DAM','IDA1','IDA2','IDA3'):
  active=[r for r in rows if r['stage']==stage and r['buy_volume_mwh']>1e-4]
  volume=sum(r['buy_volume_mwh'] for r in active)
  auction.append(dict(mtu_minutes=minutes,stage=stage,volume_mwh=volume,
   vwap_eur_mwh=sum(r['buy_volume_mwh']*r['price'] for r in active)/volume if volume else None,
   bid_surplus_eur=results[stage].social_welfare,
   rejected_blocks=len(results[stage].metadata.get('rejected_paradoxical_blocks',[]))))
 for r in results['FM'].metadata['participant_metrics']:
  if r['stage']=='ALL' and r['participant'] in FOCAL:
   participants.append(dict(mtu_minutes=minutes,**{k:r[k] for k in ('participant','buy_mwh','sell_mwh','buy_vwap','sell_vwap','final_gap_mwh')}))
for name,rows in [('resolution_auctions.csv',auction),('resolution_participants.csv',participants)]:
 with (HERE/'results'/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
for row in auction: print(row)
for minutes in (60,15):
 base={r['participant']:r for r in participants if r['mtu_minutes']==30}
 rows=[r for r in participants if r['mtu_minutes']==minutes]
 for key in ('buy_vwap','sell_vwap','buy_mwh','sell_mwh','final_gap_mwh'):
  print(minutes,key,max(abs(r[key]-base[r['participant']][key]) for r in rows if r[key] is not None and base[r['participant']][key] is not None))
