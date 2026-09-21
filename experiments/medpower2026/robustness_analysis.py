"""Independent diagnostics and arithmetic decomposition for the paper."""
import csv,json,warnings
from pathlib import Path
import pulp
from run_experiments import run,inputs,FOCAL,STAGES,SCENARIOS,write_csv
warnings.filterwarnings("ignore",category=DeprecationWarning)
HERE=Path(__file__).parent
auction=[];pm=[];gaps=[];blocks=[];decomp=[];policy_checks=[];unfilled=[]
variants=[("Baseline",1,1,1,.10,"max_volume_pro_rata"),("Low RES",1,.7,1,.10,"max_volume_pro_rata"),
 ("Markup 5%",1,1,1,.05,"max_volume_pro_rata"),("Markup 20%",1,1,1,.20,"max_volume_pro_rata"),
 ("CBC allocation",1,1,1,.10,"cbc"),("Pro rata only",1,1,1,.10,"pro_rata")]
base_metrics=None
for name,dm,rm,rev,markup,policy in variants:
 results,rows,checks,_,_=run(dm=dm,rm=rm,revision=rev,markup=markup,tie_policy=policy)
 forecasts,_=inputs(30,dm,rm,rev)
 records=results['FM'].metadata['ledger']
 for stage in STAGES:
  active=[r for r in rows if r['stage']==stage and r['buy_volume_mwh']>1e-4]
  q=sum(r['buy_volume_mwh'] for r in active)
  auction.append(dict(variant=name,stage=stage,vwap=sum(r['buy_volume_mwh']*r['price'] for r in active)/q if q else None,volume=q,
   bid_surplus=results[stage].social_welfare,extra_zero_surplus_mwh=results[stage].metadata.get('extra_zero_surplus_mwh',0)))
  for bid,a in results[stage].metadata.get('block_acceptances',{}).items():
   blocks.append(dict(variant=name,block=bid,acceptance=a,rejected=bid in results[stage].metadata['rejected_paradoxical_blocks']))
 for r in results['FM'].metadata['participant_metrics']:
  if r['stage']=='ALL':pm.append(dict(variant=name,**r))
 for pid in FOCAL:
  final=[r for r in rows if r['stage']=='IDA3']
  delta=[r[pid]-forecasts['IDA3'][pid][r['period']] for r in final]
  gaps.append(dict(variant=name,participant=pid,above_signed_target_mwh=sum(max(x,0) for x in delta),below_signed_target_mwh=sum(max(-x,0) for x in delta),absolute_gap_mwh=sum(abs(x) for x in delta)))
 if name=='Baseline':
  orders=results['IDA3'].metadata['simple_orders']
  for o in orders:
   remaining=o['quantity']-o['accepted']
   if o['participant'] not in FOCAL or remaining<1e-4:continue
   opposite=[x for x in orders if x['period']==o['period'] and x['side']!=o['side'] and x['quantity']-x['accepted']>1e-4]
   eligible=[x for x in opposite if (x['price']>=o['price'] if o['side']=='sell' else x['price']<=o['price'])]
   unfilled.append(dict(participant=o['participant'],period=o['period'],side=o['side'],remaining_mwh=remaining,offer_price=o['price'],remaining_opposite_mwh=sum(x['quantity']-x['accepted'] for x in opposite),eligible_opposite_mwh=sum(x['quantity']-x['accepted'] for x in eligible),reason='price mismatch' if opposite and not eligible else 'no remaining countervolume' if not opposite else 'other'))
  base_metrics={r['participant']:r for r in results['FM'].metadata['participant_metrics'] if r['stage']=='ALL'}
  reference=next(r['vwap'] for r in auction if r['variant']==name and r['stage']=='DAM')
  dam=results['DAM'].prices
  for pid in FOCAL:
   side='buy' if pid in FOCAL[:3] else 'sell'
   trades=[r for r in records if r['participant']==pid and r['side']==side]
   q=sum(r['quantity_mwh'] for r in trades)
   contract=sum(r['quantity_mwh']*(r['price_eur_mwh']-dam[r['period']]) for r in trades if r['stage']=='FM')/q
   timing=sum(r['quantity_mwh']*(dam[r['period']]-reference) for r in trades)/q
   intraday=sum(r['quantity_mwh']*(r['price_eur_mwh']-dam[r['period']]) for r in trades if r['stage'].startswith('IDA'))/q
   price=sum(r['quantity_mwh']*r['price_eur_mwh'] for r in trades)/q
   assert abs(price-reference-contract-timing-intraday)<1e-9
   decomp.append(dict(participant=pid,side=side,price=price,dam_reference=reference,contract=contract,timing=timing,intraday=intraday,total_difference=price-reference))
for name in ['CBC allocation','Pro rata only']:
 for r in pm:
  if r['variant']==name and r['participant'] in FOCAL:
   base=base_metrics[r['participant']]
   for side in ['buy','sell']:
    key=side+'_vwap'
    if r[key] is not None and base[key] is not None:
     policy_checks.append(dict(policy=name,participant=r['participant'],side=side,change_from_selected_policy=r[key]-base[key]))
# Exact reverse-input check on all seven scenarios, comparing allocations and prices.
order_checks=[]
for name,dm,rm,rev in SCENARIOS:
 a=run(dm=dm,rm=rm,revision=rev)[0];b=run(dm=dm,rm=rm,revision=rev,reverse=True)[0]
 largest=max(abs(a[s].accepted_bids[k]-b[s].accepted_bids[k]) for s in STAGES for k in a[s].accepted_bids)
 assert largest<1e-9
 order_checks.append(dict(scenario=name,max_reversed_input_allocation_change=largest))
# The stored dual is dW*/dRHS for buys - sells = RHS. Use an interior marginal seller.
def probe(rhs):
 p=pulp.LpProblem('dual_sign_probe',pulp.LpMaximize)
 buy=pulp.LpVariable('buy',0,5);sell=pulp.LpVariable('sell',0,10)
 p+=100*buy-40*sell;p+=buy-sell==rhs,'balance'
 assert p.solve(pulp.PULP_CBC_CMD(msg=False,mip=False))==1
 return pulp.value(p.objective),p.constraints['balance'].pi
w,dual=probe(0);eps=1e-3
finite=(probe(eps)[0]-probe(-eps)[0])/(2*eps)
assert abs(dual-40)<1e-7 and abs(finite-dual)<1e-6
for filename,rows in [('robustness_auctions.csv',auction),('robustness_participants.csv',pm),('signed_gaps.csv',gaps),('block_diagnostics.csv',blocks),('price_decomposition.csv',decomp),('tie_policy_sensitivity.csv',policy_checks),('input_order_checks.csv',order_checks),('unfilled_orders.csv',unfilled)]:write_csv(filename,rows)
(HERE/'results/dual_sign_test.json').write_text(json.dumps(dict(balance='buy - sell = RHS',dual=dual,central_difference=finite,rhs_step=eps),indent=2))
print('AUCTION CHECKS',json.dumps(auction,indent=2))
print('DECOMPOSITION',json.dumps(decomp,indent=2))
print('GAPS',json.dumps([r for r in gaps if r['variant']=='Baseline'],indent=2))
