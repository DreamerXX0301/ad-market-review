"""Fresh topic override batches equal the former per-topic SQL projections."""
from contextlib import contextmanager
from copy import deepcopy
import math
import sqlite3

import pytest

from app.services import topics
from app.services.workspace_common import code_info, current, js, obj, rows

DAY = '2026-01-08'
OLD = '2026-01-07'
FUTURE = '2026-01-09'


@pytest.fixture
def local(monkeypatch):
    connection = sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    for pragma in ('foreign_keys=ON','recursive_triggers=ON','busy_timeout=10000'):
        connection.execute('PRAGMA '+pragma)
    connection.executescript('''
      CREATE TABLE business_clock(singleton INTEGER PRIMARY KEY,business_date TEXT);
      CREATE TABLE topic(topic_id TEXT PRIMARY KEY,origin TEXT,provider_topic_key TEXT,created_at_ms INTEGER);
      CREATE TABLE topic_revision(topic_id TEXT,revision INTEGER,name TEXT,description TEXT,aliases_json TEXT,
        archived INTEGER,effective_business_date TEXT,recorded_at_ms INTEGER,PRIMARY KEY(topic_id,revision));
      CREATE TABLE topic_override(override_id TEXT PRIMARY KEY,topic_id TEXT,instrument_id TEXT,action TEXT,
        effective_business_date TEXT,recorded_at_ms INTEGER,supersedes_override_id TEXT,reason TEXT);
    ''')
    connection.execute('INSERT INTO business_clock VALUES(1,?)',(DAY,))
    catalog={}
    @contextmanager
    def same_connection():yield connection
    monkeypatch.setattr(topics,'connect',same_connection)
    monkeypatch.setattr(topics,'_catalog_map',lambda market=None:deepcopy(catalog))
    yield connection,catalog
    connection.close()


def add_topic(connection,catalog,identifier,*,origin='provider',archived=False,kind='concept',complete=False):
    connection.execute('INSERT INTO topic VALUES(?,?,?,?)',(identifier,origin,identifier,1))
    connection.execute('INSERT INTO topic_revision VALUES(?,?,?,?,?,?,?,?)',
        (identifier,1,'name-'+identifier,'description','["alias"]',int(archived),OLD,1))
    catalog[identifier]={'topic_id':identifier,'name':'provider-name','kind':kind,
        'members_complete':complete,'observed_date':OLD,'instrument_ids':['600001.SH','600002.SH']}


def override(connection,topic,symbol,action,day=DAY,stamp=50):
    number=connection.execute('SELECT count(*) FROM topic_override').fetchone()[0]
    connection.execute('INSERT INTO topic_override VALUES(?,?,?,?,?,?,?,?)',
        (f'override-{number}',topic,symbol,action,day,stamp,None,'synthetic'))


def legacy_map(connection,day,catalog):
    result={}
    selected=rows(connection,'''SELECT t.*,r.name,r.archived FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id)''')
    for topic in selected:
        if topic['archived']:continue
        members,_=topics._members(connection,topic,day,catalog=catalog)
        for symbol in members:result.setdefault(symbol,[]).append({'topic_id':topic['topic_id'],'name':topic['name']})
    return result


def legacy_view(connection,topic,catalog,with_members=False,master=None):
    meta=catalog.get(topic.get('provider_topic_key'),{})
    value={key:item for key,item in topic.items() if key!='aliases_json'}
    value['aliases']=obj(topic.get('aliases_json'),[])
    value.update(kind='manual' if topic['origin']=='manual' else meta.get('kind','concept'),
        source_observed_date=meta.get('observed_date'),historical_membership=False,
        members_complete=True if topic['origin']=='manual' else meta.get('members_complete',False))
    members,origin=topics._members(connection,topic,catalog=catalog)
    value['observed_member_count']=len(members)
    value['member_count']=len(members) if value['members_complete'] else None
    if with_members:
        day=current(connection);provider=topics._provider_members(topic,catalog)
        latest={item['instrument_id']:item for item in rows(connection,'SELECT * FROM topic_override WHERE topic_id=? AND effective_business_date<=? ORDER BY effective_business_date,recorded_at_ms,rowid',(topic['topic_id'],day))}
        def member(symbol,action):
            selected=latest.get(symbol,{}).get('action')
            return {**code_info(symbol,master or {}),'origin':origin.get(symbol,'manual'),'action':action,
                'provider_member':symbol in provider,'manual_override':selected if selected in ('include','exclude') else None}
        value['members']=[member(symbol,'include') for symbol in sorted(members)]
        value['excluded_members']=[member(symbol,'exclude') for symbol,item in latest.items() if item['action']=='exclude']
    return value


def legacy_list(connection,catalog,q='',include_archived=False,kind=None):
    selected=rows(connection,'''SELECT t.*,r.name,r.description,r.aliases_json,r.archived,r.revision,r.effective_business_date FROM topic t JOIN topic_revision r ON r.topic_id=t.topic_id AND r.revision=(SELECT max(revision) FROM topic_revision WHERE topic_id=t.topic_id) ORDER BY r.name''')
    selected=[legacy_view(connection,row,catalog) for row in selected if (include_archived or not row['archived'])
        and (not q or q.lower() in (row['name']+row['description']+' '.join(obj(row['aliases_json'],[]))).lower())]
    if kind and kind!='all':selected=[item for item in selected if item['kind']==kind]
    return {'items':selected,'total':len(selected)}


def seed(local):
    connection,catalog=local
    for number in range(9):
        identifier=f'topic-{number}'
        add_topic(connection,catalog,identifier,origin='manual' if number%3==0 else 'provider',
                  archived=number==8,kind='industry' if number%2 else 'concept',complete=number%2==0)
        override(connection,identifier,'600004.SH','exclude',OLD,1)
        override(connection,identifier,'600001.SH','exclude',OLD,2)
        override(connection,identifier,'600002.SH','exclude',DAY,50)
        override(connection,identifier,'600003.SH','include',DAY,50)
        override(connection,identifier,'600003.SH','exclude',DAY,50)
        override(connection,identifier,'600001.SH','include',DAY,50)
        override(connection,identifier,'600001.SH','reset_to_provider',DAY,50)
        override(connection,identifier,'600004.SH','exclude',DAY,999)
        override(connection,identifier,'600002.SH','reset_to_provider',FUTURE,1000)


@pytest.mark.parametrize('day',[OLD,DAY,FUTURE,None])
def test_complete_mapping_preserves_all_actions_effective_dates_and_rowids(local,day):
    seed(local);connection,catalog=local
    assert topics.topic_map(connection,day)==legacy_map(connection,day,catalog)


@pytest.mark.parametrize('archived',[False,True])
@pytest.mark.parametrize('kind',[None,'all','manual','concept','industry'])
@pytest.mark.parametrize('query',['','name-topic-1','alias','missing'])
def test_list_all_fields_match_legacy_filters_unknowns_and_catalog(local,archived,kind,query):
    seed(local);connection,catalog=local
    assert topics.list_topics(query,archived,kind)==legacy_list(connection,catalog,query,archived,kind)


def test_detail_keeps_original_excluded_order_and_override_provenance(local):
    seed(local);connection,catalog=local;topic=topics._topic(connection,'topic-1')
    master={'600001.SH':{'name':'one'},'600002.SH':{'name':'two'}}
    expected=legacy_view(connection,topic,catalog,True,master)
    actual=topics.topic_view(connection,topic,True,catalog=catalog,known_master=master)
    assert actual==expected
    assert [item['instrument_id'] for item in actual['excluded_members']]==['600004.SH','600002.SH','600003.SH']
    assert actual['member_count'] is None and actual['observed_member_count']==1
    assert actual['members'][0]['origin']=='provider' and actual['members'][0]['manual_override'] is None


def test_each_request_immediately_sees_add_reset_revision_archive_and_source_changes(local):
    seed(local);connection,catalog=local
    before=topics.topic_map(connection,DAY)
    override(connection,'topic-1','600005.SH','include',DAY,2000)
    assert '600005.SH' in topics.topic_map(connection,DAY) and '600005.SH' not in before
    override(connection,'topic-1','600005.SH','reset_to_provider',DAY,2000)
    assert '600005.SH' not in topics.topic_map(connection,DAY)
    catalog['topic-1']['instrument_ids'].append('600006.SH')
    assert '600006.SH' in topics.topic_map(connection,DAY)
    connection.execute('INSERT INTO topic_revision VALUES(?,?,?,?,?,?,?,?)',
        ('topic-1',2,'renamed','new description','[]',0,DAY,3000))
    assert any(item['name']=='renamed' for item in topics.topic_map(connection,DAY)['600006.SH'])
    connection.execute('INSERT INTO topic_revision VALUES(?,?,?,?,?,?,?,?)',
        ('topic-1',3,'archived','archive','[]',1,FUTURE,4000))
    # This helper remains current catalog classification, never PIT revisions.
    assert '600006.SH' not in topics.topic_map(connection,DAY)
    assert topics.topic_map(connection,DAY)==legacy_map(connection,DAY,catalog)


def test_710_topics_use_bounded_fresh_override_queries_and_no_writes(local):
    connection,catalog=local
    for number in range(710):
        identifier=f'topic-{number:04d}'
        add_topic(connection,catalog,identifier)
        override(connection,identifier,'600003.SH','include')
    statements=[];connection.set_trace_callback(statements.append)
    actual=topics.topic_map(connection,DAY)
    connection.set_trace_callback(None)
    assert len(statements)==1+math.ceil(710/128)
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert actual==legacy_map(connection,DAY,catalog)
    statements=[];connection.set_trace_callback(statements.append)
    result=topics.list_topics()
    connection.set_trace_callback(None)
    assert len(statements)==2+math.ceil(710/128)
    assert result==legacy_list(connection,catalog)


def test_empty_or_fully_filtered_directory_does_not_require_business_clock(local):
    connection,catalog=local
    connection.execute('DELETE FROM business_clock')
    assert topics.list_topics()=={'items':[],'total':0}
    assert topics.topic_map(connection,None)=={}
    add_topic(connection,catalog,'archived',archived=True)
    assert topics.list_topics()=={'items':[],'total':0}
    assert topics.list_topics('not-found',True)=={'items':[],'total':0}
    assert topics.topic_map(connection,None)=={}
