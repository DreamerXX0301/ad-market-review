"""Frozen policy marker for newly authorized manual data chains."""

VERSION = 'supplement-retention-250-v1'


def validate(value):
    if value not in (None, VERSION):
        raise ValueError('补充数据滚动保留策略版本不受支持')
    return value
