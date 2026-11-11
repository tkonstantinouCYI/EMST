from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from emst.market import MarketResults


def example():
    at = '2026-11-05T00:00:00+02:00'
    asset = dict(energy_mwh=2., up_mwh=0., down_mwh=0., power_mw=4., reserve_mw={},
                 up_offer_eur_mwh=47.123, down_offer_eur_mwh=40., actual_mwh=2.)
    rows = [dict(period=0, at=at, hours=.5, assets={'secret_unit': asset},
                 reserve_prices={}, slack={}, price_eur_mwh=80.)]
    def entry(id, code, account, other, quantity, price, cash):
        return dict(id=id, component=code, account=account, counterparty=other,
                    quantity=str(quantity), price=str(price), cash_eur=str(cash), period='2026-11-05:30:000')
    ledger = [entry('forward:secret_contract:0', 'S01', 'participant:alice:private',
                    'participant:bob:private', 1, 50, 50),
              entry('dam:sell', 'S02', 'participant:alice:DAM', 'A-A', 1, 60, 60),
              entry('dam:buy', 'S02', 'participant:bob:DAM', 'A-A', -1, 60, -60)]
    rtbm = dict(rows[0], isp_run='initial', RES_delivery_mwh={}, actual_demand_mwh=2.,
                actual_RES_mwh=0., physical_imbalance_mwh=0., price_eur_mwh=80., settlement_price_eur_mwh=80.)
    return MarketResults(
        manifest={'day': '2026-11-05'}, orders=[dict(id='sell', participant='alice', asset='secret_unit',
            period=0, side='sell', energy_mwh=1., price_eur_mwh=47.123, block=None)],
        dam={'accepted_mwh': {'sell': 1.}, 'prices_eur_mwh': [60.]},
        isp_runs=[dict(id='initial', cutoff=at, rows=rows)], rtbm=[rtbm],
        meters=[dict(asset=a, participant=p, period=0, MSQ_mwh=2., actual_mwh=2., INSTQ_mwh=0., UNINSTQ_mwh=0.)
                for a, p in (('secret_unit', 'alice'), ('load', 'bob'))],
        ledger=ledger, ending_states={}, participant_results={}, audit={'passed': True},
        inputs=dict(time_index=[at], reserve_requirements_mw={}, actual_mw={'load': [4.]},
            participants=[dict(id=p, brp=None) for p in ('alice', 'bob')],
            assets=[dict(id=a, participant=p) for a, p in (('secret_unit', 'alice'), ('load', 'bob'))]))


class ReportingTests(unittest.TestCase):
    def test_financial_position_additive_stage_and_uplift_cash(self):
        result = example()
        for code, cash in (('S05', 6), ('S13', 4), ('S03', 8), ('S04', -1),
                           ('S14', -5), ('S16', -2), ('S17', 3)):
            result.ledger.append(dict(result.ledger[0], id=code, component=code,
                account='participant:alice:nonDAM', counterparty='operator', cash_eur=str(cash)))
        result.ledger.append(dict(result.ledger[0], id='transfer', component='S16',
            account='A-B', counterparty='UA-1', cash_eur='77'))
        result.ledger.append(dict(result.ledger[0], id='monthly', component='S23',
            period='2026-11', account='participant:alice:nonDAM', counterparty='A-E', cash_eur='-9'))
        stages = result.to_readable(audience='participant', participant='alice')['simulation_summary']['days'][0]['market_stages']
        position = stages['settlement']['participants']['alice']['financial_position']
        row = position['intervals'][0]
        self.assertEqual(row['forward_market_cash_eur'], '50')
        self.assertEqual(row['day_ahead_market_cash_eur'], '60')
        self.assertEqual(row['isp_cash_eur'], '10')
        self.assertEqual(row['rtbm_cash_eur'], '8')
        self.assertEqual(row['settlement_cash_eur'], '-6')
        self.assertEqual(row['ua1_cash_eur'], '-2')
        self.assertEqual(row['ua2_cash_eur'], '3')
        self.assertEqual(row['total_cash_eur'], '123')
        self.assertEqual(position['non_mtu_cash_eur'], '-9')
        self.assertEqual(position['daily_total_cash_eur'], '114')
        self.assertNotIn('financial_position', json.dumps(result.to_readable()))

    def test_isp_run_prices_and_nested_privacy(self):
        result = example()
        shared_run = result.to_readable()['simulation_summary']['days'][0]['market_stages']['isp']['runs'][0]
        self.assertNotIn('participants', shared_run)
        row = shared_run['summary']['intervals'][0]
        self.assertEqual(row['energy_shadow_price_eur_mwh'], 80.)
        self.assertIsNone(row['indicative_upward_offer_price_eur_mwh'])
        self.assertIsNone(row['indicative_downward_offer_price_eur_mwh'])
        offers = result.isp_runs[0]['rows'][0]['assets']
        offers['secret_unit'].update(up_mwh=1., up_offer_eur_mwh=40.)
        offers['second'] = dict(offers['secret_unit'], up_mwh=2., down_mwh=1.,
                                up_offer_eur_mwh=90., down_offer_eur_mwh=15.)
        offers['third'] = dict(offers['secret_unit'], up_mwh=0., down_mwh=2.,
                               up_offer_eur_mwh=999., down_offer_eur_mwh=-5.)
        stage = result.to_readable()['simulation_summary']['days'][0]['market_stages']['isp']
        self.assertEqual(list(stage), ['runs'])
        row = stage['runs'][0]['summary']['intervals'][0]
        self.assertEqual(row['indicative_upward_offer_price_eur_mwh'], 90.)
        self.assertEqual(row['indicative_downward_offer_price_eur_mwh'], -5.)

    def test_dam_signed_quantity_per_participant_and_mtu(self):
        result = example()
        for id, owner, side, quantity in (('buy', 'bob', 'buy', 1.),
                                          ('extra_sell', 'alice', 'sell', .5),
                                          ('extra_buy', 'alice', 'buy', .25)):
            result.orders.append(dict(id=id, participant=owner,
                asset='load' if owner == 'bob' else 'secret_unit', period=0,
                side=side, energy_mwh=quantity, price_eur_mwh=60., block=None))
            result.dam['accepted_mwh'][id] = quantity
        stage = result.to_readable(audience='operator')['simulation_summary']['days'][0]['market_stages']['day_ahead_market']
        self.assertEqual(stage['participants']['alice']['intervals'][0]['quantity_mwh'], 1.25)
        self.assertEqual(stage['participants']['bob']['intervals'][0]['quantity_mwh'], -1.)
        self.assertEqual(stage['participants']['bob']['quantity_convention'], 'positive_sell_negative_buy')

    def test_isp_reports_average_dispatch_and_rtbm_retains_endpoint(self):
        result = example()
        result.isp_runs[0]['rows'][0]['assets']['secret_unit']['end_mw'] = 5.
        stages = result.to_readable(audience='participant', participant='alice')['simulation_summary']['days'][0]['market_stages']
        isp = stages['isp']['runs'][0]['participants']['alice']['intervals'][0]
        self.assertEqual(isp['dispatch_power_mw'], 4.)
        self.assertNotIn('ending_power_mw', isp)
        rtbm = stages['rtbm']['participants']['alice']['intervals'][0]
        self.assertEqual(rtbm['ending_power_mw'], 5.)

    def test_forward_summary_intervals_reconcile_to_total(self):
        result = example()
        summary = result.to_readable()['simulation_summary']['days'][0]['market_stages']['forward_market']['summary']
        self.assertEqual(len(summary['intervals']), 1)
        self.assertEqual(summary['intervals'][0]['contracted_energy_mwh'], 1.)
        self.assertEqual(sum(r['contracted_energy_mwh'] for r in summary['intervals']),
                         summary['contracted_energy_mwh'])
        self.assertEqual(summary['intervals'][0]['start'], '2026-11-05T00:00:00+02:00')

    def test_shared_allowlist_and_private_isolation(self):
        result = example()
        before = deepcopy(asdict(result))
        shared = json.dumps(result.to_readable())
        for value in ('alice', 'bob', 'secret_unit', 'secret_contract', '47.123'):
            self.assertNotIn(value, shared)
        private = result.to_readable(audience='participant', participant='bob')
        stages = private['simulation_summary']['days'][0]['market_stages']
        self.assertTrue(all(list(s['participants']) == ['bob'] for name, s in stages.items() if name != 'isp'))
        self.assertTrue(all(list(run['participants']) == ['bob'] for run in stages['isp']['runs']))
        self.assertEqual(stages['settlement']['participants']['bob']['total_participant_cash_eur'], '-110')
        self.assertEqual(asdict(result), before)
        self.assertEqual(stages['rtbm']['participants']['bob']['intervals'][0]['actual_consumption_mwh'], 2.)

    def test_zero_price_contract_and_invalid_audience(self):
        result = example()
        result.ledger[0].update(price='0', cash_eur='0')
        stages = result.to_readable(audience='participant', participant='bob')['simulation_summary']['days'][0]['market_stages']
        self.assertEqual(len(stages['forward_market']['participants']['bob']['intervals']), 1)
        with self.assertRaises(ValueError):
            result.to_readable(audience='participant', participant='unknown')
        with self.assertRaises(ValueError):
            result.to_readable(audience='shared', participant='alice')

    def test_separate_files_and_safe_participant_paths(self):
        result = example()
        parent = Path(__file__).resolve().parents[1] / 'tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            files = result.save_readable(folder)
            self.assertEqual(len(files['participants']), 2)
            self.assertEqual(json.loads(files['shared'].read_text())['audience'], 'shared')
            self.assertTrue(all(p.resolve().is_relative_to(Path(folder).resolve()) for p in files['participants'].values()))
