"""Conservative stock-resource authorization from real user messages only.

This gate authorizes read/search resources, not a general language classifier.
Unknown uses receive no resources. Assistant prose, page kind and planner reasons
cannot confer permission; a newer unrelated user topic revokes older context.
"""
import re
import unicodedata
import json

VERSION='ai-stock-scope-v3'
DENIED_MESSAGE='我只能协助A股行情复盘和股票研究，包括上市公司经营、估值及对股票的影响分析。'
PURPOSES=('stock_research','market_review','stock_impact')

def _clean(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text or '') if unicodedata.category(c)!='Cf').strip()

_TARGET=re.compile(r'A股|股票|股市|个股|上市公司|大盘|板块|强势股|(?:地产|银行|保险|旅游|消费|科技|软件|白酒|医药|券商|周期|能源|煤炭)股|股价|股指|上证|深证|沪深|创业板|科创板',re.I)
_DIRECT=re.compile(r'竞价|涨停|跌停|复盘|午盘|午间|盘后|收盘|成交额|净流入|市值|市盈率|市净率|估值|年报|财报|季报|公告|每股|股息|分红|(?<![A-Za-z])(?:PE|PB|MACD|KDJ|BOLL)(?![A-Za-z])|(?:今天|今日|当前|最近|最新).{0,4}(?:大盘|市场)|大盘|板块轮动',re.I)
_MARKET=re.compile(r'股市|大盘|板块|强势股|竞价|涨停|跌停|复盘|午盘|午间|盘后|收盘|指数|上证|深证|沪深|创业板|科创板|(?:今天|今日|当前|最近|最新).{0,4}市场')
_CONCEPT=re.compile(r'是什么|什么意思|如何理解|怎么理解|概念|含义|定义|怎么算|如何计算|什么是|就一定|一定.{0,3}吗')
_OTHER=re.compile(r'黄金(?!股|板块)|原油(?!股|板块)|期货(?!股|板块|上市公司)|加密货币|虚拟货币|数字货币(?!板块|概念股)|比特币|以太坊|狗狗币|币圈|币价|(?<!货)(?<!人民)币|显卡(?!股|板块)|钻石|海鲜|二手手机|足球|篮球|磁场|房价|住宅|楼盘|看房|买房|租房|房租|房贷|楼市|房地产|地产数据|天气|气温|降雨|旅游|旅行|酒店|机票|景点|购物|优惠券|买酒|买手机|买电脑|穿搭|菜谱|做饭|美食|食谱|健身|医疗|看病|药怎么吃|感情|恋爱|婚姻|育儿|星座|讲笑话|写诗|写作|作文|写代码|编程|写程序|爬虫|闲聊|GDP|CPI|通胀|利率|汇率|美联储|经济数据',re.I)
_ACTION=re.compile(r'写(?:一[个篇首段份])?.{0,6}(?:代码|程序|脚本|作文|诗)|编程|旅游攻略|旅行攻略|购物推荐|优惠券|哪个楼盘|推荐.{0,8}(?:房子|楼盘|酒店|手机|电脑)|怎么做饭|讲.{0,3}笑话')
_REVOKE=re.compile(r'(?:不聊|不谈|别谈|不看|不要|无关|不涉及).{0,6}(?:股票|股市|A股)|(?:股票|股市|A股).{0,6}(?:放一边|先不谈|先不聊)|(?:换个话题|题外话|先不聊)|只.{0,4}(?:房价|天气|旅游|购物|住宅|看房|写代码)')
_IMPACT=re.compile(r'影响|冲击|利好|利空|传导|受益|拖累|敏感|关联|关系')
_FOLLOW=re.compile(r'(?:(?:北京|上海|广州|深圳)(?:的|呢|的呢)?|(?:今天|今日|现在|目前|下午|上午|午间|明天|昨天|最近)(?:的)?(?:呢|怎么样|如何)?|(?:它|该股|这只股票|这家公司)(?:的)?(?:呢|怎么样|如何|有哪些风险呢?|主要靠什么赚钱)?|(?:那|那么)(?:呢|继续)?|继续(?:看|分析|研究)?(?:一下)?|接着(?:看|分析)?|再(?:看看|分析一下)|对|是|嗯|好的)[？?。！! ]*',re.I)
_STOCK_USE=re.compile(r'研究|分析|经营|主营|赚钱|业务|财报|年报|估值|走势|风险|竞争|利润|盈利|负债|现金流|业绩|增长|股息|分红|公告')
_AUX_TOPICS=(r'房价|住宅|楼盘|房贷|楼市|房地产|地产数据',r'天气|气温|降雨',r'旅游|旅行|酒店|机票|景点',r'GDP|CPI|通胀|利率|汇率|美联储|经济数据',r'黄金',r'原油',r'期货',r'加密货币|虚拟货币|数字货币|比特币|以太坊|狗狗币|币圈|币价|(?:^|[，,。；;!?！？\s])币',r'显卡')

# A complete market-review request need not repeat "A股" on every turn.
# Recognize a time/object/comparison structure, not isolated "周五/对比" words.
# The market object must have a neutral question/date prefix: "钻石周五行情"
# cannot acquire equity permission merely by inserting a weekday before 行情.
_REVIEW_TIME=re.compile(r'\d{4}[-年]\d{1,2}[-月]\d{1,2}日?|(?:今天|今日|昨天|昨日|前天|前日|明天|明日|当日|当天|之前|此前|现在|当前|目前)|(?:上|前)(?:一|个)?交易日|(?:(?:上|本|这|下)(?:个)?周|星期|周)[一二三四五六日天]?|(?:近|最近|过去|前|这)(?:几|两|三|四|五|[1-9]\d?)(?:个)?(?:交易日|天|日)|(?:早盘|午盘|午间|尾盘|盘前|盘中|开盘|收盘|竞价|上午|下午)')
_REVIEW_CHANGE=re.compile(r'对比|比较|相比|相较|比起|相对|对照|不同|变化|变了|转变|延续|持续|轮动|轮转|切换|分化|修复|回流|承接|表现|走势|走强|走弱|强弱|更强|更弱|怎么样|如何')
_REVIEW_DIRECTION=re.compile(r'(?:强势|弱势|走强|走弱|领涨|领跌|强|弱).{0,4}(?:方向|题材|板块)|(?:方向|题材|板块).{0,6}(?:强势|弱势|领涨|领跌|走强|走弱)')
_REVIEW_OBJECT=re.compile(r'行情|盘面')
_REVIEW_PREFIX=re.compile(r'我想了解|请您|请介绍|介绍|讲讲|讲一下|给我|能否|了解|这个|那个|那几条|这几条|那几个|这几个|有什么|有哪些|有何|有啥|是不是|是否|怎么样|怎样|这些|那些|它们|一些|一点|想请你|想知道|能不能|可不可以|请问|请你|帮我|帮忙|我们|我想|一下|一看|看看|看下|详细|具体|简单|整体|展开|分析|梳理|说明|解释|总结|说说|聊聊|评估|评价|对比|比较|相比|相较|比起|相对|对照|根据|对于|关于|那么|那|再|和|跟|与|于|的|请|我|也|先|将|把|到|更|还|仍|呢|吗')


def _neutral_review_prefix(text, position, *, whole=False):
    prefix=text[:position] if whole else re.split(r'[，,。；;!?！？\n]',text[:position])[-1]
    # Strip polite '一下' before dates so '比较一下周五' is not parsed as 下周五.
    prefix=_REVIEW_PREFIX.sub('',prefix)
    prefix=_REVIEW_CHANGE.sub('',prefix)
    prefix=_REVIEW_TIME.sub('',prefix)
    return not prefix.strip(' ：:、-—~～和与及，,。；;!?！？\n')


def _market_review_request(text):
    if not (_REVIEW_TIME.search(text) and _REVIEW_CHANGE.search(text)):
        return False
    for pattern in (_REVIEW_OBJECT,_REVIEW_DIRECTION):
        if any(_neutral_review_prefix(text,match.start()) for match in pattern.finditer(text)):
            return True
    return False


def _market_review_followup(text):
    # Longer, elliptical follow-ups may inherit only the nearest actual market
    # request. Outside/unknown subjects are still denied before this is consulted.
    if _market_review_request(text):return True
    if not _REVIEW_CHANGE.search(text):return False
    if (_REVIEW_TIME.search(text) and _neutral_review_prefix(text,len(text),whole=True)):return True
    subject=re.search(r'整体|方向|题材|它们|量能|承接|情绪|主线|热点|赚钱效应|强势|弱势|更强|更弱',text)
    return bool(subject and _neutral_review_prefix(text,subject.start(),whole=True)
                and (_REVIEW_TIME.search(text) or re.search(r'相比|相较|对比|比较|对照|变化|延续|持续|轮动|分化',text)))

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
    if _market_review_request(text):return 'allowed','market_review'
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
    cleaned=_clean(current)
    identities=[value for value in (focus.get('instrument_id'),focus.get('name')) if isinstance(value,str) and value]
    if focus.get('instrument_id'):identities.append(focus['instrument_id'].split('.')[0])
    names_stock=any(value in cleaned for value in identities)
    if state=='allowed' and purpose=='market_review' and not (_TARGET.search(cleaned) or names_stock):
        # In an otherwise empty stock assistant "周五行情" can mean A shares.
        # A just-mentioned commodity/housing/unknown subject must not silently
        # become equities, however. Explicit stock names/targets can start anew.
        for previous in list(recent_user_texts)[-4:][::-1]:
            prior,_=_classify(previous,focus)
            if prior=='outside':state,purpose='outside',None;break
            if prior=='unknown':
                if _FOLLOW.fullmatch(_clean(previous)) or _market_review_followup(_clean(previous)):continue
                state,purpose='unknown',None
            break
    if state=='unknown':
        # Stop at the nearest user topic. Never skip an intervening unrelated
        # question to revive an older stock discussion or assistant suggestion.
        for previous in list(recent_user_texts)[-4:][::-1]:
            prior,prior_purpose=_classify(previous,focus)
            if prior in ('outside','greeting','concept'):state='outside' if prior=='outside' else 'unknown';break
            if prior=='allowed':
                if (_FOLLOW.fullmatch(_clean(current))
                        or prior_purpose=='market_review' and _market_review_followup(_clean(current))):
                    state,purpose,basis='allowed',prior_purpose,[previous]
                break
            # Only a recognizable elliptical continuation can lead back to its
            # actual user topic; never jump over an unknown new subject.
            if not (_FOLLOW.fullmatch(_clean(previous)) or _market_review_followup(_clean(previous))):break
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
