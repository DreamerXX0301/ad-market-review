"""New manual tasks retire unreliable fund collection; old frozen tasks are unchanged."""
FUND_FLOW_RETIRED = 'fund-flow-retired-v1'


def validate_fund_flow_policy(value):
    if value not in (None, FUND_FLOW_RETIRED):
        raise ValueError('行业资金采集策略版本不受支持')
    return value
