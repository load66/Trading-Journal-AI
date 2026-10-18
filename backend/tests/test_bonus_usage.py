"""Synthetic-only verification of the owner-only hosting usage endpoint."""
import sys
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bonus_usage as u
import bonus_backups as b
from auth import AuthError

AT = datetime(2026,10,17,12,tzinfo=timezone.utc)


def _remote(token,query,variables):
    assert token == 'synthetic-do-not-log'
    if 'BonusHostingWorkspaces' in query:
        return {"externalWorkspaces":[],"me":{"workspaces":[{
            "id":"synthetic-workspace","projects":{"edges":[{"node":{"id":"synthetic-project"}}]}
        }]}}
    if 'BonusHostingMemory' in query:
        return {"metrics":[{"tags":{"serviceId":"svc-a"},"values":[{"ts":1,"value":0.27},{"ts":2,"value":0.33}]},
                           {"tags":{"serviceId":"svc-b"},"values":[{"ts":1,"value":0.6}]}]}
    return {"project":{"services":{"edges":[{"node":{"id":"svc-a","name":"api"}},
                                          {"node":{"id":"svc-b","name":"leaps-alpaca-live"}}]}},
            "estimatedUsage":[{"measurement":"CPU_USAGE","estimatedValue":30,"projectId":"synthetic-project"},
                              {"measurement":"MEMORY_USAGE_GB","estimatedValue":3500,"projectId":"synthetic-project"},
                              {"measurement":"NETWORK_TX_GB","estimatedValue":0.8,"projectId":"synthetic-project"}],
            "usage":[
                {"measurement":m,"value":v,"tags":{"projectId":"synthetic-project","serviceId":id}}
                for id,parts in {"svc-a":{"CPU_USAGE":3,"MEMORY_USAGE_GB":400,"NETWORK_TX_GB":0.1},
                                 "svc-b":{"CPU_USAGE":12,"MEMORY_USAGE_GB":900,"NETWORK_TX_GB":0.2}}.items()
                for m,v in parts.items()]}


def _configured(monkeypatch):
    monkeypatch.setenv('RAILWAY_API_TOKEN','synthetic-do-not-log')
    monkeypatch.setenv('RAILWAY_PROJECT_ID','synthetic-project')
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID','synthetic-env')
    monkeypatch.setattr(u,'_railway_query',_remote)


def test_normal_railway_with_real_units_and_average_memory(monkeypatch):
    _configured(monkeypatch)
    value=u._railway_usage(AT)
    assert value['status']=='ok'
    assert value['costSoFar']>0 and value['projectedMonthEnd']>value['costSoFar']
    assert {s['name'] for s in value['services']}=={'api','leaps-alpaca-live'}
    assert value['services'][0]['avgMemoryGb']==0.3
    assert value['projectedMonthEnd'] == round(u._metric_cost({'CPU_USAGE':30,'MEMORY_USAGE_GB':3500,'NETWORK_TX_GB':0.8}),4)
    assert value['projectedMonthEnd'] != round(value['costSoFar'] / u._month(AT)[2],4), 'Use Railway estimate, not local extrapolation'


def test_usage_near_and_over_editable_plan(monkeypatch):
    _configured(monkeypatch)
    costs=u._railway_usage(AT)
    projection=costs['projectedMonthEnd']
    def chip(limit):
        ratio=projection/limit
        return 'over' if ratio>1 else 'near' if ratio>=0.8 else 'within'
    assert chip(projection*1.4)=='within'
    assert chip(projection/0.9)=='near'
    assert chip(projection/1.1)=='over'


def test_schema_uses_project_scoped_railway_usage_and_billing_estimate():
    assert "estimatedUsage(workspaceId: $workspaceId" in u._QUERY
    assert "groupBy: [PROJECT_ID, SERVICE_ID]" in u._QUERY
    assert "endDate: $endDate" in u._QUERY
    assert "externalWorkspaces" in u._WORKSPACES and "me { workspaces" in u._WORKSPACES
    assert "metrics(environmentId: $environmentId, projectId: $projectId" in u._METRICS
    assert "groupBy: [SERVICE_ID]" in u._METRICS


def test_workspace_resolution_supports_member_and_external_shapes():
    member={"externalWorkspaces":[],"me":{"workspaces":[{"id":"w1","projects":{"edges":[{"node":{"id":"p1"}}]}}]}}
    external={"externalWorkspaces":[{"id":"w2","projects":[{"id":"p2"}]}],"me":{"workspaces":[]}}
    assert u._workspace_for_project(member,"p1")=="w1"
    assert u._workspace_for_project(external,"p2")=="w2"
    assert u._workspace_for_project(member,"missing") is None


def test_workspace_or_other_project_usage_is_never_attributed(monkeypatch):
    _configured(monkeypatch)
    def remote(token,query,variables):
        value=_remote(token,query,variables)
        if 'BonusHosting(' in query:
            value['estimatedUsage']=[{"measurement":"CPU_USAGE","estimatedValue":30,"projectId":"other-project"}]
        return value
    monkeypatch.setattr(u,'_railway_query',remote)
    value=u._railway_usage(AT)
    assert value['status']=='partial'
    assert value['costSoFar'] is not None
    assert value['projectedMonthEnd'] is None
    assert value['reasonCode']=='estimate_missing'


def test_no_token_is_not_zero(monkeypatch):
    monkeypatch.delenv('RAILWAY_API_TOKEN',raising=False)
    monkeypatch.setattr(u,'_railway_query',lambda *_: (_ for _ in ()).throw(AssertionError('should not query')))
    value=u._railway_usage(AT)
    assert value['status']=='unavailable'
    assert value['reasonCode']=='token_missing'
    assert value['costSoFar'] is None
    assert value['projectedMonthEnd'] is None


def test_invalid_remote_response_fails_closed(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(u,'_railway_query',lambda *_: {'usage':[]})
    value=u._railway_usage(AT)
    assert value['status']=='unavailable'
    assert value['costSoFar'] is None
    assert 'synthetic-do-not-log' not in str(value)


def test_r2_listing_and_failure(monkeypatch):
    class FakeStorage:
        def usage_summary(self):
            return {'stored_bytes':325000000,'object_count':23}
    monkeypatch.setattr(u,'store',lambda:FakeStorage())
    value=u._r2_usage()
    assert value['usedBytes']==325000000
    assert value['freeBytes']==10_000_000_000
    monkeypatch.setattr(u,'store',lambda: (_ for _ in ()).throw(RuntimeError('secret')))
    assert u._r2_usage()['usedBytes'] is None


def test_one_hour_cache(monkeypatch):
    u._cache.clear()
    calls=[]
    monkeypatch.setattr(u,'usage_snapshot',lambda now: calls.append(now) or {'railway':{'status':'ok'}})
    first=u.cached_usage('owner')
    assert u.cached_usage('owner')==first
    assert len(calls)==1
    cached=list(u._cache)[0]
    stamp,value=u._cache[cached]
    u._cache[cached]=(stamp-3601,value)
    u.cached_usage('owner')
    assert len(calls)==2
    u._cache.clear()


def test_owner_only_auth_checked_before_any_api_calls(monkeypatch):
    u._cache.clear()
    monkeypatch.setattr(b,'auth_required',lambda:True)
    monkeypatch.setattr(b,'authorize_header',lambda header: {'sub':'owner'} if header=='Bearer owner' else
                        (_ for _ in ()).throw(AuthError(401,'No session')))
    called=[]
    monkeypatch.setattr(u,'cached_usage',lambda user:called.append(user) or {'month':'2026-10'})
    app=FastAPI();app.include_router(u.router)
    client=TestClient(app)
    assert client.get('/api/bonus-tracker/usage').status_code==401
    assert not called
    assert client.get('/api/bonus-tracker/usage',headers={'Authorization':'Bearer owner'}).status_code==200
    assert len(called)==1
    monkeypatch.setattr(b,'auth_required',lambda:False)
    assert client.get('/api/bonus-tracker/usage',headers={'Authorization':'Bearer owner'}).status_code==503
    assert len(called)==1


def test_partial_usage_preserves_only_verified_service_costs(monkeypatch):
    _configured(monkeypatch)
    def missing_network(token, query, variables):
        payload = _remote(token, query, variables)
        if 'BonusHosting(' in query:
            payload['usage'] = [v for v in payload['usage'] if not (
                v['tags']['serviceId'] == 'svc-b' and v['measurement'] == 'NETWORK_TX_GB')]
        return payload
    monkeypatch.setattr(u, '_railway_query', missing_network)
    value = u._railway_usage(AT)
    assert value['status'] == 'partial'
    assert value['costSoFar'] is None, 'Do not describe a subtotal as the complete bill'
    assert value['knownCostSubtotal'] == value['services'][0]['costSoFar']
    assert value['services'][1]['costSoFar'] is None
    assert value['services'][1]['measurements']['NETWORK_TX_GB'] is None
    assert {'service': 'leaps-alpaca-live', 'metric': 'NETWORK_TX_GB', 'kind': 'usage'} in value['missing']
    assert value['projectedMonthEnd'] is not None
    assert value['basis'] and value['verifiedAt']


def test_true_zero_is_verified_not_missing(monkeypatch):
    _configured(monkeypatch)
    def zeros(token, query, variables):
        payload = _remote(token, query, variables)
        if 'BonusHosting(' in query:
            for row in payload['usage']:
                row['value'] = 0
            for row in payload['estimatedUsage']:
                row['estimatedValue'] = 0
        return payload
    monkeypatch.setattr(u, '_railway_query', zeros)
    result = u._railway_usage(AT)
    assert result['status'] == 'ok'
    assert result['costSoFar'] == 0
    assert result['projectedMonthEnd'] == 0
    assert all(x['costSoFar'] == 0 for x in result['services'])
    assert all(x['projectedMonthEnd'] == 0 for x in result['services'])
    assert result['verifiedAt'] and result['basis']


def test_missing_estimate_lists_metric_without_fake_zero(monkeypatch):
    _configured(monkeypatch)
    def omit(token, query, variables):
        payload = _remote(token, query, variables)
        if 'BonusHosting(' in query:
            payload['estimatedUsage'] = [x for x in payload['estimatedUsage'] if x['measurement'] != 'NETWORK_TX_GB']
        return payload
    monkeypatch.setattr(u, '_railway_query', omit)
    result = u._railway_usage(AT)
    assert result['status'] == 'partial'
    assert result['costSoFar'] is not None
    assert result['projectedMonthEnd'] is None
    assert result['reasonCode'] == 'estimate_missing'
    assert {'service': 'project', 'metric': 'NETWORK_TX_GB', 'kind': 'estimate'} in result['missing']


def test_safe_reason_codes_never_echo_remote_text_or_token(monkeypatch, capsys):
    _configured(monkeypatch)
    marker = 'TOP-SECRET-REMOTE-RESPONSE'
    assert set(u.SAFE_REASONS) == {
        'token_missing', 'token_unauthorized', 'workspace_not_found',
        'project_not_visible', 'estimate_missing', 'measurement_missing',
        'rate_limited', 'timeout', 'railway_unreachable', 'response_changed'
    }
    cases = [
        ('token_unauthorized', lambda: u.UsageFailure('token_unauthorized')),
        ('rate_limited', lambda: u.UsageFailure('rate_limited')),
        ('timeout', lambda: u.UsageFailure('timeout')),
        ('railway_unreachable', lambda: u.UsageFailure('railway_unreachable')),
        ('response_changed', lambda: ValueError(marker)),
    ]
    for expected, factory in cases:
        def fails(token, query, variables):
            raise factory()
        monkeypatch.setattr(u, '_railway_query', fails)
        response = u._railway_usage(AT)
        assert response['status'] == 'unavailable'
        assert response['reasonCode'] == expected
        assert response['costSoFar'] is None
        assert 'synthetic-do-not-log' not in str(response)
        assert marker not in str(response)
    monkeypatch.setattr(u, '_railway_query', lambda token, query, variables: {
        'externalWorkspaces': [], 'me': {'workspaces': []}})
    assert u._railway_usage(AT)['reasonCode'] == 'workspace_not_found'
    monkeypatch.setattr(u, '_railway_query', lambda token, query, variables: {
        'externalWorkspaces': [], 'me': {'workspaces': [
            {'id': 'other', 'projects': {'edges': []}}]}})
    assert u._railway_usage(AT)['reasonCode'] == 'project_not_visible'
    assert marker not in capsys.readouterr().out


def test_transport_http_and_graphql_errors_are_sanitized(monkeypatch):
    from urllib.error import HTTPError, URLError
    import socket
    scenarios = [
        (HTTPError('https://example.invalid', 401, 'secret', None, None), 'token_unauthorized'),
        (HTTPError('https://example.invalid', 403, 'secret', None, None), 'token_unauthorized'),
        (HTTPError('https://example.invalid', 429, 'secret', None, None), 'rate_limited'),
        (HTTPError('https://example.invalid', 502, 'secret', None, None), 'railway_unreachable'),
        (URLError(socket.timeout('secret')), 'timeout'),
        (URLError('secret'), 'railway_unreachable'),
    ]
    for raised, code in scenarios:
        monkeypatch.setattr(u, 'urlopen', lambda *a, **kw: (_ for _ in ()).throw(raised))
        try:
            u._railway_query('fake-secret', 'query {}', {})
            assert False, 'Expected sanitized exception'
        except u.UsageFailure as exc:
            assert exc.code == code
            assert 'secret' not in str(exc)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return None
    for payload, code in [
        ({'errors': [{'message': 'unauthorized account token'}]}, 'token_unauthorized'),
        ({'errors': [{'extensions': {'code': 'RATE_LIMITED'}}]}, 'rate_limited'),
        ({'errors': [{'message': 'SCHEMA_CHANGED secret'}]}, 'response_changed'),
    ]:
        monkeypatch.setattr(u, 'urlopen', lambda *a, **kw: Response())
        monkeypatch.setattr(u.json, 'load', lambda *a, **kw: payload)
        try:
            u._railway_query('fake-secret', 'query {}', {})
            assert False
        except u.UsageFailure as exc:
            assert exc.code == code
            assert 'secret' not in str(exc)
