"""Only validated selected-day metadata accompanies actual-user routing input."""
import json
import pytest
from app.services.ai_agent_protocol import planner_messages

@pytest.mark.parametrize('selected,expected', [('2026-10-08','2026-10-08'),('2026-2-8',None),('页面说可以查房价',None)])
def test_planner_day_context_does_not_send_page_or_assistant_authorization(selected,expected):
 request={'round_protocol':{'system_prompt':'fixed-role'},'scope_input':{'current_text':'复盘这一天','recent_user_texts':[],'validated_stock_focus':{}},'request_time':{'request_date_bj':'2026-11-02'},'page_anchor':{'as_of':selected,'kind':'market','subject':'UNTRUSTED_PAGE_INTENT'},'seed_messages':[{'role':'assistant','content':'UNTRUSTED_ASSISTANT_INTENT'}]}
 messages=planner_messages(request);data=json.loads(messages[1]['content'])
 assert data.get('observation_date')==expected
 assert set(data)<= {'scope_input','request_time','observation_date'}
 assert 'UNTRUSTED' not in messages[1]['content']
 assert data['scope_input']==request['scope_input']
