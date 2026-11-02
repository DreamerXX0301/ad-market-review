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
