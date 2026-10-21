"""Price samples and prior closing-limit cohorts do not imply account profits."""
from copy import deepcopy
import pytest

from app.services.market_sentiment import describe_sentiment, market_distribution
from app.services import strategy_limits
from test_market_ladder import Market, TODAY, DAYS, A, B, C, OTHER


def rows(market):
    result = market.rows()
    for row in result:
        row.update(price_continuity=True, corporate_action_coverage_known=True,
                   change_pct=(row['close']/10-1)*100)
    return result


@pytest.fixture
def market(monkeypatch):
    value=Market()
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    return value


def values(dimension):
    return {row['key']: row['value'] for row in dimension['metrics']}


def test_equal_weight_distribution_uses_raw_percentages_and_excludes_unknowns():
    sample=[]
    for i, change in enumerate((2, 4, -6, 0)):
        sample.append({'instrument_id':f'60000{i}.SH','date':TODAY,'change_pct':change,
                       'volume':100,'price_continuity':True,'corporate_action_coverage_known':True})
    sample.extend([{**sample[0],'instrument_id':'600090.SH','volume':0},
                   {**sample[0],'instrument_id':'600091.SH','corporate_action_coverage_known':False},
                   {**sample[0],'instrument_id':'600092.SH','price_continuity':False}])
    result=market_distribution(sample,TODAY); actual=values(result)
    assert actual['equal_weight_return_pct']==0
    assert actual['median_return_pct']==1
    assert actual['up_ratio_pct']==50
    assert actual['up_5pct_count']==0 and actual['down_5pct_count']==1
    assert result['sample_count']==4 and result['observed_count']==7 and result['unknown_count']==3
    assert result['state']=='insufficient'


def test_yesterday_cohort_is_verified_then_current_returns_and_promotion_are_independent(market):
    # Previous pool is B + OTHER. B falls 10%, OTHER rises 10% and remains limit-up.
    market.history[B][-1]['close']=9
    market.history[OTHER][-1]['close']=11
    market.ref['_supplement']['annual_pools'][TODAY]['items'][-1]['last_price']=11
    result=describe_sentiment(market,market.ref,rows(market),TODAY)
    short=result['short_term']; metrics=values(short)
    assert short['previous_date']==DAYS[-2]
    assert short['observed_count']==short['sample_count']==2
    assert metrics['previous_limit_up_avg_return_pct']==pytest.approx(0)
    assert metrics['promotion_rate_pct']==50
    assert metrics['promoted_count']==1 and metrics['failed_count']==1
    assert short['cohort_source_complete'] is True
    assert len(market.history_calls)==1
    assert market.history_calls[0][1:]==(DAYS[-2],TODAY)
    assert '账户' in result['notice']


@pytest.mark.parametrize('mode', ['missing', 'suspended', 'duplicate', 'source_partial', 'conflict'])
def test_incomplete_or_conflicting_evidence_never_becomes_failed_promotion(market,mode):
    if mode=='missing':market.history[B].pop()
    elif mode=='suspended':market.history[B][-1]['volume']=0
    elif mode=='duplicate':market.history[B].append(dict(market.history[B][-1]))
    elif mode=='source_partial':market.ref['_supplement']['annual_pools'][TODAY].update(complete=False,pagination_complete=False)
    else:
        market.ref['_supplement']['annual_pools'][TODAY]['items'].append({'instrument_id':B,'date':TODAY,'last_price':999})
    result=describe_sentiment(market,market.ref,rows(market),TODAY)['short_term']
    assert values(result)['promotion_rate_pct'] is None
    assert values(result)['failed_count']==0
    assert result['promotion_unknown_count']>=1


def test_incomplete_previous_pool_does_not_claim_complete_cohort_even_when_returns_known(market):
    market.ref['_supplement']['annual_pools'][DAYS[-2]].update(complete=False,pagination_complete=False)
    short=describe_sentiment(market,market.ref,rows(market),TODAY)['short_term']
    assert short['sample_count']==2
    assert short['cohort_source_complete'] is False
    assert short['state']=='insufficient'
    assert values(short)['promotion_rate_pct'] is None


def test_action_or_price_disagreement_does_not_enter_cohort_return_mean(market):
    current=rows(market)
    next(row for row in current if row['instrument_id']==B)['price_continuity']=False
    other=next(row for row in current if row['instrument_id']==OTHER)
    other['change_pct']=999
    short=describe_sentiment(market,market.ref,current,TODAY)['short_term']
    assert short['sample_count']==0 and short['unknown_count']==2
    assert values(short)['previous_limit_up_avg_return_pct'] is None
    # Raw closing-event proof remains independent of ex-right return comparability.
    assert values(short)['promoted_count']==1


def test_asof_has_no_future_prices_or_events_and_empty_samples_are_unknown(market):
    before=describe_sentiment(market,market.ref,rows(market),TODAY)
    for history in market.history.values():history.append({**history[-1],'date':'2099-01-01','close':999})
    market.ref['_supplement']['annual_pools']['2099-01-01']={'items':[{'instrument_id':B,'last_price':999}]}
    # Preserve the actual observation rows; future rows cannot enter the query.
    current=[{**history[-2],'price_continuity':True,'corporate_action_coverage_known':True,'change_pct':0} for history in market.history.values()]
    assert describe_sentiment(market,market.ref,current,TODAY)==before
    empty=market_distribution([],TODAY)
    assert empty['coverage_pct'] is None
    assert all(item['value'] is None for item in empty['metrics'])


@pytest.mark.parametrize('mode', ['raw_only', 'rejected', 'quarantine', 'ambiguous', 'incomplete_flag'])
def test_generic_absence_is_not_proof_of_a_complete_previous_cohort(market,mode):
    from test_market_ladder import X
    previous=market.ref['_supplement']['annual_pools'][DAYS[-2]]
    if mode=='raw_only':previous['raw_codes'].append(X[:6])
    elif mode=='rejected':
        previous['raw_codes'].append(X[:6]);previous['rejected_codes']=[X[:6]]
    elif mode=='quarantine':previous['quarantine']=[{'record':{'thscode':X},'reason':'unverified'}]
    elif mode=='ambiguous':previous['ambiguous_code_rows']=1
    else:previous['complete']=False
    # Granular default absences may still prove ordinary stocks not limit-up;
    # that useful fact cannot prove the entire positive cohort was accepted.
    short=describe_sentiment(market,market.ref,rows(market),TODAY)['short_term']
    assert short['cohort_source_complete'] is False
    assert values(short)['promotion_rate_pct'] is None
    assert short['state']=='insufficient'
    assert short['cohort_candidate_count']==short['observed_count']+short['previous_unverified_count']
