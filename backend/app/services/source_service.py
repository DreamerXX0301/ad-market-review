"""Source configuration and evidence-backed account capabilities."""
from __future__ import annotations
from app.services.supplement_access import supplement_reader
import datetime as dt
import json
from fastapi import HTTPException
from app.core import connect, dumps, new_id, now_ms, setting, set_setting
from app.infrastructure.credentials import CredentialStore
from app.adapters.providers import ProviderClient, ProviderError, provider_connection_scope

CAPABILITIES = {"daily": "个股日K", "index": "指数日K", "adjustment": "复权与除权", "topics": "题材与行业", "realtime": "行情快照", "minutes": "分钟K", "level2": "五档盘口", "financial": "财务", "bulk": "全市场批量"}
CATALOG = {
    "fuyao": {"name": "扶摇", "tier": "注册免费", "billing_mode":"registered_free", "requires_key":True, "sync_supported":True, "configuration_note":"扶摇使用免费注册Key，没有付费套餐需要选择；每项能力仍以当前Key的真实探测结果为准。", "description": "免费注册Key；用于全市场盘后同步。日K、指数、题材等能力逐项验证。", "supported": {"daily", "index", "adjustment", "topics", "realtime", "financial", "bulk"}},
    "tickflow-public": {"name": "TickFlow 公共历史", "tier": "免Key免费", "billing_mode":"public_free", "requires_key":False, "sync_supported":False, "configuration_note":"公共免费服务无需Key，直接验证日K与指数查询；本项目未接入它的全市场批量同步。", "description": "无需Key的日周历史查询，适合指数、个股与交叉核验。", "supported": {"daily", "index"}},
    "tickflow": {"name": "TickFlow", "tier": "按账号实际权限", "billing_mode":"account", "requires_key":True, "sync_supported":False, "configuration_note":"免费与付费账号都只需填写自己的Key，不用在此选择套餐。界面逐项显示实际探测结果，不根据账号标签授予权限。", "description": "填写现有账号Key；免费或付费权限都以接口探测为准。", "supported": set(CAPABILITIES)},
}


def list_sources(*, include_supplement=True):
    vault = CredentialStore()
    with connect() as connection:
        profiles = {r["connection_id"]: dict(r) for r in connection.execute("SELECT * FROM provider_connection")}
        revisions={row['key'].removeprefix('source-credential-revision:'):json.loads(row['value_json']) for row in connection.execute("SELECT key,value_json FROM platform_setting WHERE key LIKE 'source-credential-revision:%'")}
        latest = {}
        for row in connection.execute("SELECT * FROM capability_probe ORDER BY probed_at_ms"):
            revision=revisions.get(row['connection_id'],'legacy')
            if json.loads(row['evidence_json']).get('credential_revision','legacy')!=revision:continue
            latest[(row["connection_id"], row["capability"])] = dict(row)
    result = []
    for identifier, meta in CATALOG.items():
        profile = profiles.get(identifier, {})
        verified = [r for (source, _), r in latest.items() if source == identifier]
        capabilities = []
        for key, name in CAPABILITIES.items():
            probe = latest.get((identifier, key))
            document = key in meta["supported"]
            state = "available" if probe and probe["account_access"] == "allowed" else "locked" if probe and probe["account_access"] == "denied" else "unsupported" if not document else "unverified"
            reason = json.loads(probe["evidence_json"]).get("reason") if probe else "适配器支持，等待此账号单项核验" if document else "本项目尚未适配此能力，不能由此判断供应商全部权限"
            evidence=json.loads(probe["evidence_json"]) if probe else {}
            capabilities.append({"key": key, "name": name, "status": state, "reason": reason, "checked_at":probe['probed_at_ms'] if probe else None, "account_access":probe['account_access'] if probe else None, "sample":evidence.get('sample')})
        configured = identifier == "tickflow-public" or vault.exists(identifier)
        # Pricing labels describe the source, never user-declared permissions.
        # Legacy source-tier settings are preserved but no longer presented as
        # account facts, including incorrectly saved paid labels for Fuyao.
        result.append({"source_id": identifier, "name": meta["name"], "tier": meta["tier"], "billing_mode":meta['billing_mode'], "requires_key":meta['requires_key'], "sync_supported":meta['sync_supported'], "configuration_note":meta['configuration_note'], "configured": configured, "enabled": bool(profile.get("enabled", True)), "status": "verified" if any(r["account_access"] == "allowed" for r in verified) else "unverified" if configured else "needs_key", "description": meta["description"], "capabilities": capabilities, "last_verified_at": max((r["probed_at_ms"] for r in verified), default=None), "active": setting("active_source", "fuyao") == identifier})
    response = {"items": result, "total": len(result)}
    if include_supplement:
        response['public_supplement'] = supplement_status()
    return response


@supplement_reader
def supplement_status():
    """Read accepted evidence only; visiting settings never starts remote requests."""
    from app.core import get_market
    from app.services.market_supplement import load_supplement
    from app.services.sector_history import sector_prices_for_date
    market=get_market();status=market.status();doc=load_supplement(status.get('snapshot_id'))
    day=doc.get('target_date');pool=doc.get('public_pools',{}).get(day,{})
    flow=doc.get('fund_flows',{}).get(day,{})
    coverage=doc.get('annual_coverage',{}); down=doc.get('annual_down_coverage',{})
    sectors=sector_prices_for_date(doc,day) if day else {}
    return {'source_id':'akshare-public','name':'AKShare同源公开补充','mode':'supplement','tier':'免Key免费','target_date':day,'quality':doc.get('quality','not_synced'),'seal_rows':len(pool.get('items',[])),'seal_complete':pool.get('complete',False),'fund_flow_rows':len(flow.get('items',[])),'fund_flow_complete':flow.get('complete',False),'sector_index_rows':len(sectors.get('items',[])),'sector_index_catalog_count':sectors.get('coverage',{}).get('catalog_count'),'sector_index_complete':sectors.get('complete') is True,'annual_covered_days':coverage.get('covered_days',0),'annual_expected_days':coverage.get('expected_days',0),'annual_complete':coverage.get('complete',False),'annual_down_observed_days':down.get('observed_days',0),'annual_down_covered_days':down.get('covered_days',0),'annual_down_expected_days':down.get('expected_days',0),'annual_down_complete':down.get('complete',False),'failures':doc.get('failures',[])[-5:],'description':'公开来源补充封板与行业资金；扶摇历史池补充年内涨跌停，按日期的指数历史补充行业与概念日行情。板块行情和历史成员分别核验，不以当前成员回填历史。失败保留缺口，完整批次可缓存复用。','seal_sequence_available':False}


def configure(identifier, payload):
    if identifier not in CATALOG:
        raise HTTPException(404, "数据源不存在")
    key = payload.get("api_key")
    try:
        credential_ref = CredentialStore().save(identifier, key) if key and identifier != "tickflow-public" else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if credential_ref:set_setting('source-credential-revision:'+identifier,new_id('credential'))
    with connect() as connection:
        if credential_ref:
            connection.execute("UPDATE provider_connection SET credential_ref=? WHERE connection_id=?", (credential_ref, identifier))
        if "enabled" in payload:
            connection.execute("UPDATE provider_connection SET enabled=? WHERE connection_id=?", (int(bool(payload["enabled"])), identifier))
    # Older clients may still submit tier. It cannot grant or label account
    # capabilities, so accept the rest of the configuration without storing it.
    if payload.get("active", True):
        set_setting("active_source", identifier)
    return next(r for r in list_sources()["items"] if r["source_id"] == identifier)


def client(identifier=None):
    identifier = identifier or setting("active_source", "fuyao")
    if identifier not in CATALOG:
        raise ProviderError("没有可用数据源")
    with connect() as connection:
        profile=connection.execute('SELECT enabled FROM provider_connection WHERE connection_id=?',(identifier,)).fetchone()
    if profile and not profile[0]:raise ProviderError('此数据源已停用，请先在配置中启用')
    return ProviderClient(identifier, None if identifier == "tickflow-public" else CredentialStore().get(identifier))


@provider_connection_scope()
def verify(identifier):
    revision=setting('source-credential-revision:'+identifier,'legacy')
    adapter = client(identifier)
    if CATALOG[identifier]['requires_key'] and not adapter.key:
        raise HTTPException(422,'请先保存此数据源的Key，再验证实际权限')
    end = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    tests = [
        ("daily", "/api/a-share/prices/historical", {"thscode": "600519.SH", "interval": "1d", "start": end - 14 * 86400000, "end": end, "adjust": "none"}),
        ("index", "/api/a-share-index/prices/historical", {"thscode": "000001.SH", "interval": "1d", "start": end - 14 * 86400000, "end": end}),
        ('topics','/api/a-share-index/constituents/ths-stock-list',{'thscode':'881155.TI'}),
        ('realtime','/api/a-share/prices/snapshot',{'thscodes':'600519.SH'}),
        ('financial','/api/a-share/financials/income-statements',{'thscode':'600519.SH','period':'quarterly','limit':1}),
        ('adjustment','/api/dump/market-dumps/adjustment-factors/download-url',{}),
        ('bulk','/api/dump/market-dumps/daily-k-10d/download-url',{}),
    ] if identifier == "fuyao" else [("daily", "/v1/klines", {"symbol": "600519.SH", "period": "1d", "count": 2, "adjust": "none"}), ("index", "/v1/klines", {"symbol": "000001.SH", "period": "1d", "count": 2, "adjust": "none"})]
    for capability, path, params in tests:
        allowed, reason, state = "allowed", "本次小样请求成功；不代表全市场历史完整", "partial"
        try:
            payload = adapter.request(path, params)
            data = payload["data"]
            values = data.get("item", data.get("timestamp", data.get('presigned_url',[]))) if isinstance(data, dict) else data
            if not values:
                raise ProviderError("请求成功但样本为空，覆盖尚未确认")
        except ProviderError as error:
            allowed = "denied" if error.status in (401, 403) or error.code in (2001, 2003) else "temporary_error"
            reason, state = str(error), "unknown"
        with connect() as connection:
            sample='下载授权已验证，文件字段与完整性须经同步另验' if capability in ('adjustment','bulk') else '单一标的或成员样本，不代表全市场完整'
            connection.execute("INSERT INTO capability_probe(probe_id,connection_id,capability,document_support,account_access,coverage_state,probed_at_ms,schema_version,evidence_json) VALUES(?,?,?,?,?,?,?,?,?)", (new_id("probe"), identifier, capability, "supported", allowed, state, now_ms(), "provider-v1", dumps({"reason": reason, "endpoint": path, "sample": sample,'credential_revision':revision})))
    return next(r for r in list_sources()["items"] if r["source_id"] == identifier)
