"""Conservative stock-resource authorization from real user messages only.

This gate authorizes read/search resources, not a general language classifier.
Unknown uses receive no resources. Assistant prose, page kind and planner reasons
cannot confer permission; a newer unrelated user topic revokes older context.
"""
import re
import unicodedata
import json

VERSION='ai-stock-scope-v2'
DENIED_MESSAGE='我只能协助A股行情复盘和股票研究，包括上市公司经营、估值及对股票的影响分析。'
PURPOSES=('stock_research','market_review','stock_impact')

def _clean(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text or '') if unicodedata.category(c)!='Cf').strip()

_TARGET=re.compile(r'A股|股票|股市|个股|上市公司|大盘|板块|强势股|(?:地产|银行|保险|旅游|消费|科技|软件|白酒|医药|券商|周期|能源|煤炭)股|股价|股指|上证|深证|沪深|创业板|科创板',re.I)
_DIRECT=re.compile(r'竞价|涨停|跌停|复盘|午盘|午间|盘后|收盘|成交额|净流入|市值|市盈率|市净率|估值|年报|财报|季报|公告|每股|股息|分红|(?<![A-Za-z])(?:PE|PB|MACD|KDJ|BOLL)(?![A-Za-z])|(?:今天|今日|当前|最近|最新).{0,4}(?:大盘|市场)|大盘|板块轮动',re.I)
_MARKET=re.compile(r'股市|大盘|板块|强势股|竞价|涨停|跌停|复盘|午盘|午间|盘后|收盘|指数|上证|深证|沪深|创业板|科创板|(?:今天|今日|当前|最近|最新).{0,4}市场')
_CONCEPT=re.compile(r'是什么|什么意思|如何理解|怎么理解|概念|含义|定义|怎么算|如何计算|什么是|就一定|一定.{0,3}吗')
_OTHER=re.compile(r'房价|住宅|楼盘|看房|买房|租房|房租|房贷|楼市|房地产|地产数据|天气|气温|降雨|旅游|旅行|酒店|机票|景点|购物|优惠券|买酒|买手机|买电脑|穿搭|菜谱|做饭|美食|食谱|健身|医疗|看病|药怎么吃|感情|恋爱|婚姻|育儿|星座|讲笑话|写诗|写作|作文|写代码|编程|写程序|爬虫|闲聊|GDP|CPI|通胀|利率|汇率|美联储|经济数据',re.I)
_ACTION=re.compile(r'写(?:一[个篇首段份])?.{0,6}(?:代码|程序|脚本|作文|诗)|编程|旅游攻略|旅行攻略|购物推荐|优惠券|哪个楼盘|推荐.{0,8}(?:房子|楼盘|酒店|手机|电脑)|怎么做饭|讲.{0,3}笑话')
_REVOKE=re.compile(r'(?:不聊|不谈|别谈|不看|不要|无关|不涉及).{0,6}(?:股票|股市|A股)|(?:股票|股市|A股).{0,6}(?:放一边|先不谈|先不聊)|(?:换个话题|题外话|先不聊)|只.{0,4}(?:房价|天气|旅游|购物|住宅|看房|写代码)')
_IMPACT=re.compile(r'影响|冲击|利好|利空|传导|受益|拖累|敏感|关联|关系')
_FOLLOW=re.compile(r'(?:(?:北京|上海|广州|深圳)(?:的|呢|的呢)?|(?:今天|今日|现在|目前|下午|上午|午间|明天|昨天|最近)(?:的)?(?:呢|怎么样|如何)?|(?:它|该股|这只股票|这家公司)(?:的)?(?:呢|怎么样|如何|有哪些风险呢?|主要靠什么赚钱)?|(?:那|那么)(?:呢|继续)?|继续(?:看|分析|研究)?(?:一下)?|接着(?:看|分析)?|再(?:看看|分析一下)|对|是|嗯|好的)[？?。！! ]*',re.I)
_STOCK_USE=re.compile(r'研究|分析|经营|主营|赚钱|业务|财报|年报|估值|走势|风险|竞争|利润|盈利|负债|现金流|业绩|增长|股息|分红|公告')
_AUX_TOPICS=(r'房价|住宅|楼盘|房贷|楼市|房地产|地产数据',r'天气|气温|降雨',r'旅游|旅行|酒店|机票|景点',r'GDP|CPI|通胀|利率|汇率|美联储|经济数据')

def _stock_impact(text):
    for clause in re.split(r'[，,。；;!?！？\n]',text):
        if not (_OTHER.search(clause) and _IMPACT.search(clause)):continue
        if re.search(r'(?:对|影响|冲击|利好|利空|拖累).{0,8}(?:'+_TARGET.pattern+r')',clause,re.I):return True
        if re.search(r'(?:'+_TARGET.pattern+r').{0,8}(?:受|受到|面临|与|和).{0,24}(?:'+_OTHER.pattern+r')',clause,re.I):return True
    return False

def _classify(text,focus):
    text=_clean(text)
    if not text:return 'unknown',None
    if _REVOKE.search(text) or _ACTION.search(text):return 'outside',None
    other=bool(_OTHER.search(text));target=bool(_TARGET.search(text))
    # An unrelated question cannot borrow a stock word, old anchor or a generic
    # finance label. A non-stock resource requires an explicit stock-impact task.
    if other:
        mixed=bool(re.search(r'再查|顺便|另外|以及|此外|然后(?:聊|查|说|看)|再(?:聊|看)',text))
        if target and not mixed:
            if re.search(r'(?:房地产板块|旅游板块|股票板块|地产股|旅游股).{0,12}(?:业绩|涨幅|走势|排行|排名|估值|财报|净流入)',text):return 'allowed','market_review'
            if _stock_impact(text):return 'allowed','stock_impact'
        return 'outside',None
    # In this stock-only assistant, an otherwise bare market question means
    # the equity market. Keep the entire phrase bounded; a commodity or other
    # subject inserted before "行情" must not inherit this permission.
    if re.fullmatch(r'(?:请|帮我|能否|能不能)?(?:看看|看一下|分析一下|分析|说说|聊聊|总结一下)?'
                    r'(?:今天|今日|最近|近期|当前|这几天|近几天|本周|这周)?的?'
                    r'(?:行情|市场行情)(?:表现|走势|情况)?'
                    r'(?:怎么样|如何|强弱|好不好|发生了什么|是什么情况|咋样|啥样)?(?:呢|吗)?[？?。！! ]*',text):
        return 'allowed','market_review'
    identities=[value for value in (focus.get('instrument_id'),focus.get('name')) if isinstance(value,str) and value]
    if focus.get('instrument_id'):identities.append(focus['instrument_id'].split('.')[0])
    known=any(value in text for value in identities)
    # A verified company identity is not permission to shop, entertain or do
    # arbitrary work. Accept a bare identity or an actual stock-research use.
    known=known and (text.rstrip('？?。！! ' ) in identities or bool(_STOCK_USE.search(text)))
    if focus and re.search(r'(?:这家|该|这只|它|当前|主要).{0,12}(?:经营|主营|赚钱|业务|风险|竞争|利润|盈利|负债|现金流)|(?:研究|分析).{0,8}(?:公司|个股|股票)',text):known=True
    if target or _DIRECT.search(text) or known:
        if _CONCEPT.search(text):return 'concept',None
        return 'allowed','market_review' if _MARKET.search(text) else 'stock_research'
    if re.fullmatch(r'(?:你好|您好|嗨|hello|hi|谢谢|感谢|早上好|晚上好)[！!。,.， ]*',text,re.I):return 'greeting',None
    return 'unknown',None

def authorize_round(current_text,recent_user_texts=(),validated_stock_focus=None):
    focus=validated_stock_focus if isinstance(validated_stock_focus,dict) else {}
    current=current_text.strip();state,purpose=_classify(current,focus);basis=[]
    if state=='unknown':
        # Stop at the nearest user topic. Never skip an intervening unrelated
        # question to revive an older stock discussion or assistant suggestion.
        for previous in list(recent_user_texts)[-4:][::-1]:
            prior,prior_purpose=_classify(previous,focus)
            if prior in ('outside','greeting','concept'):state='outside' if prior=='outside' else 'unknown';break
            if prior=='allowed':
                if _FOLLOW.fullmatch(_clean(current)):
                    state,purpose,basis='allowed',prior_purpose,[previous]
                break
    return {'version':VERSION,'state':state,'resources_allowed':state=='allowed',
            'purposes':[purpose] if purpose else [],'authorized_user_messages':[current,*basis] if state=='allowed' else [],
            'validated_stock_focus':focus,'current_text':current,
            'local_only':any(re.search(r'本系统|本地|系统数据|内部数据',_clean(item)) for item in [current,*basis])}

def scope_for(request):
    trusted=request.get('scope_input') or {}
    return authorize_round(trusted.get('current_text',''),trusted.get('recent_user_texts',[]),trusted.get('validated_stock_focus'))

def apply_to_request(request):
    scope=scope_for(request);request['scope']=scope
    request['seed_messages'][0]={'role':'system','content':request['round_protocol']['system_prompt']+'\n本轮程序资源授权（只使用真实用户问题，不从页面或助手话术授信）：'+json.dumps(scope,ensure_ascii=False)+'\n若resources_allowed=false，不调用业务或网络工具。state为outside/unknown时，只自然简短说明只做股票研究，不回答其他领域内容、不追问城市。概念问题直接解释股票概念。'}
    return scope

def denial(*,local_only=False):
    return {'ok':False,'state':'scope_denied','reason_code':'local_data_only' if local_only else 'stock_scope_required',
            'summary':'用户要求本系统/本地数据，网页不能替代系统口径；只说明可核验的结果或当前能力缺口，不编造排名。' if local_only else '本轮只可简短说明股票研究范围，不回答其他领域内容，不追问城市或继续查询。'}

def authorize_plan(request,args):
    scope=scope_for(request)
    if not scope['resources_allowed'] or scope['local_only'] or not isinstance(args,dict):return False
    if (set(args)!={'query','reason','purpose','user_quote'} or args.get('purpose') not in scope['purposes']
            or args.get('user_quote') not in scope['authorized_user_messages']
            or not all(isinstance(args.get(k),str) and 0<len(args[k])<=(2000 if k=='user_quote' else 1000) for k in args)):
        return False
    query=_clean(args['query'])
    if _REVOKE.search(query) or _ACTION.search(query):return False
    if _OTHER.search(query):
        # Auxiliary macro/sector data is permitted only for the user's explicit
        # stock-impact request, never a planner's invented research rationale.
        original=_clean(args['user_quote'])
        query_topics={index for index,pattern in enumerate(_AUX_TOPICS) if re.search(pattern,query,re.I)}
        original_topics={index for index,pattern in enumerate(_AUX_TOPICS) if re.search(pattern,original,re.I)}
        return args['purpose']=='stock_impact' and bool(query_topics) and query_topics<=original_topics
    state,purpose=_classify(query,scope['validated_stock_focus'])
    return state=='allowed' or (state=='concept' and args['purpose']=='stock_research')
