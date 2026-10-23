"""Local daily/weekly/monthly descriptive trends, with explicit warm-up gaps."""
from app.analytics.indicators import enrich_bars,aggregate_weekly
from app.analytics.monthly import aggregate_monthly,enrich_monthly_bars

FIELDS=['close','ma5','ma10','ma20','ma30','ma60','ma250','volume','amount','volume_ratio','macd_dif','macd_dea','macd_hist','kdj_k','kdj_d','kdj_j']


def build_trends(market,symbol,as_of):
    # v1 reports keep their shape while using the same strict periods as v2.
    from app.services.research_technical import build_legacy_trends
    return build_legacy_trends(market,symbol,as_of)
