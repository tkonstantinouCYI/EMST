"""Independent CSV audit: energy, money, VWAP and matched-resource assumptions."""
import csv
from collections import defaultdict
from pathlib import Path
P=Path(__file__).with_name('results')
def read(name): return list(csv.DictReader((P/name).open()))
ledger=read('trade_ledger.csv'); metrics=read('participant_metrics.csv')
money=defaultdict(float); energy=defaultdict(float)
for r in ledger:
    q=float(r['quantity_mwh']); price=float(r['price_eur_mwh']); cash=float(r['cash_eur'])
    sign=1 if r['side']=='sell' else -1
    assert abs(cash-sign*q*price)<1e-8
    key=(r['scenario'],r['stage'],r['period'])
    money[key]+=cash; energy[key]+=sign*q
assert max(map(abs,money.values()))<.01
assert max(map(abs,energy.values()))<1e-4
for r in metrics:
    rows=[x for x in ledger if x['scenario']==r['scenario'] and x['participant']==r['participant'] and (r['stage']=='ALL' or x['stage']==r['stage'])]
    for side,quantity,cost,vwap in [('buy','buy_mwh','purchase_eur','buy_vwap'),('sell','sell_mwh','sales_eur','sell_vwap')]:
        trades=[x for x in rows if x['side']==side]
        q=sum(float(x['quantity_mwh']) for x in trades)
        c=sum(float(x['quantity_mwh'])*float(x['price_eur_mwh']) for x in trades)
        assert abs(q-float(r[quantity]))<1e-8 and abs(c-float(r[cost]))<1e-6
        if q>1e-4: assert abs(c/q-float(r[vwap]))<1e-8
forecasts={(r['scenario'],r['stage'],r['participant'],r['period']):float(r['target_mwh']) for r in read('forecasts.csv')}
for (scenario,stage,pid,period),q in forecasts.items():
    if pid in ('PV_C','W_C'):
        assert q==forecasts[scenario,stage,{'PV_C':'PV_A','W_C':'W_A'}[pid],period]
contracts=read('contracts.csv')
fixed={}
for r in contracts:
    key=r['contract'],r['period']; value=float(r['quantity_mwh']),float(r['price_eur_mwh'])
    assert fixed.setdefault(key,value)==value
print(f'PASS: {len(ledger)} trade rows; {len(metrics)} participant/stage metrics; matched RES and fixed contracts.')
print('Maximum stage/MTU cash residual EUR:',max(map(abs,money.values())))
