"""Natural answers remain bounded and bind every displayed metric to evidence."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.services.ai_validation import validate_answer, validate_response, output_schema

CONTEXT={'allowed_ids':{'evidence_ids':['e1','e2'],'metric_ids':['m:close','m:missing','m:other']},
         'metrics':[{'metric_id':'m:close','value':12.3,'unit':'点','evidence_ids':['e1']},
                    {'metric_id':'m:missing','value':None,'unit':'%','evidence_ids':['e1']},
                    {'metric_id':'m:other','value':4,'evidence_ids':['e2']}]}
ANSWER={'answer':[{'heading':None,'text':'市场表现偏弱，指数收于{{metric:m:close}}，后续应观察量能能否配合改善。',
                   'evidence_ids':['e1'],'metric_refs':['m:close']}],'follow_up_questions':['你更关注指数走势还是板块分化？']}


def test_valid_metric_tokens_preserve_exact_ids_and_null_without_filling_zero():
    result=validate_answer(ANSWER,CONTEXT)
    assert result==ANSWER
    unknown=deepcopy(ANSWER);unknown['answer'][0]['text']='该指标目前为{{metric:m:missing}}。'
    unknown['answer'][0]['metric_refs']=['m:missing']
    assert validate_answer(unknown,CONTEXT)==unknown


@pytest.mark.parametrize('field,value,error',[
    ('text','指数上涨3%。','ungrounded_number'),
    ('text','观察日为2026-10-30。','ungrounded_number'),
    ('text','指数收于{{metric:foreign}}。','invalid_reference'),
    ('text','指数收于{{metric:m:other}}。','invalid_reference'),
    ('text','指数收于{{metric: m:close}}。','invalid_output'),
    ('text','指数收于{{metric:m:close}。','invalid_output'),
    ('text','{{javascript:alert()}}','invalid_output'),
    ('heading','涨幅3%','ungrounded_number'),
    ('heading','{{metric:m:close}}','invalid_output'),
    ('evidence_ids',['foreign'],'invalid_reference'),
    ('evidence_ids',['e1','e1'],'invalid_reference'),
    ('metric_refs',['m:close','m:close'],'invalid_reference'),
])
def test_invalid_numbers_ids_or_placeholder_injection_are_rejected(field,value,error):
    candidate=deepcopy(ANSWER);candidate['answer'][0][field]=value
    with pytest.raises(ValueError,match=error):validate_answer(candidate,CONTEXT)


def test_metric_reference_must_be_related_to_the_same_block_evidence():
    candidate=deepcopy(ANSWER);candidate['answer'][0].update(text='观察该指标{{metric:m:other}}。',metric_refs=['m:other'])
    with pytest.raises(ValueError,match='invalid_reference'):validate_answer(candidate,CONTEXT)


@pytest.mark.parametrize('mutate',[
    lambda x:x.update(answer=[]),
    lambda x:x.update(answer=x['answer']*6),
    lambda x:x['answer'][0].update(text='文'*901),
    lambda x:x['answer'][0].update(heading='题'*41),
    lambda x:x['answer'][0].update(evidence_ids=[]),
    lambda x:x.update(follow_up_questions=['问']*4),
    lambda x:x.update(follow_up_questions=['问'*151]),
    lambda x:x.update(extra='不能添加字段'),
])
def test_v2_schema_hard_limits_remain_local_even_if_provider_schema_omits_them(mutate):
    candidate=deepcopy(ANSWER);mutate(candidate)
    with pytest.raises((ValidationError,ValueError)):validate_answer(candidate,CONTEXT)


def test_total_text_bound_and_question_number_bound():
    candidate=deepcopy(ANSWER)
    candidate['answer']=[{**candidate['answer'][0],'text':'文'*900} for _ in range(3)]
    with pytest.raises(ValueError,match='invalid_output'):validate_answer(candidate,CONTEXT)
    candidate=deepcopy(ANSWER);candidate['follow_up_questions']=['明天涨10%吗？']
    with pytest.raises(ValueError,match='ungrounded_number'):validate_answer(candidate,CONTEXT)


def test_schema_version_selects_validator_without_converting_history():
    assert validate_response(ANSWER,CONTEXT,{'output_schema_version':'ai-evidence-answer-v2'})==ANSWER
    with pytest.raises(ValidationError):validate_response(ANSWER,CONTEXT,{'output_schema_version':'ai-research-interpret-v1'})
    schema=output_schema('ai-evidence-answer-v2')
    assert set(schema['properties'])=={'answer','follow_up_questions'} and schema['additionalProperties'] is False
