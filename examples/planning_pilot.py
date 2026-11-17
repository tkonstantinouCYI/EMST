"""Paired isolated-market interface checks; not an investment optimization."""
import argparse,json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from library_day import build_market
from emst import StorageState,Strategy


def candidate(power_mw):
    m,index,reserves,history=build_market(storage_energy=True,storage_capacity=True)
    for pid in ('producer_A','producer_B','storage_owner'):
        m.participants[pid]=replace(m.participants[pid],economic_owner='incumbent')
    if power_mw==0:del m.assets['battery']
    else:
        a=m.assets['battery'];factor=power_mw/10
        a.parameters.update(power_mw=power_mw,capacity_mwh=4*power_mw)
        a.initial_state=StorageState(0,2*power_mw)
        a.strategy=Strategy(a.strategy.name,{k:([v*factor for v in values] if k in ('buy_mwh','sell_mwh') else values)
            for k,values in a.strategy.parameters.items()})
    return m,index,reserves,history


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('output/planning-pilot'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    rows=[];baseline=None
    for label,power,bias in (('baseline',0,0),('battery_10',10,0),('battery_20',20,0),('biased_forecast',0,20),('baseline_repeat',0,0)):
        m,index,reserves,history=candidate(power)
        tso=None
        if bias:
            tso=dict(demand=[sum(a.forecast[t] for a in m.assets.values() if a.technology=='supplier')+bias for t in range(48)],
                res=list(m.assets['solar_portfolio'].forecast))
        result=m.run(time_index=index,reserve_requirements=reserves,historical_rtbm_prices=history,
            must_run_requirement_mw=[180.]*48,system_forecasts=tso,
            storage_terminal_soc_mwh={'battery':2*power} if power else None)
        if not result.audit['passed'] or not all(p['reconciled'] for p in result.economics['participants'].values()):
            raise RuntimeError('Pilot physical/cash reconciliation failed')
        if power and abs(result.ending_states['battery']['soc_mwh']-2*power)>1e-5:
            raise RuntimeError('Realized terminal inventory target failed')
        if bias and result.diagnostics['actual_balance']['actual_delivered_balance']:
            raise RuntimeError('Biased forecast failed to demonstrate actual residuals')
        cash={p:Decimal(v['participant_total_cash_eur']) for p,v in result.economics['participants'].items()}
        row=dict(case=label,power_mw=power,capacity_mwh=4*power,
            supplier_outflow_eur=str(-cash['supplier_T']-cash['supplier_D']),
            battery_cash_eur=str(cash['storage_owner']),physical_operating_cost_eur=result.economics['physical_operating_cost_eur'],
            actual_balance=result.diagnostics['actual_balance'],input_fingerprint=result.manifest['input_fingerprint'],
            smoothing_fallback_count=result.diagnostics['smoothing_fallback_count'])
        if label=='baseline':baseline=row
        if label=='baseline_repeat':
            if row['input_fingerprint']!=baseline['input_fingerprint'] or abs(Decimal(row['supplier_outflow_eur'])-Decimal(baseline['supplier_outflow_eur']))>Decimal('0.0001'):
                raise RuntimeError('Candidate order/state isolation failed')
        directory=args.output/label
        result.save(directory/'raw');result.save_readable(directory)
        (directory/'evaluation.json').write_text(json.dumps(result.to_evaluation(),indent=2,allow_nan=False),encoding='utf-8')
        rows.append(row)
        print(label+': passed',flush=True)
    (args.output/'verification.json').write_text(json.dumps(dict(passed=True,cases=rows,
        independent_initial_states=True,realized_storage_inventory='cyclic_when_present',
        load_loss_reliability='unassessed',annualized=False),indent=2),encoding='utf-8')


if __name__=='__main__':main()
