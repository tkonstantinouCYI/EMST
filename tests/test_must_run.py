from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, localcontext
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from emst import Market, ThermalState, Forward
from emst.must_run import clear_auction
from emst.optimization import highspy

HISTORY={t:[[80.]*6 for _ in range(3)] for t in range(48)}
ROOT=Path(__file__).resolve().parents[1]/'tmp'
ROOT.mkdir(exist_ok=True)


def market():
    m=Market()
    for p in ('producer','supplier_A','supplier_B'):m.add_participant(p)
    m.add_thermal('unit','producer',capacity_mw=100,minimum_mw=20,marginal_cost=60,
        initial_state=ThermalState('dispatch',60,480),
        must_run_participation=True,must_run_offer_eur_per_mtu=11)
    m.add_supplier('a','supplier_A',demand_forecast=[36]*48,actual_consumption=[37]*48)
    m.add_supplier('b','supplier_B',demand_forecast=[24]*48,actual_consumption=[25]*48)
    m.add_forward(Forward('ordinary','producer','supplier_A','unit',tuple([5.]*48),70,buyer_entity='a'))
    return m


class MustRunTests(unittest.TestCase):
    def test_merit_order_uses_minimum_mw_and_pay_as_bid(self):
        m=market()
        m.add_participant('other')
        m.add_thermal('other','other',capacity_mw=1000,minimum_mw=10,marginal_cost=1,
            initial_state=ThermalState('dispatch',10,480),must_run_participation=True,
            must_run_offer_eur_per_mtu=2)
        r=clear_auction(m,{'2026-11-05':[10,20,30,0]*12})['2026-11-05']
        self.assertEqual([x['service_cost_eur'] for x in r['intervals'][:4]],[2,11,13,0])
        self.assertEqual([x['awarded_mw'] for x in r['intervals'][:4]],[10,20,30,0])
        with self.assertRaisesRegex(ValueError,'eligible minimum'):
            clear_auction(m,{'2026-11-05':[31]*48})
        for v in ([-1]*48,[float('nan')]*48,[20]*47):
            with self.assertRaises(ValueError):clear_auction(m,{'2026-11-05':v})

    @unittest.skipUnless(highspy is not None,'Integration requires HiGHS')
    def test_daily_all_stages_and_private_reporting(self):
        m=market()
        r=m.run(time_index=m.time_index('2026-11-05'),must_run_requirement_mw=[20]*48,
            historical_rtbm_prices=HISTORY)
        self.assertTrue(r.audit['passed'],r.audit)
        self.assertEqual(len(r.isp_runs),2)
        self.assertEqual(len(r.rtbm),288)
        for meter in r.meters:
            self.assertAlmostEqual(meter['MSQ_mwh'],{'unit':30,'a':18,'b':12}[meter['asset']])
        # 10 MWh inherent energy plus 5 MWh ordinary forward leaves 15 MWh DAM.
        self.assertAlmostEqual(r.dam['accepted_mwh']['unit:sell:0'],15)
        cash={p:sum((Decimal(e['cash_eur']) for e in r.ledger if e['component'] in ('S24','S26') and e['account']==f'participant:{p}:must_run'),Decimal(0)) for p in m.participants}
        self.assertEqual(cash,{'producer':Decimal(528),'supplier_A':Decimal('-316.8'),'supplier_B':Decimal('-211.2')})
        self.assertTrue(all(Decimal(e['cash_eur'])==0 for e in r.ledger if e['component']=='S25'))
        shared=r.to_readable()['simulation_summary']['days'][0]['market_stages']['must_run_auction']
        self.assertNotIn('participants',shared)
        self.assertNotIn('producer',json.dumps(shared))
        private=r.to_readable(audience='participant',participant='supplier_A')
        stages=private['simulation_summary']['days'][0]['market_stages']
        self.assertEqual(set(stages['must_run_auction']['participants']),{'supplier_A'})
        position=stages['settlement']['participants']['supplier_A']['financial_position']
        self.assertEqual(Decimal(position['totals']['must_run_cash_eur']),cash['supplier_A'])
        with localcontext() as context:
            context.prec=80
            self.assertEqual(Decimal(position['daily_total_cash_eur']),sum((Decimal(e['cash_eur'])*(int(e['account'].startswith('participant:supplier_A:'))-int(e['counterparty'].startswith('participant:supplier_A:'))) for e in r.ledger),Decimal(0)))

    @unittest.skipUnless(highspy is not None,'Integration requires HiGHS')
    def test_monthly_auction_once_and_resume_matches(self):
        scenarios={f'2026-11-{d:02}':dict(forecasts={'a':[36]*48,'b':[24]*48},
            actuals={'a':[37]*48,'b':[25]*48},historical_rtbm_prices=HISTORY,
            must_run_requirement_mw=[20]*48) for d in range(1,31)}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root=Path(directory)
            with patch('emst.must_run.clear_auction',wraps=clear_auction) as solve:
                continuous=market().run_period(start='2026-11-01',end='2026-11-02',scenario=scenarios,checkpoint_directory=root/'continuous')
                self.assertEqual(solve.call_count,1)
                self.assertEqual(len(solve.call_args.args[1]),30)
            first=market().run_period(start='2026-11-01',end='2026-11-01',scenario=scenarios,checkpoint_directory=root/'split')
            with patch('emst.must_run.clear_auction',side_effect=AssertionError('Must not reauction on resume')):
                resumed=market().run_period(start='2026-11-02',end='2026-11-02',scenario=scenarios,
                    checkpoint_directory=root/'split',resume_from=first.checkpoint)
            for day in continuous.days:
                self.assertEqual(json.loads(json.dumps(asdict(continuous.days[day]))),json.loads(json.dumps(asdict(resumed.days[day]))))
            saved=json.loads(first.checkpoint.read_text())['state']['must_run_auctions']['2026-11']
            self.assertEqual(len(saved['awards']),30)
            self.assertEqual(saved['supplier_shares'],{'supplier_A':.6,'supplier_B':.4})

    @unittest.skipUnless(highspy is not None,'Integration requires HiGHS')
    def test_daily_frequency_explicit_shares_and_zero_requirement(self):
        data={'2026-11-05':dict(forecasts={'a':[36]*48,'b':[24]*48},
            actuals={'a':[37]*48,'b':[25]*48},historical_rtbm_prices=HISTORY,
            must_run_requirement_mw=[0]*24+[20]*24,
            must_run_supplier_shares={'supplier_A':.5,'supplier_B':.5})}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            r=market().run_period(start='2026-11-05',end='2026-11-05',scenario=data,
                checkpoint_directory=directory,must_run_auction_frequency='daily').days['2026-11-05']
        self.assertTrue(r.audit['passed'])
        self.assertEqual(r.inputs['must_run']['frequency'],'daily')
        self.assertEqual([x['awarded_mw'] for x in r.inputs['must_run']['intervals']],[0]*24+[20]*24)
        charges=[Decimal(e['cash_eur']) for e in r.ledger if e['component']=='S26' and e['account']=='participant:supplier_A:must_run']
        self.assertEqual(charges,[Decimal(0)]*24+[Decimal('-5.5')]*24)

    def test_partial_month_requires_daily_frequency(self):
        scenario={'2026-11-05':dict(forecasts={'a':[36]*48,'b':[24]*48},actuals={'a':[37]*48,'b':[25]*48},
            historical_rtbm_prices=HISTORY,must_run_requirement_mw=[20]*48)}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            with self.assertRaisesRegex(ValueError,'complete month'):
                market().run_period(start='2026-11-05',end='2026-11-05',scenario=scenario,checkpoint_directory=directory)
        for shares in ({'supplier_A':1},{'supplier_A':.8,'supplier_B':.8}):
            with self.assertRaisesRegex(ValueError,'shares'):
                m=market();m.run(time_index=m.time_index('2026-11-05'),must_run_requirement_mw=[20]*48,must_run_supplier_shares=shares)
