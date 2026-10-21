"""Small coverage read models: denominators are source evidence, never file size."""
import json


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def measure(label, observed, expected, unit, dimension, basis, reason=None, *, on_demand=False):
    observed, expected = count(observed), count(expected)
    valid = observed is not None and expected is not None and expected > 0 and observed <= expected
    if observed is not None and expected is not None and observed > expected:
        reason = '分子超过已声明分母，覆盖比例待核验'
    percent = round(observed / expected * 100, 4) if valid else None
    if valid and observed < expected:
        # Rounding a very small remaining gap must not promote it to 100%.
        percent = min(percent, 99.9999)
    coverage_status = 'on_demand' if on_demand else 'covered' if valid and observed == expected else 'partial' if valid else 'unknown'
    coverage_label = {'covered': '当前范围已覆盖', 'partial': '当前范围有缺口',
                      'unknown': '覆盖待核验', 'on_demand': '按需计算'}[coverage_status]
    return {'label': label, 'observed_count': observed, 'expected_count': expected,
            'percent': percent,
            'unit': unit, 'dimension': dimension, 'basis': basis,
            'state': 'known' if valid else 'unknown', 'reason': reason,
            'coverage_status': coverage_status, 'coverage_label': coverage_label,
            'scope_complete': True if coverage_status == 'covered' else False if coverage_status == 'partial' else None}


def snapshot_quality_view(row, progress):
    """Publication quality and today's narrow measurement answer different questions."""
    quality = row.get('quality')
    note = {
        'calendar': '当前比例只核验冻结窗口来源交易日；较早来源推导日期不等同交易所官方完整历史日历。',
        'security_identity': '当前名录中的上市日期完整度，与历史退市证券及逐日身份覆盖分开判断。',
        'daily_raw': '已返回行情不等于逐股预期记录全部核验；停牌、漏报和未知上市日期需要单独解释。',
        'corporate_actions': '已返回公司行动事件没有独立的来源应有总量，不代表全部事件或完整复权验收。',
        'analysis_bars': '分析输入沿用原始行情范围；日周月聚合不消除原始行情的身份与报价缺口。',
        'indicators': '指标按观察日及日周月周期计算；各指标预热和缺口规则不同，不能由输入行数推定全部可用。',
        'topics': '当前目录成员组完整，不代表历史每日目录及成分归属也已留存。',
        'market_review': '比例仅限同盘面日涨停、跌停、炸板三来源池；异动监管历史与盘中时序不在该分母内。',
    }.get(row.get('module'), '当前测量只对声明的维度和范围负责，不代表所有历史数据。')
    return {'snapshot_quality': quality,
            'snapshot_quality_label': {'ready': '发布时已核验', 'partial': '发布时部分覆盖', 'unknown': '发布时待核验'}.get(quality, '发布质量待核验'),
            'snapshot_quality_note': note+' 发布质量保留原始记录；任务是否结束以任务状态为准。',
            'coverage_status': progress['coverage_status'], 'coverage_label': progress['coverage_label']}


def dataset_progress(row, reference=None, generation=None, raw_coverage=None, local_report=None):
    """At most a few hundred directory groups/days; never scan daily bars."""
    module = row['module']
    lineage = json.loads(row.get('lineage_json') or '{}')
    coverage = lineage.get('coverage') or {}
    observed, expected = row.get('observed_rows'), row.get('expected_rows')
    same_generation = bool(generation and lineage.get('market_snapshot_id') == generation)
    reference = reference if same_generation and isinstance(reference, dict) else {}
    if module == 'security_identity':
        stocks=[item for item in reference.get('master',{}).values() if item.get('kind')=='stock']
        if stocks:
            from . import listing_status
            as_of=row.get('data_end') or max(reference.get('calendar',[]),default='')
            identities=(local_report or {}).get('identities') if (local_report or {}).get('snapshot_id')==generation else None
            if identities is None:identities=listing_status.read_summary(generation,as_of,reference)
            if identities is None:
                identities=listing_status.resolve(reference,as_of,confirmations=listing_status.confirmations(),market_snapshot_id=generation)
            known=identities['listed_count'];pending=identities['pending_listing_count'];unknown=identities['unknown_count']
            result=measure('已上市股票日期覆盖',known,known+unknown,'只','listing_dates',
                           '当前目录剔除指数和有证据的待上市证券；尚未知身份仍占分母，已上市日期完整不证明历史退市全集',
                           str(unknown)+'只证券上市状态或日期待核验，不以首根K线代替上市日' if unknown else None)
            result.update(catalog_count=len(stocks),listed_count=known,pending_listing_count=pending,unknown_count=unknown,
                          listing_revision=identities.get('listing_revision'),pending_listing=identities.get('pending_listing',[]))
            return result
        unknown = count(coverage.get('identity_unknown_count'))
        total = count(observed)
        return measure('上市日期已核验', total - unknown if total is not None and unknown is not None and unknown <= total else None,
                       total, '只', 'listing_dates', '分母为本次已收录证券名录，不代表历史上市/退市全集；分子为其中上市日期已知的证券',
                       '未提供上市日期缺口统计' if unknown is None else None)
    if module == 'calendar':
        provenance = coverage.get('calendar_provenance') or {}
        count_key = 'retained_calendar_count' if 'retained_calendar_count' in provenance else 'count'
        declared = count(provenance.get(count_key))
        end = (provenance.get('recent_extension') or {}).get('end') or provenance.get('end')
        if provenance.get('basis') == 'source_derived_cross_checked' and end == row.get('data_end'):
            return measure('冻结窗口交易日覆盖', observed, declared, '日', 'trading_days',
                           '分母为本次冻结窗口已交叉核验的来源交易日数；早期由行情与指数推导，近期与官方日历核对，不代表完整官方历史日历')
        return measure('交易日覆盖', observed, expected, '日', 'trading_days', '使用当前快照明确登记的应有交易日数',
                       '尚无同窗口已核验的应有交易日总数' if expected is None else None)
    if module in ('daily_raw', 'analysis_bars'):
        checked=(local_report or {}).get('daily',{})
        raw_verified=any(item.get('role')=='raw-daily.parquet' and item.get('sha256_verified') is True and item.get('schema_verified') is True for item in (local_report or {}).get('file_checks',[]))
        if checked.get('ready') is True and same_generation and (local_report or {}).get('snapshot_id')==generation and (module!='daily_raw' or raw_verified):
            unknown=len(checked.get('unknown_identities',[]))
            result=measure('已知上市范围报价出现率',checked.get('observed_count'),checked.get('expected_count'),'条','known_stock_sessions',
                           checked['scope']+'；排除'+str(unknown)+'只身份日期未知股票，不代表全市场历史完整',
                           str(checked.get('unquoted_sessions',0))+'个证券交易日无有效报价；停牌、来源缺报与身份变化尚未逐一解释，不填造K线')
            result['coverage_label']='存在无报价日' if result['coverage_status']=='partial' else '区间均有报价'
            result['shared_diagnosis_id']=checked.get('gap_diagnosis',{}).get('diagnosis_id')
            result['shared_modules']=['daily_raw','analysis_bars']
            return result
        if module == 'analysis_bars':
            coverage = raw_coverage or {}
        unknown = count(coverage.get('identity_unknown_count'))
        if unknown:
            expected = None
            reason = f'{unknown}只证券上市日期未知，无法核验逐股应有交易日记录；已确认缺口下界不能当作全部缺口'
        else:
            reason = '尚未登记逐股上市/退市范围内应有交易日记录总数；停牌与漏报缺口分开核验' if expected is None else None
        return measure('日行情记录覆盖' if module == 'daily_raw' else '分析行情输入覆盖', observed, expected, '条', 'daily_records',
                       '分母必须来自证券有效期与交易日历的明确预期记录；不是股票数×窗口天数，也不是记录数占全库比例', reason)
    if module == 'indicators':
        return measure('指标可用率', None, None, '项', 'indicator_readiness',
                       '按需计算日/周/月指标；输入行情记录数不能替代指标预热完成数', 'MA/MACD/KD等各有预热与缺口规则，尚未单独统计应有及可用指标总数', on_demand=True)
    if module == 'corporate_actions':
        value=measure('已留存公司行动',observed,None,'条','action_events',
                      '本地文件及字段可核验；全部应有事件数须有独立来源总量，不能由已返回数推定',
                      '来源未提供全市场应有事件总量，无法计算完整率')
        value['coverage_label']='来源总量未提供'
        return value
    if module == 'topics':
        catalog = reference.get('concept_catalog', []) + reference.get('industry_catalog', [])
        codes = {item.get('thscode') or item.get('topic_code') for item in catalog if isinstance(item, dict)} - {None, ''}
        complete = {item.get('topic_code') or item.get('code') for item in reference.get('topics', [])
                    if isinstance(item, dict) and item.get('members_complete') is True}
        if codes:
            return measure('当前目录成员组完整度', len(codes & complete), len(codes), '组', 'topic_groups',
                           '分母为该代际行业与概念目录的去重组数；分子为来源明确完整的成员组。各组采集日独立，不代表历史成员覆盖')
        return measure('成员组覆盖', None, None, '组', 'topic_groups', '需要与该快照同代际的完整目录和逐组完成证据', '尚无同代际目录分母，成员关系条数不等于已覆盖组数')
    if module == 'market_review':
        pools = reference.get('pool_coverage') or {}
        chosen = [pools.get(key) or {} for key in ('limitup', 'limitdown', 'breakpool')]
        known = bool(reference.get('pool_trade_date') == row.get('data_end')) and all(pool.get('complete') is True and count(pool.get('declared_total')) is not None and count(pool.get('observed_rows')) == count(pool.get('declared_total')) for pool in chosen)
        return measure('涨跌停与炸板三池覆盖', sum(pool['observed_rows'] for pool in chosen) if known else None,
                       sum(pool['declared_total'] for pool in chosen) if known else None, '条', 'event_pool_rows',
                       '分母为该盘面日涨停/跌停/炸板来源池声明总数之和；异动、连板历史与未接入事件不在此比例中',
                       '缺少同盘面日完整的来源池总数' if not known else None)
    return measure('覆盖完整度', observed, expected, '条', 'records', '仅使用快照明确登记的应有记录总数', '应有总量待核验' if expected is None else None)
