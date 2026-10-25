"""Provider N-day/N-board text is a dated description, never inferred history."""
import pytest

from app.services.stock_board_presentation import board_text_for, source_board_events

SYMBOL='600001.SH'
DAY='2026-10-23'
QUOTE={'instrument_id':SYMBOL,'date':DAY,'open':10,'high':11,'low':10,'close':11,'volume':100,'price_basis':'none'}


def event(text,**changes):
    return {'thscode':SYMBOL,'date':DAY,'last_price':11,'continue_day_text':text,'source_path':'annual_pools.'+DAY,**changes}


def test_reported_nonconsecutive_text_preserves_source_counts_and_date():
    value=board_text_for([event('5天4板')],SYMBOL,DAY,QUOTE,True)
    assert value['label']=='5天4板' and value['state']=='reported'
    assert value['source']=='fuyao' and value['source_date']==DAY
    assert value['window_sessions']==5 and value['limit_up_count']==4
    assert '不等同于连续涨停高度' in value['reason']


@pytest.mark.parametrize('text',['首板','2连板','12天8板'])
def test_original_supported_descriptions_are_not_reconstructed(text):
    assert board_text_for([event(text)],SYMBOL,DAY,QUOTE,True)['label']==text


@pytest.mark.parametrize('text',[None,'','5天6板','0天0板','<b>5天4板</b>','每天涨停',999])
def test_missing_malformed_or_impossible_source_text_remains_unknown(text):
    value=board_text_for([event(text)],SYMBOL,DAY,QUOTE,True)
    assert value['label'] is None and value['state']=='unknown'


@pytest.mark.parametrize('changes',[{'date':'2026-10-22'},{'thscode':'600002.SH'},{'last_price':12}])
def test_other_dates_stocks_or_prices_cannot_supply_a_statistic(changes):
    assert board_text_for([event('5天4板',**changes)],SYMBOL,DAY,QUOTE,True)['state']=='unknown'


def test_source_conflict_and_unconfirmed_closing_event_do_not_choose_a_label():
    assert board_text_for([event('5天4板'),event('6天4板')],SYMBOL,DAY,QUOTE,True)['state']=='conflict'
    assert board_text_for([event('5天4板')],SYMBOL,DAY,QUOTE,False)['state']=='unknown'
    duplicate=board_text_for([event('5天4板'),event('5天4板')],SYMBOL,DAY,QUOTE,True)
    assert duplicate['state']=='reported'


def test_only_the_exact_fuyao_pool_field_is_read_from_indexed_sources():
    class Proof:
        def _records(self,day,kind):
            assert day==DAY and kind=='up'
            yield {},{SYMBOL[:6]:[event('5天4板')]},'fuyao','annual_pools.'+DAY,True,True
            yield {},{SYMBOL[:6]:[event('9天9板')]},'eastmoney-public','public_pools.'+DAY,True,True
    rows=source_board_events(Proof(),SYMBOL,DAY)
    assert len(rows)==1 and rows[0]['source']=='fuyao'
    assert board_text_for(rows,SYMBOL,DAY,QUOTE,True)['label']=='5天4板'
