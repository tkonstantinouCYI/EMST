"""Compare both configuration representations through daily RTBM and settlement."""
import argparse,json,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from examples.configured_plant import build_market


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report=dict(passed=True,modes={})
    for minutes in (5,30):
        results={}
        for variant in ('reference','compact'):
            m=build_market();m.assets['ccgt'].parameters['configuration_formulation']=variant
            r=m.run(time_index=m.time_index('2026-11-05'),rtbm_interval_minutes=minutes,historical_rtbm_prices={t:[[60.]*6]*3 for t in range(48)})
            assert r.audit['passed'] and r.manifest['physical_security_feasible']
            assert all(v['reconciled'] for v in r.economics['participants'].values())
            assert sum(v['assets']['ccgt']['transition_cost_eur'] for v in r.rtbm)==250
            target=args.output/f'{variant}-{minutes}';r.save(target/'raw');r.save_readable(target)
            results[variant]=r
        a,b=results['reference'],results['compact']
        assert all(abs(x['milp_objective_eur']-y['milp_objective_eur'])<1e-3 for x,y in zip(a.isp_runs,b.isp_runs))
        assert a.dam==b.dam
        state_differences=sum(x['assets']['ccgt']['configuration_state']!=y['assets']['ccgt']['configuration_state'] for x,y in zip(a.rtbm,b.rtbm))
        power=max(abs(x['assets'][aid]['power_mw']-y['assets'][aid]['power_mw']) for x,y in zip(a.rtbm,b.rtbm) for aid in ('ccgt','backup'))
        prices=max(abs(x['price_eur_mwh']-y['price_eur_mwh']) for x,y in zip(a.rtbm,b.rtbm))
        settlement=max(abs(x['settlement_price_eur_mwh']-y['settlement_price_eur_mwh']) for x,y in zip(a.rtbm,b.rtbm))
        cash={}
        for variant,r in results.items():
            stages=r.to_readable(audience='operator')['simulation_summary']['days'][0]['market_stages']
            cash[variant]={owner:v['financial_position']['daily_total_cash_eur'] for owner,v in stages['settlement']['participants'].items()}
        delta={owner:str(Decimal(v)-Decimal(cash['reference'][owner])) for owner,v in cash['compact'].items()}
        assert state_differences==0 and power<1e-4,(state_differences,power)
        assert all(abs(Decimal(v))<Decimal('.01') for v in delta.values()),delta
        report['modes'][str(minutes)]=dict(ISP_objectives_match=True,DAM_match=True,configuration_mismatch_count=state_differences,
            maximum_dispatch_difference_mw=power,maximum_RTBM_price_difference_eur_mwh=prices,
            maximum_settlement_price_difference_eur_mwh=settlement,participant_cash_difference_eur=delta,
            executed_transition_cost_eur=250,all_stage_checks_passed=True,all_cash_reconciled=True,
            price_note='Reported separately: primal equivalence does not require unique dual prices')
    (args.output/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
