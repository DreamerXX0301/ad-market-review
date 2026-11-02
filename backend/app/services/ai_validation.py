"""Strict local output and reference validation, independent of provider claims."""
import re
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Identifier = Annotated[str, Field(min_length=1, max_length=100)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Hypothesis(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=300)]
    supporting_evidence_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]
    counter_evidence_ids: Annotated[list[Identifier], Field(max_length=8)]
    metric_refs: Annotated[list[Identifier], Field(max_length=8)]
    open_question: Annotated[str, Field(min_length=1, max_length=200)]

class Question(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)]
    evidence_ids: Annotated[list[Identifier], Field(max_length=8)]

class Interpretation(StrictModel):
    summary: Annotated[str, Field(min_length=1, max_length=240)]
    selected_claim_ids: Annotated[list[Identifier], Field(max_length=6)]
    hypotheses: Annotated[list[Hypothesis], Field(max_length=4)]
    questions: Annotated[list[Question], Field(max_length=5)]

PROBE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['probe', 'missing'],
                'properties': {'probe': {'type': 'string', 'enum': ['ok']}, 'missing': {'type': 'null'}}}


def output_schema(version='ai-research-interpret-v1'):
    # Compile the reviewed contract, removing unsupported annotation/uniqueItems.
    import json
    from pathlib import Path
    files = {'ai-research-interpret-v1': 'ai-research-interpretation-v1.schema.json',
             'ai-evidence-answer-v2': 'ai-evidence-answer-v2.schema.json'}
    if version not in files: raise ValueError('invalid_output')
    raw = json.loads((Path(__file__).resolve().parents[3] / 'contracts' / files[version]).read_text())
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ('$schema', 'title', 'description', 'uniqueItems', 'minLength', 'maxLength', 'minItems', 'maxItems')}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean(raw)


def validate_interpretation(value, context):
    parsed = Interpretation.model_validate(value).model_dump()
    allowed = context.get('allowed_ids', {})
    sets = {key: set(allowed.get(key, [])) for key in ('claim_ids', 'evidence_ids', 'metric_ids')}
    def check(values, kind):
        if len(set(values)) != len(values) or not set(values) <= sets[kind]:
            raise ValueError('invalid_reference')
    check(parsed['selected_claim_ids'], 'claim_ids')
    texts = [parsed['summary']]
    for item in parsed['hypotheses']:
        check(item['supporting_evidence_ids'], 'evidence_ids')
        check(item['counter_evidence_ids'], 'evidence_ids')
        check(item['metric_refs'], 'metric_ids')
        texts.extend([item['text'], item['open_question']])
    for item in parsed['questions']:
        check(item['evidence_ids'], 'evidence_ids')
        texts.append(item['text'])
    # Numerical/date assertions must be rendered by source claim/metric references.
    # Reject all new numeric prose, including Chinese numeric quantity forms.
    if any(re.search(r'[0-9０-９]|[一二三四五六七八九十百千万亿两]+(?:年|月|日|元|倍|成|点|%|％)', text) for text in texts):
        raise ValueError('ungrounded_number')
    return parsed


class AnswerBlock(StrictModel):
    heading: Annotated[str | None, Field(max_length=40)]
    text: Annotated[str, Field(min_length=1, max_length=900)]
    evidence_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]
    metric_refs: Annotated[list[Identifier], Field(max_length=8)]


class EvidenceAnswer(StrictModel):
    answer: Annotated[list[AnswerBlock], Field(min_length=1, max_length=5)]
    follow_up_questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=150)]], Field(max_length=3)]


METRIC_TOKEN = re.compile(r'\{\{metric:([^{}\s]+)\}\}')
NUMBER = re.compile(r'[0-9０-９]|[一二三四五六七八九十百千万亿两]+(?:年|月|日|元|倍|成|点|%|％)')


def validate_answer(value, context):
    parsed = EvidenceAnswer.model_validate(value).model_dump()
    if sum(len(block['text']) for block in parsed['answer']) > 2600:
        raise ValueError('invalid_output')
    allowed = context.get('allowed_ids', {})
    evidence = set(allowed.get('evidence_ids', []))
    metrics = {row['metric_id']: row for row in context.get('metrics', [])}
    metric_ids = set(allowed.get('metric_ids', []))
    prose = list(parsed['follow_up_questions'])
    for block in parsed['answer']:
        for key, permitted in [('evidence_ids', evidence), ('metric_refs', metric_ids)]:
            refs = block[key]
            if len(set(refs)) != len(refs) or not set(refs) <= permitted:
                raise ValueError('invalid_reference')
        for identifier in block['metric_refs']:
            metric = metrics.get(identifier)
            if metric is None or not set(metric.get('evidence_ids', [])) & set(block['evidence_ids']):
                raise ValueError('invalid_reference')
        token_ids = METRIC_TOKEN.findall(block['text'])
        if not set(token_ids) <= set(block['metric_refs']):
            raise ValueError('invalid_reference')
        clean = METRIC_TOKEN.sub('', block['text'])
        if '{{' in clean or '}}' in clean or '{metric:' in clean:
            raise ValueError('invalid_output')
        prose.append(clean)
        if block['heading'] is not None:
            prose.append(block['heading'])
    if any('{{' in text or '}}' in text for text in prose):
        raise ValueError('invalid_output')
    if any(NUMBER.search(text) for text in prose):
        raise ValueError('ungrounded_number')
    return parsed


def validate_response(value, context, protocol):
    version = protocol.get('output_schema_version', 'ai-research-interpret-v1')
    if version == 'ai-research-interpret-v1':
        return validate_interpretation(value, context)
    if version == 'ai-evidence-answer-v2':
        return validate_answer(value, context)
    raise ValueError('invalid_output')
