"""Source identity is fixed; user labels never grant account permissions."""
import json

import pytest

from app.adapters.providers import ProviderError


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(tmp_path/'master'))
    (tmp_path/'master').write_bytes(Fernet.generate_key())
    from app.core import ensure_ready, clear_market_cache
    clear_market_cache();ensure_ready()
    yield
    clear_market_cache()


def by_id(result, identifier):
    return next(row for row in result['items'] if row['source_id']==identifier)


def test_fuyao_free_identity_ignores_legacy_paid_label_without_deleting_settings(isolated):
    from app.core import set_setting, setting
    from app.services import source_service as service
    set_setting('source-tier:fuyao','paid')
    before=by_id(service.list_sources(),'fuyao')
    assert before['tier']=='注册免费' and before['billing_mode']=='registered_free'
    assert before['requires_key'] is True and before['configured'] is False
    configured=service.configure('fuyao',{'api_key':'fixture-private-key','tier':'paid'})
    assert configured['tier']=='注册免费' and configured['sync_supported'] is True
    assert configured['configured'] and all(row['status']!='available' for row in configured['capabilities'])
    assert setting('source-tier:fuyao')=='paid'
    assert 'fixture-private-key' not in json.dumps(service.list_sources())


def test_public_source_remains_keyless_and_tickflow_has_no_self_declared_plan(isolated):
    from app.services import source_service as service
    public=service.configure('tickflow-public',{'tier':'paid','active':False})
    assert public['billing_mode']=='public_free' and public['requires_key'] is False
    assert public['configured'] and public['sync_supported'] is False
    account=service.configure('tickflow',{'tier':'paid','active':False})
    assert account['billing_mode']=='account' and account['tier']=='按账号实际权限'
    assert account['requires_key'] and not account['configured']
    assert all(row['status']!='available' for row in account['capabilities'])


def test_permissions_come_from_individual_probes_and_new_key_resets_evidence(isolated,monkeypatch):
    from app.services import source_service as service
    def request(_self,path,params):
        if path.endswith('/ths-stock-list'):raise ProviderError('来源返回HTTP 403',403)
        if path.endswith('/snapshot'):raise ProviderError('来源暂时超时')
        if path.endswith('/income-statements'):return {'data':{'item':[]}}
        return {'data':{'item':[{'fixture':True}]}}
    monkeypatch.setattr(service.ProviderClient,'request',request)
    service.configure('fuyao',{'api_key':'fixture-first','tier':'paid'})
    result=service.verify('fuyao');caps={row['key']:row for row in result['capabilities']}
    assert result['tier']=='注册免费'
    assert caps['daily']['status']=='available' and caps['daily']['checked_at'] is not None
    assert caps['topics']['status']=='locked' and caps['topics']['account_access']=='denied'
    assert caps['realtime']['status']=='unverified' and caps['realtime']['account_access']=='temporary_error'
    assert caps['financial']['status']=='unverified' and '样本为空' in caps['financial']['reason']
    assert caps['minutes']['status']=='unsupported' and caps['minutes']['checked_at'] is None
    assert '全市场完整' in caps['daily']['sample']
    changed=service.configure('fuyao',{'api_key':'fixture-second'})
    assert all(row['status']!='available' and row['checked_at'] is None for row in changed['capabilities'])


def test_data_center_sync_readiness_never_starts_work_or_infers_paid_permissions(isolated):
    from app.core import connect
    from app.services import source_service, platform_service
    initial=platform_service.data_status()
    assert initial['sync_configuration']['ready'] is False
    source_service.configure('fuyao',{'api_key':'fixture-free','tier':'paid'})
    ready=platform_service.data_status()
    assert ready['sync_configuration']['ready'] is True
    assert ready['sync_configuration']['tier']=='注册免费'
    assert ready['automatic_schedule'] is False and ready['latest_task'] is None
    source_service.configure('tickflow',{'api_key':'fixture-tickflow','tier':'paid'})
    unsupported=platform_service.data_status()
    assert unsupported['sync_configuration']['source_id']=='tickflow'
    assert unsupported['sync_configuration']['ready'] is False
    assert '扶摇免费' in unsupported['sync_configuration']['reason']
    with connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM capability_probe').fetchone()[0]==0
    assert 'fixture-free' not in json.dumps(ready)


def test_missing_key_is_configuration_error_without_false_permission_probes(isolated):
    from fastapi import HTTPException
    from app.core import connect
    from app.services import source_service
    with pytest.raises(HTTPException) as error:source_service.verify('tickflow')
    assert error.value.status_code==422
    with connect() as connection:
        assert connection.execute('SELECT count(*) FROM capability_probe').fetchone()[0]==0
