"""Bounded, self-contained AI inputs from already retained facts only.

No provider, model, task or retention mutations belong here. Market inputs use
the published Parquet reader and a dated projection of the independent
supplement publication, never the worker's mutable DuckDB database. Saving the
returned object is sufficient to read an AI conversation after research expiry.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import json
import math
import re

from fastapi import HTTPException

from app.core import connect, digest, dumps, get_market, json_clean, published_dir
from app.services.market_amount import amount_change_metadata
from app.services.market_sentiment import describe_sentiment
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.research_cache import get_report
from app.services.sector_history import sector_prices_for_date
from app.services.supplement_access import supplement_access
from app.services.ai_market_brief import build_briefs, fingerprints as brief_fingerprints

VERSION = 'ai-frozen-context-v2'
MAX_CONTEXT_BYTES = 64 * 1024
# Leave room for identities, references, exclusions and mandatory limitations.
CONTENT_BUDGET_BYTES = 48 * 1024
MAX_ITEM_BYTES = 6 * 1024
MAX_EVIDENCE = 64
KINDS = {'research': 'stock_research', 'stock': 'stock_research',
         'stock_research': 'stock_research', 'market': 'market_review',
         'market_review': 'market_review'}
SUPPLEMENT_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools', 'sector_index_history')
NOTICE = ('只解释本次冻结材料；未知不补零。材料获取、来源披露与行情截止日期分开；'
          '当前留存回看不证明历史当时可知。成交额不是净流入，价格反馈不是可成交收益；'
          '不补抓新闻、不回填当前成员、不生成自动荐股或交易指令。')
_SECRET_KEYS = {'raw', 'raw_pages', 'headers', 'authorization', 'api_key', 'apikey',
                'access_token', 'refresh_token', 'credential', 'credentials', 'secret'}
_RESEARCH_SOURCES = {'fuyao', 'eastmoney', 'cninfo'}
_RESEARCH_KINDS = {'valuation_snapshot', 'shareholders', 'market_cap',
                   'business_composition', 'announcement', 'financial_income',
                   'financial_balance', 'financial_cashflow'}
_RESEARCH_CAPS = {'valuation_snapshot': 1, 'shareholders': 1, 'market_cap': 1,
                  'business_composition': 6, 'announcement': 6,
                  'financial_income': 2, 'financial_balance': 2, 'financial_cashflow': 2}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '观察日必须为YYYY-MM-DD') from None
    return value


def _clean(value):
    """Do not export raw responses, credentials or private filesystem paths."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()
                if str(key).lower() not in _SECRET_KEYS
                and str(key) not in ('source_ref', 'computed_at', 'frozen_charts_by_period')}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return json_clean(value)


def _base(kind, subject, as_of):
    return {'version': VERSION, 'kind': kind, 'subject': subject, 'as_of': as_of,
            'dates': {'data_cutoff': as_of}, 'price_basis': 'none',
            'indicator_continuity': 'market_sessions', 'claims': [], 'evidence': [],
            'metrics': [], 'gaps': [], 'exclusions': [], 'source_fingerprints': {},
            'notice': NOTICE}


def _gap(context, capability, reason, *, state='unknown', source=None):
    context['gaps'].append({'capability': capability, 'state': state, 'source': source,
                            'date': context['as_of'], 'reason': reason})


def _add(context, evidence, metrics=(), claim=None):
    evidence = _clean(evidence)
    evidence['content_sha256'] = digest(evidence.get('data'))
    identifier = evidence['evidence_id']
    if any(row['evidence_id'] == identifier for row in context['evidence']):
        return True
    metrics = [{**_clean(row), 'evidence_ids': [identifier]} for row in metrics]
    package = {'evidence': evidence, 'metrics': metrics, 'claim': claim}
    used = sum(len(dumps(context[key]).encode()) for key in ('claims', 'evidence', 'metrics'))
    if (len(context['evidence']) >= MAX_EVIDENCE or len(dumps(evidence).encode()) > MAX_ITEM_BYTES
            or used + len(dumps(package).encode()) > CONTENT_BUDGET_BYTES):
        context['exclusions'].append({'evidence_id': identifier, 'source': evidence.get('source'),
                                      'reason': '超过有界输入条数或字节预算，整条排除；未截断日期、单位或数值'})
        return False
    context['evidence'].append(evidence)
    context['metrics'].extend(metrics)
    if claim:
        context['claims'].append({**claim, 'evidence_ids': [identifier]})
    return True


def _metrics(rows, prefix, *, as_of, dates=None, scope=None):
    result = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str):
            continue
        row = {'metric_id': prefix + ':' + item['key'], 'label': item.get('label', item['key']),
                       'value': item.get('value') if _finite(item.get('value')) else None,
                       'unit': item.get('unit'), 'formula': item.get('formula'),
                       'reason': item.get('reason'), 'as_of': as_of, 'dates': dates or {'trade_date': as_of},
                       'price_basis': 'none'}
        if scope:
            row['scope'] = scope
        result.append(row)
    return result


def _research_metrics(evidence, as_of):
    """Expose only explicit source units; never infer units from numeric values."""
    data = evidence.get('data', {})
    kind = evidence['kind']
    dates = evidence['dates']
    rows = []
    if kind == 'valuation_snapshot':
        for key in ('pe_ttm', 'pe_mrq', 'pb_mrq', 'ps_ttm', 'pcf_ttm'):
            if key in data:
                rows.append({'key': key, 'label': key, 'value': data[key], 'unit': data.get('unit'),
                             'reason': '来源当前估值快照；不是历史观察日估值或机构预测'})
    elif kind == 'business_composition':
        for key, unit in (('revenue_cny', 'CNY'), ('cost_cny', 'CNY'), ('profit_cny', 'CNY'),
                          ('revenue_ratio', 'ratio'), ('gross_margin_ratio', 'ratio')):
            if key in data:
                rows.append({'key': key, 'label': key, 'value': data[key], 'unit': unit,
                             'reason': 'ratio为原始比率，1代表100%；分类不能混加' if unit == 'ratio' else None})
    elif kind == 'market_cap':
        for key, unit in (('total_market_cap_cny', 'CNY'), ('float_market_cap_cny', 'CNY'),
                          ('total_shares', '股'), ('float_shares', '股')):
            if key in data:
                rows.append({'key': key, 'label': key, 'value': data[key], 'unit': unit})
    elif kind.startswith('financial_'):
        for key in ('operating_income', 'parent_holder_net_profit', 'net_profit', 'basic_eps',
                    'assets_total', 'total_debt', 'holder_equity_total', 'act_cash_flow_net'):
            if key in data:
                rows.append({'key': key, 'label': key, 'value': data[key],
                             'unit': 'CNY/股' if key == 'basic_eps' else 'CNY',
                             'reason': '来源合并报表原始报告期口径；未自行拆单季或派生同比'})
    return _metrics(rows, evidence['evidence_id'], as_of=as_of, dates=dates,
                    scope=data.get('scope', '仅本条来源报告期/快照，非任意历史时点可知资料'))


def _research_context(report_id, as_of):
    if not isinstance(report_id, str) or not report_id.strip():
        raise HTTPException(422, '个股AI解读须选择已保存的v2研究报告')
    report = get_report(report_id, touch=False)
    if report.get('schema_version') != 2:
        raise HTTPException(422, '仅支持已保存的v2研究报告；请手动创建新研究')
    instrument = report.get('instrument', {})
    symbol = instrument.get('instrument_id')
    if (instrument.get('kind') != 'stock' or not isinstance(symbol, str)
            or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol)):
        raise HTTPException(422, '研究报告缺少完整个股身份')
    cutoff = _day(report.get('data_cutoff'))
    if as_of is not None and _day(as_of) != cutoff:
        raise HTTPException(409, '研究报告行情截止日与请求不一致；不会补抓或回填')
    context = _base('stock_research', instrument.get('name') or symbol, cutoff)
    context.update(instrument_id=symbol, instrument=_clean(instrument),
                   source_report_id=report['report_id'], report_id=report['report_id'])
    window = report.get('material_window', {})
    context['dates'].update(materials_started_at_ms=window.get('collection_started_at_ms'),
                            materials_finished_at_ms=window.get('collection_finished_at_ms'),
                            research_as_of_ms=report.get('research_as_of_ms'))
    context['source_coverage'] = _clean(report.get('source_coverage', []))
    context['gaps'] = _clean(report.get('gaps', []))
    context['source_fingerprints'] = {'report_sha256': digest({key: value for key, value in report.items()
                                                            if key != 'cache'}),
                                       'snapshot_refs': report.get('snapshot_refs', {})}
    if report.get('egress_allowed') is False or report.get('ai_processing_allowed') is False:
        context['exclusions'].append({'source': 'report', 'reason': '父报告明确限制外发或AI处理，全部材料均未发送'})
        return context
    blocked = {row.get('source_id') for row in report.get('source_coverage', [])
               if isinstance(row, dict) and (row.get('state') in ('requires_license', 'external_only')
                    or row.get('egress_allowed') is False or row.get('ai_processing_allowed') is False)}
    # Coverage IDs in v2 reports describe capabilities as well as providers.
    # An explicit deny on either level must reach every dependent evidence row.
    capability = {'valuation_snapshot': 'valuation', 'announcement': 'announcements',
                  'business_composition': 'company_profile'}
    evidence_ids = Counter(row.get('evidence_id') for row in report.get('evidence', []) if isinstance(row, dict))
    selected_kinds = Counter()
    for raw in report.get('evidence', []):
        if not isinstance(raw, dict):
            continue
        identifier = raw.get('evidence_id')
        source = raw.get('source')
        reason = None
        if not isinstance(identifier, str) or evidence_ids[identifier] != 1:
            reason = '证据标识缺失或重复'
        elif raw.get('instrument_id') != symbol:
            reason = '证据证券身份与父报告不一致'
        elif source not in _RESEARCH_SOURCES or raw.get('kind') not in _RESEARCH_KINDS:
            reason = '不是本版允许的已核验结构化材料来源或类型'
        elif raw.get('content_hash') != digest(raw.get('data')):
            reason = '已保存证据内容与来源内容哈希不一致，未发送损坏材料'
        elif (source in blocked or raw.get('kind') in blocked
              or capability.get(raw.get('kind')) in blocked
              or (str(raw.get('kind')).startswith('financial_') and 'financial' in blocked)
              or raw.get('state') in ('requires_license', 'external_only')
              or raw.get('egress_allowed') is False or raw.get('ai_processing_allowed') is False):
            reason = '来源限制本次外发或AI处理；不把外链入口当授权材料'
        elif selected_kinds[raw['kind']] >= _RESEARCH_CAPS[raw['kind']]:
            reason = '同类材料达到固定上限；财报每表2期、公告6项、主营6项、其他各1项'
        if reason:
            context['exclusions'].append({'evidence_id': identifier, 'source': source, 'reason': reason})
            continue
        evidence = {key: raw.get(key) for key in ('evidence_id', 'instrument_id', 'title', 'source', 'kind',
                    'tier', 'published_at_ms', 'fetched_at_ms', 'report_period', 'content_hash',
                    'response_json_sha256', 'historical_usable', 'publication_date_known', 'data')}
        evidence['dates'] = {key: raw.get(key) for key in ('published_at_ms', 'fetched_at_ms', 'report_period')}
        evidence['dates']['market_data_cutoff'] = cutoff
        if _add(context, evidence, _research_metrics(evidence, cutoff)):
            selected_kinds[raw['kind']] += 1
    accepted = {row['evidence_id'] for row in context['evidence']}
    seen = set()
    for raw in report.get('claims', []):
        if not isinstance(raw, dict):
            continue
        references = raw.get('evidence_ids', [])
        identifier = raw.get('claim_id')
        if (isinstance(identifier, str) and identifier not in seen and isinstance(raw.get('text'), str)
                and 0 < len(raw['text']) <= 1200 and raw.get('kind') in ('fact', 'source_statement', 'title_clue')
                and references and all(value in accepted for value in references)):
            candidate = {key: raw[key] for key in ('claim_id', 'text', 'kind', 'evidence_ids')}
            used = sum(len(dumps(context[key]).encode()) for key in ('claims', 'evidence', 'metrics'))
            if len(context['claims']) < 32 and used + len(dumps(candidate).encode()) <= CONTENT_BUDGET_BYTES:
                context['claims'].append(candidate)
                seen.add(identifier)
    technical = report.get('technical_assessment', {})
    for period in technical.get('periods', []):
        if not isinstance(period, dict) or period.get('period') not in ('D', 'W', 'M'):
            continue
        identifier = 'technical:' + period['period']
        # Chart series and current forming candles are not sent as closed signals.
        data = {key: value for key, value in period.items() if key != 'current_period'}
        data['analysis_contract'] = technical.get('analysis_contract')
        metrics = []
        observations = period.get('observations', {})
        for key, unit in (('close', 'CNY'), ('ma_fast', 'CNY'), ('ma_slow', 'CNY'),
                          ('ma_fast_previous', 'CNY'), ('slope_pct', '%')):
            if key in observations:
                metrics.append({'key': key, 'label': key, 'value': observations[key], 'unit': unit,
                                'reason': period.get('reason')})
        _add(context, {'evidence_id': identifier, 'source': 'local_research_technical',
                       'kind': 'technical_assessment', 'title': period.get('label', period['period']) + '严格周期观察',
                       'dates': {'market_data_cutoff': cutoff, 'observed_date': period.get('observed_date'),
                                 'expected_date': period.get('expected_date')}, 'data': data},
             _metrics(metrics, identifier, as_of=cutoff,
                      dates={'observed_date': period.get('observed_date'), 'market_data_cutoff': cutoff},
                      scope='按完整' + period['period'] + '周期计数；未复权、严格市场交易日，形成中周期不是信号'))
    _gap(context, 'historical_visibility', '已保存材料是当次取得快照；披露日期已知也不证明完整历史首知链，不能回填历史研究')
    if not technical:
        _gap(context, 'technical_assessment', '父报告未保存v2技术评估；不重新读取行情计算')
    return context


def _source_heads():
    """Small independent publication identities, with no scan of market history."""
    with connect() as connection:
        heads = {row['module']: row['snapshot_id'] for row in connection.execute(
            "SELECT module,snapshot_id FROM published_head WHERE module IN "
            "('calendar','daily_raw','analysis_bars','corporate_actions','security_identity')")}
    path = published_dir() / 'market-supplement' / 'head.json'
    supplement = None
    if path.is_file():
        if path.stat().st_size > 8192:
            supplement = {'state': 'invalid_head'}
        else:
            try:
                body = json.loads(path.read_text(encoding='utf-8'))
                supplement = {key: body.get(key) for key in ('snapshot_id', 'sha256', 'version', 'published_at_ms')}
            except (ValueError, OSError, AttributeError):
                supplement = {'state': 'invalid_head'}
    return {'published_heads': heads, 'market_supplement_head': supplement}


def _dimension(context, identifier, title, data, day, *, dates=None):
    evidence = {'evidence_id': identifier, 'source': 'published_market', 'kind': identifier,
                'title': title, 'dates': dates or {'trade_date': day},
                'data': {key: value for key, value in data.items() if key != 'metrics'}}
    # The evidence link supplies this dimension's full scope once, avoiding
    # four copies of a long index-window/unknown-sample explanation per index.
    metrics = _metrics(data.get('metrics', []), identifier, as_of=day, dates=evidence['dates'])
    claim = None
    if data.get('state_label'):
        claim = {'claim_id': 'claim:' + identifier, 'kind': 'source_statement',
                 'text': title + '：' + data['state_label'] + '。仅为已留存样本的描述。'}
    if _add(context, evidence, metrics, claim):
        if data.get('state') in ('unknown', 'insufficient') or data.get('unknown_count', 0):
            _gap(context, identifier, data.get('reason') or data.get('notice') or
                 '样本覆盖或严格窗口未齐；保留已知统计，不能宣称全市场完整结论')


def _market_context(as_of):
    if as_of is not None:
        _day(as_of)
    with supplement_access():
        before = _source_heads()
        market = get_market()
        status = market.status()
        if not status.get('ready'):
            raise HTTPException(409, '尚无已发布行情，请先手动初始化')
        day = _day(as_of or status.get('data_end'))
        calendar = market.calendar()
        if day not in calendar:
            raise HTTPException(422, '所选日没有已留存的完成交易日行情')
        previous = calendar[calendar.index(day)-1] if calendar.index(day) else None
        dates = tuple(value for value in (previous, day) if value)
        # Sector payloads are requested for T only. T-1 is needed by price
        # feedback pools, not permission to expand the sector history directory.
        pools, pool_fingerprint = load_supplement_with_fingerprint(status['snapshot_id'], dates=dates,
                                                                  fields=SUPPLEMENT_FIELDS[:-1])
        sectors, sector_fingerprint = load_supplement_with_fingerprint(status['snapshot_id'], dates=(day,),
                                                                      fields=('sector_index_history',))
        reference = {**market.reference(), '_supplement': pools}
        rows = market.panel(day)
        context = _base('market_review', 'A股单日盘面复盘', day)
        context['dates']['previous_market_date'] = previous
        context['source_fingerprints'] = {**before, 'market_snapshot_id': status['snapshot_id'],
            'pool_projection_sha256': digest(pool_fingerprint[2:]) if pool_fingerprint else None,
            'sector_projection_sha256': digest(sector_fingerprint[2:]) if sector_fingerprint else None}
        briefs = build_briefs(day)
        context['source_fingerprints']['day_briefs'] = briefs['fingerprints']
        context['gaps'].extend(briefs['gaps'])
        for block in briefs['blocks']:
            _add(context, block['evidence'], block['metrics'])
        sentiment = describe_sentiment(market, reference, rows, day)
        context['source_fingerprints']['sentiment_input_sha256'] = sentiment['provenance']['input_fingerprint']
        _dimension(context, 'market_distribution', '当日严格可比A股分布', sentiment['market'], day)
        for item in sentiment['index_environment']['indices']:
            _dimension(context, 'index:' + item['instrument_id'], item['name'], item, day)
        short = sentiment['short_term']
        _dimension(context, 'previous_limit_up', '前日已确认涨停队列反馈',
                   {key: value for key, value in short.items() if key != 'groups'}, day,
                   dates={'trade_date': day, 'cohort_date': previous})
        for group in short.get('groups', []):
            feedback = group.get('cohort_price_feedback', {})
            if feedback.get('observed_count'):
                _dimension(context, 'cohort:' + group['id'], group['label'] + '价格反馈', feedback, day,
                           dates={'trade_date': day, 'cohort_date': previous})
        _turnover(context, market, day, calendar)
        _sectors(context, sectors, day)
        context['source_provenance'] = _clean(sentiment['provenance'])
        if not pool_fingerprint:
            _gap(context, 'market_supplement_pools', '所选主行情代际没有通过SHA核验的补充池；基础行情证据仍可读')
        if not sector_fingerprint:
            _gap(context, 'market_supplement_sectors', '所选主行情代际没有通过SHA核验的板块补充；不使用当前成员推算')
        _gap(context, 'market_universe', '统计只覆盖已留存报价与可比样本；停牌、缺价与历史证券全集不能自动解释为零')
        _gap(context, 'historical_memberships', '主行情指数观察未纳入历史成员统计；午间/收盘摘要仅复用原冻结名单日期，不以当前成员回填历史')
        _gap(context, 'news_and_intraday', '未纳入新闻、盘中资金时序或连续盘口；不能据日线证明消息归因、盘中承接或封单稳定性')
        _gap(context, 'net_flow', '本版未纳入单独核验的净流入证据；股票/指数成交额均不解释为净流入')
        _gap(context, 'historical_visibility', sentiment['provenance']['notice'])
        after = _source_heads()
        if (before != after or market.status().get('snapshot_id') != status['snapshot_id']
                or brief_fingerprints(day) != briefs['fingerprints']):
            raise HTTPException(409, '构建期间独立来源发布指针变化，请重新预览；未冻结混合输入')
    return context


def _turnover(context, market, day, calendar):
    history = market.turnover_history(day)
    position = calendar.index(day)
    wanted = {day, calendar[position-1]} if position else {day}
    history = [row for row in history if row.get('date') in wanted]
    current = [row for row in history if row.get('date') == day]
    amount = current[0].get('amount') if len(current) == 1 else None
    amount = amount if _finite(amount) and amount >= 0 else None
    comparison = amount_change_metadata(amount, day, calendar, history)
    data = {'date': day, 'amount': amount, 'unit': 'CNY', 'price_basis': 'none',
            'scope': '沪深京已留存A股股票成交额；非净流入，不与沪深广度直接混算',
            'history': history, **comparison}
    values = [('amount', '已留存A股股票成交额', amount, 'CNY'),
              ('amount_change', '同口径相邻交易日成交额变化', comparison['amount_change'], 'CNY'),
              ('amount_change_pct', '同口径相邻交易日成交额变化率', comparison['amount_change_pct'], '%')]
    data['metrics'] = [{'key': key, 'label': label, 'value': value, 'unit': unit,
                        'reason': comparison['amount_change_reason'] if key != 'amount' else
                        ('缺少唯一有效当日成交额' if amount is None else None)} for key, label, value, unit in values]
    _dimension(context, 'turnover', '已留存A股成交额', data, day,
               dates={'trade_date': day, 'previous_date': comparison['amount_previous_date']})
    if comparison['amount_change_reason']:
        _gap(context, 'turnover_comparison', comparison['amount_change_reason'])


def _sectors(context, document, day):
    view = sector_prices_for_date(document, day)
    rows = [row for row in view.get('items', []) if row.get('date') == day and _finite(row.get('change_pct'))]
    chosen = []
    for row in sorted(rows, key=lambda row: (-row['change_pct'], row['topic_code']))[:3] + sorted(
            rows, key=lambda row: (row['change_pct'], row['topic_code']))[:3]:
        if row['topic_code'] not in {item['topic_code'] for item in chosen}:
            chosen.append(row)
    for raw in chosen:
        data = {key: raw.get(key) for key in ('topic_code', 'topic_id', 'thscode', 'name', 'kind', 'source',
                'date', 'quote_date', 'previous_date', 'close', 'previous_close', 'change_pct', 'amount',
                'normalization_version', 'request_signature', 'raw_sha256', 'fetched_at')}
        data.update(price_unit='点', amount_unit='CNY', price_basis='none', member_count=None,
                    limit_up_count=None, limit_down_count=None, scope=view['scope'],
                    selection_scope='指定日有效来源指数涨幅最高/最低各3项；不是历史完整板块目录或投资推荐')
        data['metrics'] = [{'key': 'change_pct', 'label': '板块指数当日涨幅', 'value': raw['change_pct'], 'unit': '%'},
                           {'key': 'amount', 'label': '板块指数成交额', 'value': raw.get('amount'), 'unit': 'CNY'}]
        _dimension(context, 'sector:' + raw['topic_code'], raw['name'] + '来源指数观察', data, day,
                   dates={'trade_date': day, 'previous_date': raw.get('previous_date'),
                          'fetched_at': raw.get('fetched_at')})
    if not view.get('complete'):
        _gap(context, 'sector_index_prices', view.get('unavailable_reason') or '板块指数样本或金额覆盖不完整，缺价不由成员推算')
    if len(rows) > len(chosen):
        context['exclusions'].append({'source': 'sector_index_history',
                                      'reason': '仅外发涨幅最高/最低各3项；其余' + str(len(rows)-len(chosen)) + '项未纳入'})


def _compact_repetitions(context):
    """Lossless defaults: retain every distinct date, unit and source proof.

    A long real-market metric list repeats the observation date and price basis
    dozens of times. The explicit common defaults are part of this same frozen
    object; report periods, cohort dates and source timestamps remain in each
    metric/evidence. Source aliases can also repeat the exact same proof after
    private filesystem references have been removed.
    """
    context['metric_defaults'] = {'as_of': context['as_of'], 'price_basis': context['price_basis']}
    for metric in context['metrics']:
        for key, value in context['metric_defaults'].items():
            if metric.get(key) == value:
                metric.pop(key, None)
    provenance = context.get('source_provenance', {})
    records = provenance.get('source_records')
    if isinstance(records, list):
        unique = []
        seen = set()
        for record in records:
            identity = dumps(record)
            if identity not in seen:
                seen.add(identity)
                unique.append(record)
        provenance['source_record_count'] = len(records)
        provenance['source_records_duplicate_count'] = len(records) - len(unique)
        provenance['source_records'] = unique


def _finalize(context, excluded):
    """Include IDs, gaps, defaults, notices and the SHA in the actual byte cap."""
    context.pop('context_sha256', None)
    context.pop('_content_budget_bytes', None)
    if excluded:
        if not any(row.get('capability') == 'input_projection' for row in context['gaps']):
            _gap(context, 'input_projection', '部分条目因来源限制或有界预算未纳入；结论范围必须局限于实际提供的条目', state='partial')
        # Thousands of excluded announcement IDs must not themselves exhaust
        # the bounded input. Keep a sample and exact counts for every reason.
        context['exclusion_summary'] = [
            {'source': source, 'reason': reason, 'count': count}
            for (source, reason), count in sorted(Counter(
                (str(row.get('source') or 'unknown'), row['reason']) for row in excluded).items())]
        context['exclusions'] = excluded[:8]
        context['excluded_count'] = len(excluded)
        context['exclusion_details_truncated'] = len(excluded) > 8
    context['allowed_ids'] = {'claim_ids': [row['claim_id'] for row in context['claims']],
                              'evidence_ids': [row['evidence_id'] for row in context['evidence']],
                              'metric_ids': [row['metric_id'] for row in context['metrics']]}
    eligible = bool(context['evidence'])
    context['eligibility'] = {'eligible': eligible,
                               'state': 'partial' if eligible and context['gaps'] else 'available' if eligible else 'unavailable',
                               'reason': None if eligible else '没有可发送的已核验材料；不会联网补抓'}
    context['context_sha256'] = digest(context)
    return len(dumps(context).encode())


def _bounded_context(context):
    _compact_repetitions(context)
    excluded = list(context['exclusions'])
    while _finalize(context, excluded) > MAX_CONTEXT_BYTES:
        # Exact final metadata can exceed the admission reserve even when all
        # individual items fit. Project whole optional observations only; never
        # cut a numeric field, unit, date, source proof or limitation in half.
        candidates = [row for row in reversed(context['evidence'])
                      if row['evidence_id'].startswith(('sector:', 'cohort:', 'technical:'))]
        if not candidates:
            raise HTTPException(422, '必要来源、日期、单位和缺口说明超过64KiB，不能安全裁剪，请缩小材料范围')
        removed = candidates[0]
        identifier = removed['evidence_id']
        context['evidence'] = [row for row in context['evidence'] if row['evidence_id'] != identifier]
        context['metrics'] = [row for row in context['metrics'] if identifier not in row['evidence_ids']]
        context['claims'] = [row for row in context['claims'] if identifier not in row['evidence_ids']]
        excluded.append({'evidence_id': identifier, 'source': removed.get('source'),
                         'dates': removed.get('dates'),
                         'reason': '完整输入含来源、ID、缺口与哈希后超过64KiB；整条可选观察排除，未截断单位或日期'})
    return context


def build_context(kind, report_id=None, as_of=None):
    """Return a new frozen <=64 KiB input; never alter saved contexts or TTL."""
    normalized = KINDS.get(kind) if isinstance(kind, str) else None
    if normalized is None:
        raise HTTPException(422, 'AI上下文仅支持stock_research或market_review')
    if normalized == 'market_review' and report_id is not None:
        raise HTTPException(422, '盘面复盘使用独立观察日，不绑定个股报告')
    context = _research_context(report_id, as_of) if normalized == 'stock_research' else _market_context(as_of)
    return _bounded_context(context)


def build_context_preview(kind, report_id=None, as_of=None):
    context = build_context(kind, report_id=report_id, as_of=as_of)
    byte_count = len(dumps(context).encode())
    sources = Counter(row['source'] for row in context['evidence'])
    preview = {key: context[key] for key in ('kind', 'subject', 'as_of', 'dates')}
    preview.update({key: context[key] for key in ('instrument_id', 'source_report_id') if key in context})
    preview.update(evidence_count=len(context['evidence']), claim_count=len(context['claims']),
                   metric_count=len(context['metrics']), gap_count=len(context['gaps']),
                   sources=[{'source': source, 'count': count} for source, count in sorted(sources.items())],
                   byte_count=byte_count, state=context['eligibility']['state'])
    return {'eligible': context['eligibility']['eligible'], 'context_digest': context['context_sha256'],
            'context_sha256': context['context_sha256'], 'context': context, 'preview': preview,
            'evidence': [{key: row.get(key) for key in ('evidence_id', 'source', 'kind', 'title', 'dates')}
                         for row in context['evidence']],
            'exclusions': context['exclusions'], 'bytes': byte_count, 'notice': context['notice']}


_MODEL_AUDIT_KEYS = {
    'context_sha256', 'content_hash', 'content_sha256', 'response_json_sha256',
    'request_signature', 'config_signature', 'source_ref', 'raw_artifact', 'artifact',
    'manifest_path', 'path', 'file_path', 'snapshot_refs', 'source_fingerprints',
    'source_provenance', 'exclusions', 'exclusion_summary', 'computed_at',
    'normalization_version', 'algorithm_version', 'indicator_version',
}
_MODEL_NULL_METADATA = {'source', 'title', 'tier', 'url', 'document_url', 'reason', 'formula', 'label'}


def _model_data(value):
    """Remove audit/secret metadata, preserving unknown prices and dates as null."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lower = str(key).lower()
            if (lower in _SECRET_KEYS or lower in _MODEL_AUDIT_KEYS
                    or lower.endswith(('_sha256', '_fingerprint', '_hash', '_path', '_artifact'))):
                continue
            if item is None and lower in _MODEL_NULL_METADATA:
                continue
            result[str(key)] = _model_data(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_model_data(item) for item in value]
    return json_clean(value)


def model_input(context):
    """Pure, lossless factual projection for the v2 conversational protocol.

    It does not build context, consult a parent report, mutate retention or
    change the persisted object. All original allowed IDs remain available;
    invalid/dangling source bindings fail closed instead of narrowing them
    silently. An old session can keep sending its original v1 input unchanged.
    """
    if not isinstance(context, dict):
        raise ValueError('invalid_context')
    evidence = context.get('evidence', [])
    claims = context.get('claims', [])
    metrics = context.get('metrics', [])
    allowed = context.get('allowed_ids', {})
    for kind, rows, field in (('evidence_ids', evidence, 'evidence_id'),
                              ('claim_ids', claims, 'claim_id'),
                              ('metric_ids', metrics, 'metric_id')):
        values = [row.get(field) for row in rows]
        identifiers = allowed.get(kind, [])
        if (not all(isinstance(value, str) for value in values)
                or len(values) != len(set(values)) or set(values) != set(identifiers)):
            raise ValueError('invalid_context_references')
    evidence_ids = set(allowed.get('evidence_ids', []))
    if any(not set(row.get('evidence_ids', [])) <= evidence_ids for row in [*claims, *metrics]):
        raise ValueError('invalid_context_references')

    result = {key: _model_data(context[key]) for key in (
        'kind', 'subject', 'as_of', 'instrument', 'instrument_id', 'dates',
        'price_basis', 'indicator_continuity', 'notice') if key in context}
    result['evidence'] = [{key: _model_data(row[key]) for key in (
        'evidence_id', 'title', 'source', 'kind', 'tier', 'dates', 'data',
        'published_at_ms', 'fetched_at_ms', 'report_period', 'official_document',
        'historical_usable', 'publication_date_known') if key in row and row[key] is not None}
        for row in evidence]
    for item in result['evidence']:
        for key in ('published_at_ms', 'fetched_at_ms', 'report_period'):
            if key in item and item.get('dates', {}).get(key) == item[key]:
                item.pop(key)
    # Null source publication/report dates remain explicit inside dates/data.
    result['claims'] = [{key: _model_data(row[key]) for key in (
        'claim_id', 'text', 'kind', 'evidence_ids') if key in row} for row in claims]
    defaults = context.get('metric_defaults') or {}
    result['metrics'] = [_model_data({**defaults, **row}) for row in metrics]
    common = {}
    for key in ('as_of', 'price_basis', 'scope'):
        values = [row.get(key) for row in result['metrics']]
        if values and values[0] is not None and all(value == values[0] for value in values):
            common[key] = values[0]
            for row in result['metrics']:
                row.pop(key, None)
    if common:
        result['metric_defaults'] = common
    by_id = {row['evidence_id']: row for row in result['evidence']}
    # The same scope is often repeated for five metrics from one source row.
    # Store it once on that evidence; evidence_ids retain the exact association.
    for identifier, item in by_id.items():
        related = [row for row in result['metrics'] if row.get('evidence_ids') == [identifier]]
        scopes = {row.get('scope') for row in related}
        if len(scopes) == 1 and next(iter(scopes), None):
            item['metric_scope'] = next(iter(scopes))
            for row in related:
                row.pop('scope', None)
    result['gaps'] = _model_data(context.get('gaps', []))
    if context.get('source_coverage'):
        result['source_coverage'] = _model_data(context['source_coverage'])
    included = Counter((row.get('source', 'unknown'), row.get('kind', 'unknown')) for row in evidence)
    excluded = context.get('exclusion_summary')
    if excluded is None:
        excluded = [{'source': source, 'reason': reason, 'count': count}
                    for (source, reason), count in sorted(Counter(
                        (str(row.get('source') or 'unknown'), row.get('reason', '未纳入'))
                        for row in context.get('exclusions', [])).items())]
    result['material_scope'] = {
        'notice': '仅使用下列实际纳入的证据；source_coverage是原报告覆盖，不代表全部已发送。排除数量按记录计，单条记录可描述更大对象范围。',
        'included': [{'source': source, 'kind': kind, 'count': count}
                     for (source, kind), count in sorted(included.items())],
        'excluded_ranges': [{'source': row.get('source'), 'reason': row.get('reason'),
                             'record_count': row.get('count')} for row in excluded],
        'excluded_record_count': context.get('excluded_count', len(context.get('exclusions', []))),
        'exclusion_details_truncated': bool(context.get('exclusion_details_truncated')),
    }
    provenance = context.get('source_provenance') or {}
    if provenance:
        dates = {key: provenance[key] for key in (
            'trade_date', 'mode', 'market_collected_at', 'first_known_at', 'known_time_complete', 'notice')
                 if key in provenance}
        records = []
        seen = set()
        for row in provenance.get('source_records', []):
            record = {key: row[key] for key in ('source', 'trade_date', 'pool', 'fetched_at',
                      'source_published_at', 'first_known_at') if key in row}
            signature = dumps(record)
            if signature not in seen:
                records.append(record)
                seen.add(signature)
        if records:
            dates['sources'] = records
        result['source_dates'] = _model_data(dates)
    result['allowed_ids'] = {key: list(allowed.get(key, [])) for key in ('claim_ids', 'evidence_ids', 'metric_ids')}
    return result
