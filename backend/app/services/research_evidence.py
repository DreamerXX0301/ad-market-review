"""Versioned, evidence-linked research views; no HTTP, SQL or model calls."""
from datetime import date, timedelta
from hashlib import sha256
import json
from urllib.parse import urlparse
from app.core import json_clean

VERSION = 'evidence-research-v2'
SCHEMA_VERSION = 2
SECTIONS = [
    {'id': 'overview', 'title': '调研总览'},
    {'id': 'company', 'title': '公司与产业链'},
    {'id': 'valuation', 'title': '财务、估值与机构观点'},
    {'id': 'news', 'title': '事件线索与风险'},
    {'id': 'technical', 'title': '日周月趋势与位置'},
    {'id': 'evidence', 'title': '证据与待核查清单'},
]


def freeze_plan(symbol, snapshot, target_date, *, market_cap_fallback=True):
    end = date.fromisoformat(target_date)
    return {
        'version': VERSION, 'schema_version': SCHEMA_VERSION,
        'instrument_id': symbol, 'market_snapshot_id': snapshot,
        'target_date': target_date, 'mode': 'manual_latest',
        'price_basis': 'none', 'indicator_continuity': 'market_sessions',
        'benchmark': '000001.SH',
        'windows': {kind: {'start': (end-timedelta(days=days-1)).isoformat(), 'end': target_date}
                    for kind, days in [('announcements', 365), ('news', 30), ('institution_reports', 180)]},
        'sources': ['fuyao-financials', 'fuyao-valuations', 'cninfo-announcements',
                    'eastmoney-shareholders', 'eastmoney-market-cap', 'eastmoney-business-composition',
                    *(['tencent-market-cap'] if market_cap_fallback else [])],
        'external_only': ['institution-reports', 'institution-forecasts', 'xueqiu', 'jiuyangongshe'],
        'source_budget': {'provider_endpoint_limit': 5, 'provider_transport': 'existing-bounded-provider-v1',
                          'public_max_http_attempts': 32, 'public_deadline_seconds': 90,
                          'public_timeout_seconds': 6, 'public_max_response_bytes': 4194304,
                          'announcement_max_pages': 12, 'company_max_rows': 200},
        'model_calls': False, 'cache_ttl_days': 7,
    }


def fingerprint(value):
    return sha256(json.dumps(json_clean(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_url(value):
    if not isinstance(value, str): return None
    parsed = urlparse(value)
    return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else None


def external_links(symbol):
    code, market = symbol.split('.')
    return [
        {'source_id': 'cninfo', 'title': '巨潮公告与公开调研', 'url': 'https://www.cninfo.com.cn/', 'state': 'external_only', 'kind': 'official'},
        {'source_id': 'irm', 'title': '互动易公司答复', 'url': 'https://irm.cninfo.com.cn/newircs/', 'state': 'external_only', 'kind': 'company_response'},
        {'source_id': 'eastmoney-reports', 'title': '个股研报目录', 'url': 'https://data.eastmoney.com/report/stock.jshtml', 'state': 'requires_license', 'kind': 'institution_directory'},
        {'source_id': 'eastmoney-forecasts', 'title': '盈利预测目录', 'url': 'https://data.eastmoney.com/report/ylyc.html', 'state': 'requires_license', 'kind': 'forecast_directory'},
        {'source_id': 'xueqiu', 'title': '雪球 · '+symbol, 'url': 'https://xueqiu.com/S/'+market+code, 'state': 'external_only', 'kind': 'community', 'reason': '只提供原文入口；未授权抓取、缓存或AI处理用户内容'},
        {'source_id': 'jiuyangongshe', 'title': '韭研公社 · 公开讨论入口', 'url': 'https://www.jiuyangongshe.com/square', 'state': 'external_only', 'kind': 'community', 'reason': '请按股票代码检索；公开预览不代表全文或自动处理授权'},
    ]


def build_evidence_report(symbol, instrument, source_data, plan):
    """Use only accepted material fields; titles are discovery clues, never facts."""
    evidence = []
    claims = []
    fetched = source_data['material_window']['collection_finished_at_ms']
    material_meta={row['kind']:row for row in source_data.get('materials',[])}
    def add(kind, title, source, data, *, url=None, published=None, period=None, tier='B', official=False):
        digest = fingerprint(data)
        material_key={'valuation_snapshot':'valuation','announcement':'announcements','business_composition':'company_profile'}.get(kind,kind)
        metadata=material_meta.get(material_key,{})
        item = {'evidence_id': 'evidence-'+fingerprint([symbol, kind, source, digest])[:24],
                'instrument_id': symbol, 'title': title, 'source': source, 'kind': kind,
                'tier': tier, 'url': safe_url(url), 'published_at_ms': published,
                'fetched_at_ms': metadata.get('fetched_at_ms',fetched), 'report_period': period, 'content_hash': digest,
                'response_json_sha256': metadata.get('response_json_sha256'),
                'identity_basis': '完整证券代码及市场；仅纳入本报告已核验字段',
                'official_document': official, 'historical_usable': False,
                'publication_date_known': published is not None,
                'data': data}
        old = next((row for row in evidence if row['evidence_id'] == item['evidence_id']), None)
        if old is None: evidence.append(item)
        return item['evidence_id']

    for kind, rows in source_data.get('financials', {}).get('statements', {}).items():
        for row in rows:
            add('financial_'+kind, row['report_period']+' · '+{'income': '利润表', 'balance': '资产负债表', 'cashflow': '现金流量表'}.get(kind, kind),
                'fuyao', row, published=row.get('published_at_ms'), period=row.get('report_period'))
    if source_data.get('valuation'):
        add('valuation_snapshot', '来源当前估值快照', 'fuyao', source_data['valuation'])
    for kind in ('shareholders', 'market_cap'):
        if source_data.get(kind): add(kind, '股东人数' if kind == 'shareholders' else '来源市值快照', source_data[kind].get('source', 'eastmoney'), source_data[kind])

    profile = source_data.get('company_profile', {})
    company = {'state': 'unavailable', 'name': instrument.get('name', symbol), 'main_business': None,
               'business_scope': None, 'industry': None, 'items': [], 'evidence_ids': [],
               'reason': '本次未取得可核验主营构成；不从题材标签或公告标题推断主营业务'}
    chain = []
    if profile.get('items'):
        company = {**company, **{key: value for key, value in profile.items() if key != 'raw'},
                   'state': 'partial', 'items': [], 'reason': '主营构成来自当前取得的来源快照；未取得经营范围、客户/供应商及原披露时间'}
        for row in profile['items']:
            reference = add('business_composition', row['report_period']+' · '+row['category']+' · '+row['name'],
                            'eastmoney', row, url=profile.get('url'), period=row['report_period'])
            row = {**row, 'evidence_ids': [reference]}
            company['items'].append(row)
            company['evidence_ids'].append(reference)
            if row['category_code'] == '2':
                chain.append({'relation_type': 'product', 'subject': instrument.get('name', symbol), 'object': row['name'],
                              'description': '来源按产品披露的主营构成项目；不推导供货关系',
                              'state': 'source_statement', 'report_period': row['report_period'], 'evidence_ids': [reference]})
        product_names = list(dict.fromkeys(row['name'] for row in profile['items'] if row['category_code'] == '2'))
        if product_names:
            company['main_business'] = '来源主营构成中的产品项目：'+'、'.join(product_names[:8])
            claims.append({'claim_id': 'claim-business', 'kind': 'source_statement', 'text': company['main_business'], 'evidence_ids': company['evidence_ids']})

    news_by_key = {}
    start = plan['windows']['news']['start']
    for announcement in source_data.get('announcements', {}).get('items', []):
        # Publication time/date and identity were verified by the source adapter.
        published_date=announcement.get('published_date','')
        if not plan['windows']['announcements']['start']<=published_date<=plan['windows']['announcements']['end']:continue
        reference = add('announcement', announcement['title'], 'cninfo',
                        {key: value for key, value in announcement.items() if key != 'raw'},
                        url=announcement.get('url'), published=announcement.get('published_at_ms'),
                        tier='A', official=True)
        if not start<=announcement.get('published_date', '')<=plan['windows']['news']['end']: continue
        key = announcement.get('announcement_id') or safe_url(announcement.get('url')) or reference
        if key in news_by_key:
            if reference not in news_by_key[key]['evidence_ids']: news_by_key[key]['evidence_ids'].append(reference)
            continue
        title = announcement['title']
        categories = [name for name, words in [('业绩', ('业绩', '年度报告', '半年度报告', '季度报告')),
                      ('股东变动', ('减持', '增持', '回购')), ('监管与风险', ('监管', '问询', '风险', '立案')),
                      ('业务线索', ('合同', '订单', '合作', '投资者关系', '调研', '中标'))] if any(word in title for word in words)]
        status = '更正或澄清线索' if any(word in title for word in ('更正', '修订', '澄清', '补充')) else '进展线索' if '进展' in title else '计划线索' if any(word in title for word in ('计划', '预案', '拟')) else '待阅读原文'
        card = {'id': 'event-'+fingerprint([symbol, key])[:24], 'title': title, 'source': 'cninfo',
                'url': safe_url(announcement.get('url')), 'published_at_ms': announcement.get('published_at_ms'),
                'published_date': announcement.get('published_date'), 'event_type': '、'.join(categories) or '公告披露',
                'summary': '公告标题线索，'+status+'；未读取全文，不确认事件已完成或利好利空。',
                'evidence_ids': [reference], 'impact': 'unknown', 'impact_label': '方向待核验',
                'positive_evidence_ids': [], 'negative_evidence_ids': [], 'status': status,
                'classification_basis': '标题关键词，仅用于线索检索',
                'questions': ['核对原文中的实施条件、金额/业务范围和实际进展', '查找更正、终止、否认或相反证据']}
        news_by_key[key] = card
        claims.append({'claim_id': card['id']+'-clue', 'kind': 'title_clue', 'text': title, 'evidence_ids': [reference]})
    news = sorted(news_by_key.values(), key=lambda row: (row.get('published_at_ms') or 0, row['id']), reverse=True)
    links = external_links(symbol)
    gaps = source_data.get('gaps', [])
    coverage = []
    for key, title in [('financial', '财报与财务指标'), ('valuation', '当前估值'), ('announcements', '官方公告'),
                       ('shareholders', '股东人数'), ('market_cap', '来源市值'), ('company_profile', '主营构成')]:
        related = [gap for gap in gaps if gap['capability'] == key or key == 'financial' and gap['capability'].startswith('financial_')]
        count = sum(1 for row in evidence if (row['kind'].startswith('financial_') if key == 'financial' else
                    row['kind'] == {'valuation': 'valuation_snapshot', 'announcements': 'announcement', 'company_profile': 'business_composition'}.get(key, key)))
        known_empty=key=='announcements' and source_data.get('announcements',{}).get('coverage',{}).get('complete') is True
        coverage.append({'source_id': key, 'title': title, 'state': 'partial' if count and related else 'available' if count or known_empty else 'unavailable',
                         'accepted_count': count, 'reason': '；'.join(gap['reason'] for gap in related) or (None if count or known_empty else '本次未取得可核验材料')})
    coverage.extend([
        {'source_id': 'institution_views', 'title': '机构研报、预测与目标价', 'state': 'requires_license', 'accepted_count': 0, 'reason': '当前已配置来源未提供已授权的研报/目标价/盈利预测合同；不从PE或新闻推算机构观点'},
        {'source_id': 'media_news', 'title': '媒体新闻', 'state': 'unavailable', 'accepted_count': 0, 'reason': '尚未配置可核验及可处理的新闻来源；本次时间轴仅含实际取得公告'},
    ])
    questions = [
        {'question': '公司产品位于哪一产业链环节？客户与供应商有哪些已披露证据？', 'reason': '产品项目不等于供应链关系，需公司正式材料核对', 'evidence_ids': company['evidence_ids']},
        {'question': '机构对未来年度的盈利预测与目标价为何不同？', 'reason': '需要有明确机构、报告日、预测年度、币种和使用许可的原报告', 'evidence_ids': []},
        {'question': '近期事件是否真正影响收入、成本、订单或政策资格？', 'reason': '标题线索不能确认经营结果；需阅读全文及反证', 'evidence_ids': [row['evidence_id'] for row in evidence if row['kind'] == 'announcement'][:6]},
    ]
    highlights = [{'text': company['main_business'], 'evidence_ids': company['evidence_ids']}] if company['main_business'] else []
    for item in news[:2]:highlights.append({'text': '近期公告线索：'+item['title'], 'evidence_ids': item['evidence_ids']})
    return {'schema_version': SCHEMA_VERSION, 'research_version': VERSION, 'frozen_plan': plan,
            'report_sections': SECTIONS, 'company_profile': company,
            'industry_chain': {'items': chain, 'state': 'partial' if chain else 'unknown', 'reason': '仅列来源产品关系；上下游客户/供应商待正式证据核验'},
            'institution_views': {'items': [], 'state': 'requires_license', 'reason': coverage[-2]['reason'], 'links': [row for row in links if row['kind'].endswith('_directory')]},
            'news': {'items': news, 'state': 'partial' if news else 'unknown', 'scope': '实际取得的近30自然日官方公告线索；不是全网新闻或完整风险事件', 'window': plan['windows']['news']},
            'evidence': evidence, 'claims': claims, 'source_coverage': coverage,
            'external_links': links, 'unresolved_questions': questions,
            'overview': {'summary': '已按实际取得材料整理证据、公告线索与本地多周期观察；未核验信息保留缺口。',
                         'highlights': highlights, 'evidence_count': len(evidence), 'event_count': len(news),
                         'model_used': False, 'historical_research': False,
                         'notice': '当前手动调研；材料获取时间与行情截止分开。来源观点不等于事实，技术距离不等于盈利目标。'}}
