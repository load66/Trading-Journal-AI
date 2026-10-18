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


USAGE_ROWS={"svc-a":{"CPU_USAGE":3,"MEMORY_USAGE_GB":400,"NETWORK_TX_GB":0.1},
            "svc-b":{"CPU_USAGE":12,"MEMORY_USAGE_GB":900,"NETWORK_TX_GB":0.2}}


def _remote(token,query,variables):
    assert token == 'synthetic-do-not-log'
    if 'BonusHostingVerify' in query:
        return {"me":{"id":"synthetic-user"}}
    if 'BonusHostingProject' in query:
        return {"project":{"workspaceId":"synthetic-workspace",
                           "services":{"edges":[{"node":{"id":"svc-a","name":"api"}},
                                                {"node":{"id":"svc-b","name":"leaps-alpaca-live"}}]}}}
    if 'BonusHostingUsage' in query:
        return {"usage":[{"measurement":m,"value":v,"tags":{"projectId":"synthetic-project","serviceId":id}}
                         for id,parts in USAGE_ROWS.items() for m,v in parts.items()]}
    if 'BonusHostingEstimate' in query:
        return {"estimatedUsage":[{"measurement":"CPU_USAGE","estimatedValue":30,"projectId":"synthetic-project"},
                                  {"measurement":"MEMORY_USAGE_GB","estimatedValue":3500,"projectId":"synthetic-project"},
                                  {"measurement":"NETWORK_TX_GB","estimatedValue":0.8,"projectId":"synthetic-project"}]}
    if 'BonusHostingMemory' in query:
        return {"metrics":[{"tags":{"serviceId":"svc-a"},"values":[{"ts":1,"value":0.27},{"ts":2,"value":0.33}]},
                           {"tags":{"serviceId":"svc-b"},"values":[{"ts":1,"value":0.6}]}]}
    raise AssertionError('unexpected query')


def _patched(base, op, change):
    def remote(token, query, variables):
        value = base(token, query, variables)
        if op in query:
            change(value)
        return value
    return remote


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


def test_schema_queries_each_railway_field_separately():
    assert 'me { id }' in u._VERIFY and 'project' not in u._VERIFY
    assert 'workspaceId' in u._PROJECT and 'services' in u._PROJECT and 'usage' not in u._PROJECT
    assert 'usage(workspaceId: $workspaceId, projectId: $projectId' in u._USAGE and 'estimatedUsage' not in u._USAGE
    assert 'groupBy: [PROJECT_ID, SERVICE_ID]' in u._USAGE and 'endDate: $endDate' in u._USAGE
    assert 'estimatedUsage(workspaceId: $workspaceId, projectId: $projectId' in u._ESTIMATE
    assert 'metrics(environmentId: $environmentId, projectId: $projectId' in u._METRICS
    assert 'groupBy: [SERVICE_ID]' in u._METRICS


def test_workspace_or_other_project_usage_is_never_attributed(monkeypatch):
    _configured(monkeypatch)
    def remote(token,query,variables):
        value=_remote(token,query,variables)
        if 'BonusHostingEstimate' in query:
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
    monkeypatch.setattr(u,'cached_usage',lambda user,refresh=False:called.append((user,refresh)) or {'month':'2026-10'})
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
        if 'BonusHostingUsage' in query:
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
        for row in payload.get('usage', []):
            row['value'] = 0
        for row in payload.get('estimatedUsage', []):
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
        if 'BonusHostingEstimate' in query:
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
        'token_missing', 'token_unauthorized', 'permission_denied', 'workspace_not_found',
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
    monkeypatch.setattr(u, '_railway_query', _patched(_remote, 'BonusHostingProject', lambda v: v.update(project=None)))
    assert u._railway_usage(AT)['reasonCode'] == 'project_not_visible'
    assert marker not in capsys.readouterr().out


def test_transport_http_and_graphql_errors_are_sanitized(monkeypatch):
    from urllib.error import HTTPError, URLError
    import socket
    scenarios = [
        (HTTPError('https://example.invalid', 401, 'secret', None, None), 'denied'),
        (HTTPError('https://example.invalid', 403, 'secret', None, None), 'denied'),
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
        ({'errors': [{'message': 'unauthorized account token'}]}, 'denied'),
        ({'errors': [{'message': 'Not Authorized', 'extensions': {'code': 'INTERNAL_SERVER_ERROR'}}]}, 'denied'),
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


def _deny(op):
    def remote(token, query, variables):
        if op in query:
            raise u.UsageFailure('denied')
        return _remote(token, query, variables)
    return remote


def test_valid_token_denied_estimate_is_permission_denied_partial(monkeypatch, capsys):
    # Regression: a denied estimatedUsage used to be reported as a rejected token.
    _configured(monkeypatch)
    monkeypatch.setattr(u, '_railway_query', _deny('BonusHostingEstimate'))
    value = u._railway_usage(AT)
    assert value['status'] == 'partial'
    assert value['reasonCode'] == 'permission_denied'
    assert value['deniedFields'] == ['estimatedUsage']
    assert value['reason'] == "Railway doesn't allow this token to read estimatedUsage."
    expected = round(sum(u._metric_cost(p) for p in USAGE_ROWS.values()), 4)
    assert value['costSoFar'] == expected and value['costSoFar'] > 0, 'verified month-to-date cost is kept'
    assert value['projectedMonthEnd'] is None, 'never a fake $0.00 projection'
    assert value['projectionNote'] == 'Projection not available from Railway'
    assert all(s['projectedMonthEnd'] is None for s in value['services'])
    assert {'service': 'project', 'metric': 'estimatedUsage', 'kind': 'permission_denied'} in value['missing']
    out = capsys.readouterr()
    assert 'synthetic-do-not-log' not in out.out + out.err + str(value)


def test_estimate_denied_in_both_scopes_before_partial(monkeypatch):
    _configured(monkeypatch)
    seen = []
    def remote(token, query, variables):
        if 'BonusHostingEstimate' in query:
            seen.append((variables['workspaceId'], variables['projectId']))
        return _deny('BonusHostingEstimate')(token, query, variables)
    monkeypatch.setattr(u, '_railway_query', remote)
    assert u._railway_usage(AT)['reasonCode'] == 'permission_denied'
    assert seen == [('synthetic-workspace', None), (None, 'synthetic-project')]


def test_workspace_scope_denied_falls_back_to_project_scope(monkeypatch):
    _configured(monkeypatch)
    def remote(token, query, variables):
        if ('BonusHostingUsage' in query or 'BonusHostingEstimate' in query) and variables.get('workspaceId'):
            raise u.UsageFailure('denied')
        return _remote(token, query, variables)
    monkeypatch.setattr(u, '_railway_query', remote)
    value = u._railway_usage(AT)
    assert value['status'] == 'ok' and value['projectedMonthEnd'] > 0


def test_invalid_token_on_me_is_token_unauthorized_and_stops(monkeypatch):
    _configured(monkeypatch)
    calls = []
    def remote(token, query, variables):
        calls.append(query)
        return _deny('BonusHostingVerify')(token, query, variables)
    monkeypatch.setattr(u, '_railway_query', remote)
    value = u._railway_usage(AT)
    assert value['status'] == 'unavailable'
    assert value['reasonCode'] == 'token_unauthorized'
    assert value['failedStep'] == 'me'
    assert len(calls) == 1, 'no billing query after a rejected token'


def test_denial_after_token_check_is_never_token_unauthorized(monkeypatch):
    _configured(monkeypatch)
    for op, field in [('BonusHostingProject', 'project'), ('BonusHostingUsage', 'usage')]:
        monkeypatch.setattr(u, '_railway_query', _deny(op))
        value = u._railway_usage(AT)
        assert value['status'] == 'unavailable'
        assert value['reasonCode'] == 'permission_denied'
        assert value['deniedFields'] == [field] and value['failedStep'] == field
        assert value['reason'] == f"Railway doesn't allow this token to read {field}."
        assert value['costSoFar'] is None
    monkeypatch.setattr(u, '_railway_query', _deny('BonusHostingMemory'))
    value = u._railway_usage(AT)
    assert value['status'] == 'partial' and value['reasonCode'] == 'permission_denied'
    assert value['deniedFields'] == ['metrics'] and value['projectedMonthEnd'] is not None


def test_transient_estimate_failure_keeps_cost_and_its_own_code(monkeypatch):
    _configured(monkeypatch)
    def remote(token, query, variables):
        if 'BonusHostingEstimate' in query:
            raise u.UsageFailure('timeout')
        return _remote(token, query, variables)
    monkeypatch.setattr(u, '_railway_query', remote)
    value = u._railway_usage(AT)
    assert value['status'] == 'partial' and value['reasonCode'] == 'timeout'
    assert value['costSoFar'] > 0 and value['projectedMonthEnd'] is None
    assert 'deniedFields' not in value


def test_step_failures_log_fixed_identifiers_only(monkeypatch, caplog):
    _configured(monkeypatch)
    monkeypatch.setattr(u, '_railway_query', _deny('BonusHostingEstimate'))
    with caplog.at_level('INFO', logger='uvicorn.error'):
        u._railway_usage(AT)
        u.startup_probe()
    text = caplog.text
    assert 'bonus_usage_step_failed step=estimatedUsage code=denied' in text
    assert 'bonus_usage_probe status=partial reason=permission_denied step=none denied=estimatedUsage' in text
    assert 'synthetic-do-not-log' not in text


def _snapshot_counter(monkeypatch, status):
    calls = []
    monkeypatch.setattr(u, 'usage_snapshot', lambda now: calls.append(now) or {'railway': {'status': status}})
    return calls


def test_failure_cache_expires_after_two_minutes(monkeypatch):
    u._cache.clear(); u._refreshed_at.clear()
    calls = _snapshot_counter(monkeypatch, 'unavailable')
    u.cached_usage('owner'); u.cached_usage('owner')
    assert len(calls) == 1
    key = list(u._cache)[0]
    stamp, value = u._cache[key]
    u._cache[key] = (stamp - 119, value)
    u.cached_usage('owner')
    assert len(calls) == 1
    u._cache[key] = (stamp - 121, value)
    u.cached_usage('owner')
    assert len(calls) == 2, 'failures are cached at most two minutes, not an hour'
    calls = _snapshot_counter(monkeypatch, 'partial')
    u._cache.clear()
    u.cached_usage('owner')
    key = list(u._cache)[0]
    stamp, value = u._cache[key]
    u._cache[key] = (stamp - 121, value)
    u.cached_usage('owner')
    assert len(calls) == 2, 'partial results are not cached for an hour'
    u._cache.clear()


def test_refresh_bypasses_cache_once_per_minute_per_owner(monkeypatch):
    u._cache.clear(); u._refreshed_at.clear()
    calls = _snapshot_counter(monkeypatch, 'ok')
    clock = [1000.0]
    monkeypatch.setattr(u.time, 'monotonic', lambda: clock[0])
    u.cached_usage('owner')
    u.cached_usage('owner', refresh=True)
    assert len(calls) == 2, 'refresh bypasses a fresh hour-long cache'
    clock[0] += 30
    u.cached_usage('owner', refresh=True)
    assert len(calls) == 2, 'second refresh within a minute serves the cache'
    clock[0] += 31
    u.cached_usage('owner', refresh=True)
    assert len(calls) == 3
    u.cached_usage('other-owner', refresh=True)
    assert len(calls) == 4, 'the limit is per owner'
    u._cache.clear(); u._refreshed_at.clear()


def test_refresh_query_parameter_reaches_cache(monkeypatch):
    monkeypatch.setattr(b, 'auth_required', lambda: True)
    monkeypatch.setattr(b, 'authorize_header', lambda header: {'sub': 'owner'})
    called = []
    monkeypatch.setattr(u, 'cached_usage', lambda user, refresh=False: called.append(refresh) or {'month': '2026-10'})
    app = FastAPI(); app.include_router(u.router)
    client = TestClient(app)
    client.get('/api/bonus-tracker/usage', headers={'Authorization': 'Bearer x'})
    client.get('/api/bonus-tracker/usage?refresh=1', headers={'Authorization': 'Bearer x'})
    client.get('/api/bonus-tracker/usage?refresh=true', headers={'Authorization': 'Bearer x'})
    assert called == [False, True, False]


def test_snapshot_reports_actual_cache_lifetime(monkeypatch):
    monkeypatch.setattr(u, '_r2_usage', lambda: {'status': 'ok'})
    monkeypatch.setattr(u, '_railway_usage', lambda now: {'status': 'ok'})
    assert u.usage_snapshot(AT)['cacheSeconds'] == 3600
    monkeypatch.setattr(u, '_railway_usage', lambda now: {'status': 'unavailable'})
    assert u.usage_snapshot(AT)['cacheSeconds'] == 120
