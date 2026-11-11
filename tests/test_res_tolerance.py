import unittest
from emst.rulesets import Ruleset
from emst.settlement import Ledger, res_bridge, settle_energy
from emst.model import Registration
from emst.scenario import synthetic_day


class RESToleranceTests(unittest.TestCase):
    def test_example_with_zero_and_nonzero_tolerance(self):
        period = synthetic_day()['periods'][0]
        reg = Registration('res','solar','brp','member','meter','production','HV',tolerance=.25)
        for tol, inst, uninst in ((0.,-5.,-2.),(.02,-5.1,-1.9)):
            policy = {'res_tolerance': {'commercial':tol,'grant_supported':0.}}
            direct = res_bridge(10,5,3,policy)
            self.assertAlmostEqual(direct['INSTQ'],inst)
            self.assertAlmostEqual(direct['UNINSTQ'],uninst)
            rtus = [dict(period=0,price_eur_mwh=80,rows=[],res_curtail_mwh=5/6,
                entity_meters={'res':.5},entity_down={'res':5/6},
                res_offer_rows=[dict(entity='res',up_mwh=0,down_mwh=5/6,
                    up_offer_eur_mwh=100,down_offer_eur_mwh=40,
                    price_forming=True,reason='RES_down_offer')]) for _ in range(6)]
            ledger = Ledger()
            bridges,_ = settle_energy(ledger,[period],rtus,{'res':reg},{'res':[10]},
                {0:[[80]*6 for _ in range(3)]},settlement_rules=policy)
            self.assertAlmostEqual(bridges[0]['INSTQ'],inst)
            self.assertAlmostEqual(bridges[0]['UNINSTQ'],uninst)
            cash = {e.component:float(e.cash_eur) for e in ledger.entries}
            self.assertAlmostEqual(cash['S03'],inst*80)
            self.assertAlmostEqual(cash['S04'],uninst*80)

    def test_policy_validation_and_grant_category(self):
        self.assertEqual(Ruleset()['settlement']['res_tolerance'],{'commercial':0.,'grant_supported':0.})
        for value in (-.01,1,float('nan'),True):
            with self.assertRaises(ValueError):
                Ruleset({'settlement':{'res_tolerance':{'commercial':value}}})
        bridge = res_bridge(10,5,3,{'res_tolerance':{'commercial':.02,'grant_supported':0.}},True)
        self.assertEqual(bridge['INSTQ'],-5)
