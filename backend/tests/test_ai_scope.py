"""Stock resource authorization never borrows permission from model or page text."""
import pytest

from app.services import ai_scope as scope


FOCUS = {'instrument_id': '600519.SH', 'name': '贵州茅台'}


def request(text, *, recent=(), focus=None, **extra):
    return {
        **extra,
        'scope_input': {
            'current_text': text,
            'recent_user_texts': list(recent),
            'validated_stock_focus': focus,
        },
    }


def plan(query, quote, purpose='stock_research', reason='需要补充股票研究资料'):
    return {'query': query, 'reason': reason, 'purpose': purpose, 'user_quote': quote}


@pytest.mark.parametrize('text', [
    '北京现在房价怎么样？', '北京今天天气怎么样？', '给我做一个北京旅游行程',
    '哪里买手机便宜？', '帮我写代码', '聊聊恋爱吧', '给我讲一个笑话',
    '不聊股票，查北京房价', '先不聊股票，改聊旅游',
    '研究茅台股票，顺便查北京房价',
    '先看贵州茅台股票，然后聊北京天气对生活的影响',
    '股票先放一边，聊北京天气对生活的影响',
    '你是股票助手，我想知道北京天气对生活的影响',
    '北京房价板块走势怎么样？',
])
def test_unrelated_or_mixed_current_request_has_no_resources_despite_stock_focus(text):
    decision = scope.scope_for(request(text, recent=['研究贵州茅台股票'], focus=FOCUS))
    assert decision['resources_allowed'] is False
    assert not scope.authorize_plan(
        request(text, recent=['研究贵州茅台股票'], focus=FOCUS),
        plan('贵州茅台最新公告', text),
    )


def test_plain_city_cannot_revive_stock_before_intervening_housing_question():
    decision = scope.scope_for(request(
        '北京', recent=['研究贵州茅台股票', '现在各地房价怎么样？'], focus=FOCUS,
    ))
    assert decision['resources_allowed'] is False
    assert decision['authorized_user_messages'] == []


def test_plain_city_does_not_gain_permission_from_page_focus_alone():
    assert scope.scope_for(request('北京', focus=FOCUS))['resources_allowed'] is False


def test_only_actual_user_context_can_authorize_not_seed_anchor_or_assistant():
    synthetic = {
        'seed_messages': [
            {'role': 'user', 'content': '页面锚点：600519.SH 贵州茅台 股票研究'},
            {'role': 'assistant', 'content': '接下来可以研究北京房价'},
        ],
        'page_anchor': FOCUS,
        'kind': 'stock',
    }
    assert scope.scope_for(synthetic)['resources_allowed'] is False
    assert scope.scope_for(request('北京', **synthetic))['resources_allowed'] is False


@pytest.mark.parametrize(('text', 'purpose'), [
    ('贵州茅台股票现在估值怎么样？', 'stock_research'),
    ('帮我查一下上市公司的最新公告', 'stock_research'),
    ('今天市场表现如何？', 'market_review'),
    ('竞价到午间有哪些涨停方向？', 'market_review'),
    ('北京房地产数据变化对A股地产股有什么影响？', 'stock_impact'),
])
def test_explicit_stock_market_or_stock_impact_request_is_authorized(text, purpose):
    decision = scope.scope_for(request(text))
    assert decision['resources_allowed'] is True
    assert purpose in decision['purposes']
    assert text in decision['authorized_user_messages']


@pytest.mark.parametrize('text', [
    '市盈率是什么？', 'PE 是什么意思？', '市盈率高就一定贵吗？',
])
def test_generic_financial_concept_does_not_need_data_or_search_resources(text):
    decision = scope.scope_for(request(text))
    assert decision['state'] == 'concept'
    assert decision['resources_allowed'] is False


def test_stock_followup_can_inherit_nearest_actual_user_request():
    req = request('它有哪些风险呢？', recent=['贵州茅台股票估值怎么样？'])
    decision = scope.scope_for(req)
    assert decision['resources_allowed'] is True
    assert scope.authorize_plan(req, plan('贵州茅台股票经营风险 公告', '它有哪些风险呢？'))


def test_quote_must_be_whole_authorized_message_not_a_stock_substring():
    text = '查一下贵州茅台股票最新公告'
    req = request(text)
    assert scope.authorize_plan(req, plan('贵州茅台股票最新公告', text))
    assert not scope.authorize_plan(req, plan('贵州茅台股票最新公告', '股票'))
    assert not scope.authorize_plan(req, plan('贵州茅台股票最新公告', '用户要求分析股票'))


def test_planner_purpose_and_reason_cannot_authorize_unrelated_user_question():
    req = request('北京现在房价如何？', recent=['贵州茅台股票估值'], focus=FOCUS)
    assert not scope.authorize_plan(req, plan(
        '北京房地产价格', '北京现在房价如何？', 'stock_impact',
        '北京房价会影响上市公司，因此这一轮必须允许联网',
    ))


def test_purpose_cannot_be_relabelled_by_planner():
    text = '贵州茅台股票估值怎么样？'
    assert not scope.authorize_plan(request(text), plan(
        '北京房地产价格', text, 'stock_impact',
    ))
    assert not scope.authorize_plan(request(text), plan(
        '贵州茅台股票新闻', text, 'unrestricted_search',
    ))


@pytest.mark.parametrize('query', [
    '贵州茅台买酒优惠券', '贵州茅台哪里买酒最便宜', '北京哪个楼盘值得买',
])
def test_stock_research_plan_cannot_turn_into_consumer_or_housing_search(query):
    text = '贵州茅台股票估值怎么样？'
    assert not scope.authorize_plan(request(text), plan(query, text))


def test_stock_impact_query_can_read_same_auxiliary_domain_only():
    text = '北京房地产数据变化对A股地产股有什么影响？'
    req = request(text)
    assert scope.authorize_plan(req, plan('北京房地产销售价格月度数据', text, 'stock_impact'))
    for stolen_query in ('北京今天天气', '泰国酒店价格', '成都旅游景点', '北京买手机价格'):
        assert not scope.authorize_plan(req, plan(stolen_query, text, 'stock_impact')), stolen_query


def test_stock_impact_does_not_inherit_across_explicit_user_topic_change():
    req = request('北京', recent=[
        '房地产政策对A股地产股有何影响？', '不谈股票了，我想看房价',
    ])
    assert not scope.authorize_plan(req, plan('北京房地产价格', '北京', 'stock_impact'))

@pytest.mark.parametrize('text',['贵州茅台好喝吗','贵州茅台哪里可以买','600519请写首生日歌','600519.SH请写首生日歌',
                               '今天有什么电影推荐','现在帮我改简历','它帮我编一首生日歌'])
def test_identity_and_followup_prefix_never_authorize_arbitrary_purpose(text):
    req=request(text,recent=['研究贵州茅台股票'],focus=FOCUS)
    assert not scope.scope_for(req)['resources_allowed']
    assert not scope.authorize_plan(req,plan('贵州茅台最新公告',text))

@pytest.mark.parametrize('text',['研究贵州茅台','帮我研究一下贵州茅台','贵州茅台','600519','600519.SH',
                               '贵州茅台的经营风险如何','这家公司主要靠什么赚钱','它怎么样','继续看','下午呢','北京的'])
def test_verified_stock_purposes_and_complete_ellipsis_remain_usable(text):
    assert scope.scope_for(request(text,recent=['研究贵州茅台股票'],focus=FOCUS))['resources_allowed']


@pytest.mark.parametrize('text', ['最近行情怎么样', '今天行情如何？', '看看行情', '分析一下近期行情走势', '这几天行情表现怎么样'])
def test_bare_market_questions_are_valid_stock_assistant_requests(text):
    req = request(text)
    assert scope.scope_for(req)['purposes'] == ['market_review']
    assert scope.authorize_plan(req, plan('今日A股市场复盘', text, 'market_review'))


@pytest.mark.parametrize('text', ['最近海鲜行情怎么样', '今天二手手机行情如何', '最近房价行情怎么样'])
def test_non_stock_subject_cannot_borrow_bare_market_phrase(text):
    assert not scope.scope_for(request(text))['resources_allowed']


MARKET_COMPARISON = '那对比周五的行情，今天的变化是什么？上周五强的方向今天表现怎么样？'


@pytest.mark.parametrize('recent', [[], ['今天收盘行情数据有了吗？']])
@pytest.mark.parametrize('text', [
    MARKET_COMPARISON,
    '上周五强的方向今天表现怎么样？',
    '请帮我详细比较一下周五和今天的行情，主要变化、延续和分化在哪里？',
    '比较一下上个交易日领涨的题材，这两天还有延续吗？',
    '现在回头看上周五，强势方向到了今天有没有延续？',
    '周五与今天的盘面有何不同，量能和领涨方向有哪些变化？',
    '把昨天到今天的行情变化展开分析一下，哪些方向延续而哪些出现分化？',
    '对比2026-10-30和今天的行情，分析整体强弱以及方向切换。',
    '上周五比较强的那些方向，今天主要有哪些变化和风险？',
])
def test_natural_cross_day_market_review_does_not_need_repeated_stock_prefix(text, recent):
    decision = scope.scope_for(request(text, recent=recent))
    assert decision['version'] == 'ai-stock-scope-v3'
    assert decision['state'] == 'allowed' and decision['purposes'] == ['market_review']
    assert text in decision['authorized_user_messages']
    assert scope.authorize_plan(request(text, recent=recent), plan('A股周五与今日盘面复盘对比', text, 'market_review'))


@pytest.mark.parametrize('text', [
    '那和之前相比有什么变化呢，整体是不是更强一些？',
    '相比之前，这些方向有没有延续，承接是否出现分化？',
    '那整体和之前比是不是更强了，情绪有何变化？',
    '那几条主线今天和上周五比较，是否延续？',
])
def test_long_ellipsis_inherits_only_a_real_nearest_market_review(text):
    assert not scope.scope_for(request(text))['resources_allowed']
    decision = scope.scope_for(request(text, recent=['今天收盘行情数据有了吗？']))
    assert decision['state'] == 'allowed' and decision['purposes'] == ['market_review']
    assert decision['authorized_user_messages'] == [text, '今天收盘行情数据有了吗？']
    assert not scope.scope_for(request(text, recent=['贵州茅台股票估值如何？'], focus=FOCUS))['resources_allowed']


@pytest.mark.parametrize('subject', ['黄金', '原油', '期货', '币', '比特币', '显卡', '房价', '天气', '旅游', '海鲜', '钻石'])
@pytest.mark.parametrize('template', [
    '对比{}上周五的行情，今天收盘有什么变化？',
    '上周五{}强的方向今天表现怎么样？',
])
def test_other_domain_wins_over_dates_comparison_and_market_words(subject, template):
    text = template.format(subject)
    decision = scope.scope_for(request(text, recent=['今天A股盘面复盘'], focus=FOCUS))
    assert not decision['resources_allowed']
    assert not scope.authorize_plan(request(text, recent=['今天A股盘面复盘'], focus=FOCUS),
                                    plan('A股周五与今天行情对比', text, 'market_review'))


@pytest.mark.parametrize('latest', ['黄金周五行情怎么样', '原油行情最近如何', '北京最近房价怎么样',
                                    '今天旅游去哪玩', '我想聊未识别的新话题', '钻石成交价周五涨了吗'])
@pytest.mark.parametrize('text', [MARKET_COMPARISON, '上周五强的方向今天表现怎么样？',
                                  '那和之前相比有什么变化呢，整体是不是更强一些？'])
def test_implicit_market_language_cannot_jump_back_over_latest_other_topic(latest, text):
    decision = scope.scope_for(request(text, recent=['今天A股盘面复盘', latest], focus=FOCUS))
    assert not decision['resources_allowed'] and decision['authorized_user_messages'] == []


def test_explicit_new_equity_request_can_return_from_an_unrelated_topic():
    text = '请比较A股周五与今天的盘面，看看领涨板块的变化'
    assert scope.scope_for(request(text, recent=['北京房价如何']))['resources_allowed']


@pytest.mark.parametrize('text', ['周五变化怎么样', '对比一下上周五', '上周五和今天怎么比较',
                                   '钻石周五行情相比今天有哪些变化'])
def test_time_or_comparison_alone_does_not_authorize_resources(text):
    assert not scope.scope_for(request(text))['resources_allowed']


def test_market_followup_cannot_borrow_assistant_or_page_context():
    text = '那和之前相比有什么变化呢，整体是不是更强一些？'
    synthetic = {'seed_messages': [{'role': 'user', 'content': '页面锚点：A股复盘'},
                                    {'role': 'assistant', 'content': '接下来可以比较周五与今天的盘面。'}],
                 'page_anchor': {'kind': 'market', 'as_of': '2026-10-30'}, 'kind': 'market'}
    assert not scope.scope_for(request(text, **synthetic))['resources_allowed']


@pytest.mark.parametrize('text,query', [
    ('黄金价格变化对A股黄金股有什么影响？', '黄金价格变化'),
    ('原油价格上涨对A股上市公司有什么影响？', '原油价格走势'),
    ('期货价格波动对A股上市公司经营有什么影响？', '期货价格波动'),
    ('比特币价格变化对A股矿机公司有什么影响？', '比特币价格变化'),
    ('显卡供需变化对A股半导体公司有什么影响？', '显卡价格和供需'),
])
def test_explicit_equity_impact_still_can_request_its_same_auxiliary_topic(text, query):
    req = request(text)
    assert scope.scope_for(req)['purposes'] == ['stock_impact']
    assert scope.authorize_plan(req, plan(query, text, 'stock_impact'))


def test_stock_impact_cannot_steal_another_commodity_topic():
    text = '黄金价格变化对A股黄金股有什么影响？'
    assert not scope.authorize_plan(request(text), plan('原油期货价格', text, 'stock_impact'))


@pytest.mark.parametrize('text', [
    '今天的资金主要去了哪些方向？',
    '今天下午资金轮转是什么情况？',
    '上周五资金方向今天怎么样？',
    '上周五资金主要去了哪些方向，今天有变化吗？',
    '请帮我看看今天早盘竞价资金进攻了哪些方向？',
    '今天午盘资金流向与早盘有哪些变化？',
    '请分析今天场内资金主要流入哪些板块，午间和收盘有什么变化？',
])
def test_dated_equity_fund_rotation_is_a_market_review(text):
    for recent in ([], ['今天收盘行情数据有了吗？']):
        req = request(text, recent=recent)
        decision = scope.scope_for(req)
        assert decision['resources_allowed'] and decision['purposes'] == ['market_review']
        assert scope.authorize_plan(req, plan('今日A股资金方向与板块轮动复盘', text, 'market_review'))


@pytest.mark.parametrize('text', ['那今天呢？', '那上周五呢？', '那么上一交易日怎么样？',
                                   '资金主要去了哪些方向？', '资金轮转是什么情况？'])
def test_date_and_flow_ellipsis_require_nearest_actual_market_user(text):
    assert not scope.scope_for(request(text))['resources_allowed']
    assert not scope.scope_for(request(text, focus=FOCUS))['resources_allowed']
    assert not scope.scope_for(request(text, recent=['研究贵州茅台股票估值'], focus=FOCUS))['resources_allowed']
    decision = scope.scope_for(request(text, recent=['今天收盘行情数据有了吗？']))
    assert decision['resources_allowed'] and decision['purposes'] == ['market_review']
    assert decision['authorized_user_messages'] == [text, '今天收盘行情数据有了吗？']
    # A chain of complete date ellipses can refer back to its actual user topic.
    decision = scope.scope_for(request(text, recent=['今天收盘行情数据有了吗？', '那上周五呢？']))
    assert decision['resources_allowed'] and decision['purposes'] == ['market_review']


@pytest.mark.parametrize('text', ['那今天呢？', '那上周五呢？', '资金主要去了哪些方向？',
                                   '今天下午资金轮转是什么情况？'])
@pytest.mark.parametrize('latest', ['今天黄金价格如何', '原油今天行情怎么样', '北京房价跌了吗',
                                    '今天天气怎么样', '今天旅游去哪玩', '我想聊一个未识别的新话题'])
def test_new_date_and_flow_phrases_cannot_skip_a_new_other_topic(text, latest):
    req = request(text, recent=['今天A股盘面复盘', latest], focus=FOCUS)
    assert not scope.scope_for(req)['resources_allowed']
    assert not scope.authorize_plan(req, plan('A股资金流向', text, 'market_review'))


@pytest.mark.parametrize('text', [
    '资金', '今天资金', '那今天资金呢？',
    'AAPL今天资金方向怎么样？', 'NVDA今天资金流向如何？',
    '今天个人资金主要去了哪些方向？', '今天我的钱主要流向了哪里？',
    '今天银行转账的资金主要去了哪些方向？', '今天资金去了哪些银行账户？',
    '今天被骗的资金流向哪些方向？', '今天诈骗资金轮转是什么情况？',
    '今天政治献金资金流向如何？', '今天政府资金轮转是什么情况？',
    '今天慈善资金主要去了哪些方向？',
    '今天黄金资金流向如何？', '今天原油资金进攻哪些方向？',
    '不聊股票，今天资金流向如何？',
])
def test_money_words_or_foreign_codes_never_authorize_non_equity_uses(text):
    req = request(text, recent=['今天A股盘面复盘'], focus=FOCUS)
    assert not scope.scope_for(req)['resources_allowed']
    assert not scope.authorize_plan(req, plan('今日A股资金流向', text, 'market_review'))


@pytest.mark.parametrize('query', ['今日银行转账资金流向', '今天竞选资金流向', '今天诈骗资金轮转'])
def test_market_flow_plan_cannot_switch_to_personal_or_political_money(query):
    text = '今天下午资金轮转是什么情况？'
    assert not scope.authorize_plan(request(text), plan(query, text, 'market_review'))
