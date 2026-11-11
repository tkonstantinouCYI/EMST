from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from emst.accounting import finalise_accounts, post_tariffs
from emst.monthly import settle_month
from emst.settlement import Ledger


class PublicUpliftScopeTests(unittest.TestCase):
    def test_network_tariffs_and_penalties_create_no_excluded_uplifts(self):
        ledger = Ledger()
        tariffs = {v:dict(tuos_energy=10, tuos_power=2, duos_energy=3, duos_power=1)
                   for v in ('HV','MV','LV')}
        meters = [dict(id='meter', participant='supplier', period='2026-11-01:30:000',
                       energy_mwh=5, hours=.5, voltage='HV', purpose='consumption')]
        post_tariffs(ledger, meters, tariffs, include_levies=False)
        self.assertEqual({e.component for e in ledger.entries}, {'S14','S15'})
        ledger.post('penalty', 'S23', 'A-E', 'participant:generator:nonDAM',
                    1, 100, '2026-11', 'synthetic', 'test')
        finalise_accounts(ledger, '2026-11', {'supplier':5}, None, None,
                          {'UA-1':1}, uplift_scope='UA1_UA2_only')
        self.assertFalse({'S18','S19','S20','S21','S22'} & {e.component for e in ledger.entries})
        credits = [e for e in ledger.entries if e.id.startswith('UA1_penalty_credit')]
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].cash_eur, '100')

    def test_monthly_default_and_legacy_zero_keys(self):
        days = {date(2026,11,i).isoformat():SimpleNamespace(audit={'passed':True},
                manifest={'day':date(2026,11,i).isoformat()}) for i in range(1,31)}
        results = SimpleNamespace(days=days)
        with patch('emst.monthly._month_ledger', return_value=(Ledger(),[],[],{})) as build:
            closed = settle_month(results, '2026-11')
            self.assertEqual(build.call_args.args[2]['penalty_shares'], {'UA-1':1})
            self.assertEqual(closed['excluded_uplifts'], ['UA-3','UA-4','UA-5','UA-6','UA-7'])
            settle_month(results, '2026-11', penalty_shares={'UA-1':1,'UA-6':0,'UA-7':0})
            self.assertEqual(build.call_args.args[2]['penalty_shares'], {'UA-1':1})
            with self.assertRaises(ValueError):
                settle_month(results, '2026-11', penalty_shares={'UA-1':.5,'UA-6':.5})
