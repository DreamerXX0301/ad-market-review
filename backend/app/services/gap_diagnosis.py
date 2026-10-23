"""Bounded local quote-gap episodes; patterns never establish a suspension.

This module compares retained raw source rows with the derived daily series.
It performs no requests or writes and is shared by raw/analysis coverage.
"""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from hashlib import sha256
import json

VERSION = 'local-gap-episodes-v1'
MAX_EPISODES = 10000
LONG_RUN_SESSIONS = 5

IMPACT = '日K不填造价格；严格前交易日比较在缺口边界待核验，相关周/月K与指标需按实际报价及预热规则判断'
NOTICE = '连续指市场交易日连续，周末和节假日不打断；形态不等于停牌。原始包无报价也不证明远端无数据或已停牌。'


def _id(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


def stock_episodes(calendar, missing, quoted, instrument_id, name, *, date_budget, episode_budget):
    """Return complete counts and bounded episode/date detail for one stock."""
    positions = {day: index for index, day in enumerate(calendar)}
    missing = sorted(set(missing), key=positions.__getitem__)
    quoted = sorted(day for day in quoted if day in positions)
    runs = []
    for day in missing:
        if runs and positions[day] == positions[runs[-1][-1]] + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    episodes = []
    used = 0
    for run in runs:
        if len(episodes) >= episode_budget or used >= date_budget:
            break
        dates = run[:max(0, date_budget-used)]
        used += len(dates)
        count = len(run)
        shape = 'isolated' if count == 1 else 'long_consecutive' if count >= LONG_RUN_SESSIONS else 'consecutive'
        left = bisect_left(quoted, run[0]); right = bisect_right(quoted, run[-1])
        identifier = _id([instrument_id, run[0], run[-1]])
        episodes.append({'episode_id': identifier, 'gap_id': identifier,
                         'start_date': run[0], 'end_date': run[-1], 'missing_sessions': count,
                         'dates': dates, 'dates_complete': len(dates) == count, 'shape': shape,
                         'shape_label': {'isolated': '孤立1个交易日', 'consecutive': '连续无报价', 'long_consecutive': '较长连续无报价'}[shape],
                         'previous_quote_date': quoted[left-1] if left else None,
                         'next_quote_date': quoted[right] if right < len(quoted) else None,
                         'evidence_state': 'local_only', 'confirmed_suspension': None, 'source_verification': None,
                         'impact': IMPACT})
    summary = {'instrument_id': instrument_id, 'code': instrument_id.split('.')[0], 'name': name,
               'missing_sessions': len(missing), 'episode_count': len(runs),
               'longest_episode_sessions': max((len(run) for run in runs), default=0),
               'isolated_episode_count': sum(len(run) == 1 for run in runs),
               'consecutive_episode_count': sum(len(run) > 1 for run in runs),
               'long_episode_count': sum(len(run) >= LONG_RUN_SESSIONS for run in runs),
               'long_session_count': sum(len(run) for run in runs if len(run) >= LONG_RUN_SESSIONS),
               'episodes': episodes, 'details_complete': len(episodes) == len(runs) and used == len(missing)}
    return summary, used


def attach_local_evidence(stocks, raw_presence, *, raw_verified):
    """Use only reported pairs; absent raw evidence remains explicitly unknown."""
    for stock in stocks:
        for episode in stock['episodes']:
            dates = episode['dates']
            proofs = [raw_presence.get((stock['instrument_id'], day), False) for day in dates]
            present = sum(proof.get('valid_quote', False) if isinstance(proof, dict) else bool(proof) for proof in proofs)
            conflicts = sum(isinstance(proof, dict) and proof.get('row_count', 0)>0 and not proof.get('valid_quote') for proof in proofs)
            complete = episode['dates_complete']
            if not raw_verified or not complete:
                kind = 'unverified_raw'; label = '本地来源对照待完成'
                reason = '原始文件未通过核验或本区间明细超过报告上限，尚不能判定本地来源包是否有完整报价'
                next_action = '先完成原始文件核验或扩大明确诊断范围，再核对可用来源；不自动补价格'
            elif present == len(dates):
                kind = 'local_normalization_difference'; label = '原始与分析序列有差异'
                reason = '本地原始来源包有唯一有效报价，但对应规范日K未计入有效报价，需要核对转换或重复键'
                next_action = '可先依据已留存原始报价核验规范化过程；确认后在独立新代际重建，保留旧版本'
            elif conflicts:
                kind = 'local_quote_conflict'; label = '本地原始报价需核验'
                reason = '本地原始包留有部分重复键或无效价格记录，不能作为唯一有效报价，也不能直接当作来源没有返回'
                next_action = '先核对本地原始记录的重复键和价格字段；有确切有效原始报价后再独立重建规范序列'
            elif present:
                kind = 'mixed_local_evidence'; label = '本地来源证据不一致'
                reason = '该连续区间只有部分日期在本地原始来源包中找到唯一有效报价，须分日期核验'
                next_action = '分别核对有原始报价的转换差异与无原始报价的来源记录，不按同一原因批量填补'
            else:
                kind = 'source_unquoted_unknown'; label = '本地来源包也无有效报价'
                reason = '当前保留的原始来源包同样未检出这些日期的唯一有效报价；尚无同期停牌或来源漏报证据'
                next_action = '按已归并区间与可用来源或正式停复牌材料核对；来源仍无报价时保留原因待核验'
            episode.update(diagnosis_type=kind, type_label=label, reason=reason, next_action=next_action,
                           source_comparison={'state': 'unverified' if not raw_verified or not complete else 'raw_present' if present == len(dates) else 'conflict' if conflicts else 'mixed' if present else 'raw_absent',
                                              'raw_available_sessions': present if raw_verified else None,
                                              'raw_absent_sessions': len(dates)-present-conflicts if raw_verified else None,
                                              'raw_conflict_sessions': conflicts if raw_verified else None,
                                              'compared_sessions': len(dates) if raw_verified else 0,
                                              'basis': '仅比较当前已留存原始来源包与规范日K，不访问远端，不据空结果认定停牌'})


def raw_comparison(con, path, stocks):
    """One bounded join for reported gap pairs, never a per-stock file scan."""
    pairs = [(stock['instrument_id'], day) for stock in stocks for episode in stock['episodes'] for day in episode['dates']]
    if path is None or not pairs:
        return {}
    con.execute('CREATE TEMP TABLE requested_gaps AS SELECT unnest(?) instrument_id,unnest(?) date',
                [[pair[0] for pair in pairs], [pair[1] for pair in pairs]])
    valid = """open_price IS NOT NULL AND high_price IS NOT NULL AND low_price IS NOT NULL AND close_price IS NOT NULL
        AND volume IS NOT NULL AND turnover IS NOT NULL AND isfinite(open_price) AND isfinite(high_price)
        AND isfinite(low_price) AND isfinite(close_price) AND isfinite(volume) AND isfinite(turnover)
        AND least(open_price,high_price,low_price,close_price)>0 AND volume>=0 AND turnover>=0
        AND high_price>=greatest(open_price,low_price,close_price) AND low_price<=least(open_price,high_price,close_price)"""
    records = con.execute("""WITH raw AS (
        SELECT thscode,try(strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d')) date,
               ("""+valid+""") is_valid FROM read_parquet(?) WHERE thscode IN (SELECT DISTINCT instrument_id FROM requested_gaps))
        SELECT wanted.instrument_id,wanted.date,count(raw.thscode) copies,bool_and(raw.is_valid) is_valid
        FROM requested_gaps wanted LEFT JOIN raw ON wanted.instrument_id=raw.thscode AND wanted.date=raw.date
        GROUP BY wanted.instrument_id,wanted.date""", [str(path)]).fetchall()
    return {(symbol, day): {'valid_quote': copies == 1 and valid is True, 'row_count': copies} for symbol, day, copies, valid in records}


def summary(stocks, *, source_signature=None):
    keys = ('missing_sessions', 'episode_count', 'isolated_episode_count', 'consecutive_episode_count',
            'long_episode_count', 'long_session_count')
    totals = {key: sum(row[key] for row in stocks) for key in keys}
    types = {}
    for stock in stocks:
        for episode in stock['episodes']:
            types[episode['diagnosis_type']] = types.get(episode['diagnosis_type'], 0) + 1
    return {'version': VERSION, 'diagnosis_id': _id([VERSION, source_signature, [(row['instrument_id'], row['missing_sessions'], row['episode_count']) for row in stocks]]),
            'shared_modules': ['daily_raw', 'analysis_bars'], 'total_missing_sessions': totals.pop('missing_sessions'),
            'stock_count': len(stocks), **totals, 'reported_missing_sessions': sum(len(e['dates']) for row in stocks for e in row['episodes']),
            'details_complete': all(row['details_complete'] for row in stocks), 'long_run_min_sessions': LONG_RUN_SESSIONS,
            'type_episode_counts': types, 'notice': NOTICE, 'stocks': stocks}


def overlay_remote_verification(report, evidence):
    """Attach already saved evidence only when its exact local inputs match.

    Remote absence remains a source observation, never a suspension diagnosis.
    The original local report/artifact is immutable; callers serialize this
    derived view or return it without writing the report back.
    """
    result = deepcopy(report)
    diagnosis = result.get('daily', {}).get('gap_diagnosis')
    if not diagnosis or not isinstance(evidence, dict):
        return result
    if any(evidence.get(key) != report.get(key) for key in ('snapshot_id', 'source_signature')) or evidence.get('diagnosis_id') != diagnosis['diagnosis_id']:
        diagnosis['source_verification_stale'] = True
        return result
    allowed = {'source_no_quote', 'local_missing', 'partial', 'failed', 'matched', 'local_difference'}
    records = evidence.get('gaps', {})
    for stock in diagnosis.get('stocks', []):
        for episode in stock['episodes']:
            item = records.get(episode['gap_id']) if isinstance(records, dict) else None
            if not isinstance(item, dict) or item.get('status') not in allowed:
                continue
            # Prevent a receipt from broadening the dates of this exact gap.
            dates = set(episode['dates'])
            copied = {key: value for key, value in item.items() if key not in ('confirmed_suspension',)}
            for key in ('missing_dates', 'returned_dates', 'matched_dates', 'local_difference_dates'):
                if key in copied:
                    copied[key] = sorted(day for day in copied[key] if isinstance(day,str) and day in dates) if isinstance(copied[key],list) else []
            for key in ('checked_at_ms', 'source'):
                copied.setdefault(key,evidence.get(key))
            copied.update({key:evidence.get(key) for key in ('receipt_id','receipt_sha256')})
            episode['source_verification'] = copied
    diagnosis['source_verification_stale'] = False
    diagnosis['source_verification_summary']={key:evidence.get(key) for key in (
        'source','checked_at_ms','receipt_id','receipt_sha256','planned_stocks','checked_stocks','failed_stocks',
        'checked_sessions','fillable_sessions','source_absent_sessions','overlap_difference_sessions','publication_changed')}
    return result
