from emst.core.bids import Bid, BlockBid
from tie_policy import CaseStudySolver
import pytest

def test_equal_offers_receive_proportional_allocation():
    bids=[Bid('b','buyer','IA',1,'buy',5,100),Bid('s1','a','IA',1,'sell',3,40),Bid('s2','b','IA',1,'sell',9,40)]
    r=CaseStudySolver().clear(market_name='IA',periods=(1,),bids=bids)
    assert r['accepted_bids']['s1']==pytest.approx(1.25)
    assert r['accepted_bids']['s2']==pytest.approx(3.75)
    assert r['social_welfare']==pytest.approx(300)

def test_zero_surplus_trade_completed_without_price_change():
    bids=[Bid('b','buyer','IA',1,'buy',5,40),Bid('s','seller','IA',1,'sell',9,40)]
    r=CaseStudySolver().clear(market_name='IA',periods=(1,),bids=bids)
    assert r['accepted_bids']['b']==pytest.approx(5)
    assert r['accepted_bids']['s']==pytest.approx(5)
    # No-trade base solutions have a placeholder price. Completed trades need an economic price.
    assert r['prices'][1]==pytest.approx(40)
    assert r['social_welfare']==pytest.approx(0)

def test_nonmatching_prices_do_not_create_trade():
    bids=[Bid('b','buyer','IA',1,'buy',5,39),Bid('s','seller','IA',1,'sell',9,40)]
    r=CaseStudySolver().clear(market_name='IA',periods=(1,),bids=bids)
    assert sum(r['accepted_bids'].values())==pytest.approx(0)

def test_fractional_blocks_explicitly_outside_policy_scope():
    b=BlockBid('block','seller','DAM',(1,),(10,),(40,),acceptance_binary=False)
    with pytest.raises(ValueError):CaseStudySolver().clear(market_name='DAM',periods=(1,),bids=[],block_bids=[b])
