"""Project a Messages & Events page selection from the retained local archive."""
from collections import Counter
from datetime import date
from itertools import zip_longest

from app.core import digest, dumps
from app.services import news_events

TOOL_NAME = 'read_news_events'
TOOL_ARGS = {'scope': 'selection'}
MAX_DAY_ITEMS = 20
MAX_ARCHIVE_BYTES = 20 * 1024
TOOL_DESCRIPTION = (
    '读取这个消息与事件会话固定的观察日或所选条目。参数只能传 scope=selection。'
    '资料是本地留存的原文标题、已有摘要、发布时间、首次收录和来源链接。'
    '区分隔夜原文发布时间与页面归档日，日历暂定、延期或取消不能当作已发生。'
    '缺少摘要时只解读标题，来源标签不是行情对照或受益证明。'
)
PROMPT = (
    '这个会话从消息与事件页面进入。遇到解读所选消息、日历事件或当天消息，请用 '
    'read_news_events(scope=selection) 读取已绑定的本地归档。'
    '用户确实在请求解读本页所选消息或事件时，按 market_review 或 stock_impact 处理；'
    '页面来源不能为明确无关的问题授予读取权限。'
    '区分来源报道、日历安排和推测。只根据已返回的标题与摘要说明已知事实；'
    '标明出处、原始日期和覆盖缺口，不声称读过原网页全文。'
    '讨论股票影响时列出有条件的影响路径和需要验证的指标，不把来源标签当作因果。'
)


def validate_selection(as_of, selection):
    if not isinstance(as_of, str):
        raise ValueError('消息与事件需要明确的 YYYY-MM-DD 观察日')
    observed = date.fromisoformat(as_of)
    if observed.isoformat() != as_of or not 1900 <= observed.year < 9999:
        raise ValueError('消息与事件需要明确的 YYYY-MM-DD 观察日')
    if not isinstance(selection, dict):
        raise ValueError('消息与事件范围无效')
    kind = selection.get('kind')
    if kind == 'day' and set(selection) == {'kind'}:
        return
    fields = {'kind', 'source', 'id'}
    if kind == 'event':
        if type(selection.get('days')) is not int or selection['days'] not in (7, 30):
            raise ValueError('事件日历只支持 7 或 30 天范围')
        fields.add('days')
    if kind not in ('news', 'event') or set(selection) != fields:
        raise ValueError('消息与事件范围无效')
    for key, maximum in (('source', 120), ('id', 200)):
        value = selection[key]
        if (not isinstance(value, str) or not value or value.strip() != value
                or len(value) > maximum or any(ord(char) < 32 for char in value)):
            raise ValueError('归档来源和标识格式无效')


def _gap(result, capability, reason, state='unknown'):
    result['gaps'].append({'capability': capability, 'date': result['date'],
                           'state': state, 'reason': reason})


def _evidence(item, kind, dates):
    data = {key: item.get(key) for key in ('url', 'summary')}
    for key in ('topics', 'symbols'):
        if key in item:
            data[key] = item[key][:4]
            data[key + '_archived_count'] = len(item[key])
    for key in ('status', 'event_id'):
        if key in item:
            data[key] = item[key]
    return {'evidence_id': kind + ':' + digest([item['source'], item['id']]),
            'kind': kind, 'source': item['source'], 'title': item['title'],
            'dates': dates, 'data': data}


def _calendar(result, selection):
    day = result['date']
    calendar = news_events.read_calendar(day, days=selection['days'])
    sources = calendar['sources']
    result['summary'].update(end_date=calendar['end_date'], calendar_status=calendar['status'],
                             calendar_source_count=len(sources), archived_count=len(calendar['events']))
    if calendar['reason']:
        _gap(result, 'calendar_coverage', calendar['reason'], calendar['status'] if not sources else 'partial')
    matched = next((item for item in calendar['events']
                    if item['id'] == selection['id'] and item['source'] == selection['source']), None)
    if matched is None:
        _gap(result, 'selected_event', '所选观察日和日历范围内没有可核验的这条事件归档')
        return
    # The merged calendar does not identify each row's contributing revision.
    # A sole revision is unambiguous; with multiple revisions retain unknown.
    source = sources[0] if len(sources) == 1 else {}
    if source:
        result['summary']['calendar_source'] = source
    result['evidence'].append(_evidence(matched, 'calendar_event', {
        'observation_date': day, 'event_date': matched['event_date'],
        'published_at': matched['published_at'], 'published_date': matched['published_date'],
        'archive_captured_at': source.get('captured_at'),
    }))


def _messages(result, selection):
    day = result['date']
    view = news_events.read_view(day)
    entries = {}
    for stage in view['stages']:
        for item in stage['items']:
            entries.setdefault((item['source'], item['id']), (item, stage))
    coverage = [{**{key: stage[key] for key in ('stage', 'status', 'window_start_at',
                 'cutoff_at', 'captured_at', 'reason')}, 'archived_count': len(stage['items'])}
                for stage in view['stages']]
    result['summary'].update(stages=coverage, archived_count=len(entries))
    for stage in coverage:
        if stage['status'] != 'complete':
            _gap(result, stage['stage'], stage['reason'] or '该档采集覆盖未确认完整', stage['status'])
    if selection['kind'] == 'news':
        chosen = entries.get((selection['source'], selection['id']))
        if chosen is None:
            _gap(result, 'selected_news', '所选观察日没有可核验的这条消息归档')
            return
        ordered = [chosen]
    else:
        groups = [[row for row in entries.values() if row[1]['stage'] == stage]
                  for stage in reversed(news_events.STAGES)]
        ordered = [row for round_items in zip_longest(*groups)
                   for row in round_items if row is not None]
    remaining_bytes = max(0, MAX_ARCHIVE_BYTES - len(dumps(result).encode()))
    included = []
    included_by_stage = Counter()
    for item, stage in ordered:
        if len(included) == MAX_DAY_ITEMS:
            break
        evidence = _evidence(item, 'archived_news', {
            'observation_date': day, 'published_at': item['published_at'],
            'first_seen_at': item['first_seen_at'], 'archive_captured_at': stage['captured_at'],
        })
        size = len(dumps(evidence).encode())
        if size > remaining_bytes:
            continue
        remaining_bytes -= size
        included.append(evidence)
        included_by_stage[stage['stage']] += 1
    result['evidence'] = sorted(included, key=lambda row: row['dates']['published_at'], reverse=True)
    for stage in coverage:
        stage['included_count'] = included_by_stage[stage['stage']]
    if len(ordered) > len(result['evidence']):
        _gap(result, 'archive_projection', '只返回容量内各档按发布时间选取的样本，未返回的条目不能用于结论', 'partial')


def fill_news_context(result, anchor):
    day = anchor.get('as_of')
    selection = anchor.get('news_context')
    validate_selection(day, selection)
    result['date'] = day
    result['summary'].update(selection=selection['kind'],
                             source_notice='标题与已留存摘要供解读。原文链接仅供溯源，来源标签不代表已验证行情影响。')
    if selection['kind'] == 'event':
        _calendar(result, selection)
    else:
        _messages(result, selection)
    result['summary']['included_count'] = len(result['evidence'])
    result['source'] = sorted({row['source'] for row in result['evidence']})
