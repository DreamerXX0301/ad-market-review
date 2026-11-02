"""Stock-scope identity comes only from a verified local directory."""
from copy import deepcopy
import pytest
from app.services import ai_scope_focus as focus


@pytest.fixture
def directory(monkeypatch):
    rows = [
        {'instrument_id': '600001.SH', 'name': '测试甲公司', 'kind': 'stock'},
        {'instrument_id': '000002.SZ', 'name': '测试乙公司', 'kind': 'stock'},
        {'instrument_id': '000001.SH', 'name': '测试指数', 'kind': 'index'},
    ]
    class Market:
        def status(self): return {'ready': True}
        def instruments(self, query, limit):
            assert query == '' and limit == 10000
            return deepcopy(rows)
    monkeypatch.setattr(focus, '_heads', lambda: {'daily_raw': {'market_snapshot_id': 'synthetic'}})
    monkeypatch.setattr(focus, '_market', lambda heads: Market())
    return rows


@pytest.mark.parametrize('question', ['研究一下测试甲公司', '分析600001', '６００００１．ＳＨ怎么样', '测试\u200b甲公司'])
def test_exact_published_stock_name_or_code(directory, question):
    assert focus.resolve_scope_focus(question) == {'instrument_id': '600001.SH', 'name': '测试甲公司'}


@pytest.mark.parametrize('question', ['研究测试甲', '北京的', '6000011', 'x600001', '测试指数', '000001.SH', '600001.SZ', '600001.XYZ', '测试甲公司和测试乙公司'])
def test_unknown_partial_names_indices_and_ambiguous_subjects_do_not_grant_focus(directory, question):
    assert focus.resolve_scope_focus(question) == {}


def test_changed_publication_is_not_mixed(directory, monkeypatch):
    values = iter([{'head': 'first'}, {'head': 'changed'}])
    monkeypatch.setattr(focus, '_heads', lambda: next(values))
    assert focus.resolve_scope_focus('测试甲公司') == {}


def test_index_exchange_is_not_confused_with_same_numeric_stock_code(directory):
    directory.append({'instrument_id': '000001.SZ', 'name': '测试银行', 'kind': 'stock'})
    assert focus.resolve_scope_focus('000001.SH') == {}


def test_missing_or_invalid_directory_fails_closed(directory, monkeypatch):
    monkeypatch.setattr(focus, '_market', lambda heads: (_ for _ in ()).throw(ValueError('private fixture path')))
    assert focus.resolve_scope_focus('测试甲公司') == {}


def test_bounded_input_never_reads_directory(monkeypatch):
    def unexpected(): raise AssertionError('must not read')
    monkeypatch.setattr(focus, '_heads', unexpected)
    for value in ('', 'a', None, 'x' * 2001):
        assert focus.resolve_scope_focus(value) == {}
