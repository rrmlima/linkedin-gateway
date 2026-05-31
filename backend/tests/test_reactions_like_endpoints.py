from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote

from app.api.v1 import reactions


class _FakeService:
    headers = {'csrf-token': 'ajax:test', 'x-li-lang': 'pt_BR'}


async def _run_like_post(monkeypatch):
    fake_api_key = SimpleNamespace(user_id='user-1', instance_id='inst-1')
    captured = {}

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        return _FakeService()

    async def fake_get_profile_id(*args, **kwargs):
        return 'ACoAAA123'

    async def fake_proxy_http_request(**kwargs):
        captured['kwargs'] = kwargs
        return {'status_code': 201, 'body': {'success': True}}

    monkeypatch.setattr(reactions, 'validate_api_key_from_header_or_body', fake_validate)
    monkeypatch.setattr(reactions, 'get_linkedin_service', fake_get_service)
    monkeypatch.setattr(reactions, 'get_my_profile_id_with_fallbacks', fake_get_profile_id)
    monkeypatch.setattr(reactions, 'proxy_http_request', fake_proxy_http_request)

    payload = reactions.LikePostRequest(post_url='https://www.linkedin.com/feed/update/urn:li:activity:123', api_key='abc', server_call=False)
    result = await reactions.like_post(payload, ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)), db=SimpleNamespace(), x_api_key=None)

    return result, captured


async def _run_like_comment(monkeypatch):
    fake_api_key = SimpleNamespace(user_id='user-1', instance_id='inst-1')
    captured = {}

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        return _FakeService()

    async def fake_get_profile_id(*args, **kwargs):
        return 'ACoAAA123'

    async def fake_proxy_http_request(**kwargs):
        captured['kwargs'] = kwargs
        return {'status_code': 201, 'body': {'success': True}}

    monkeypatch.setattr(reactions, 'validate_api_key_from_header_or_body', fake_validate)
    monkeypatch.setattr(reactions, 'get_linkedin_service', fake_get_service)
    monkeypatch.setattr(reactions, 'get_my_profile_id_with_fallbacks', fake_get_profile_id)
    monkeypatch.setattr(reactions, 'proxy_http_request', fake_proxy_http_request)

    payload = reactions.LikeCommentRequest(comment_urn='urn:li:fsd_comment:(456,urn:li:activity:123)', api_key='abc', server_call=False)
    result = await reactions.like_comment(payload, ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)), db=SimpleNamespace(), x_api_key=None)

    return result, captured


def test_extract_comment_object_urn():
    assert reactions._extract_comment_object_urn('urn:li:comment:(urn:li:activity:123,456)') == 'urn:li:activity:123'
    assert reactions._extract_comment_object_urn('urn:li:fsd_comment:(456,urn:li:activity:123)') == 'urn:li:activity:123'
    assert reactions._normalize_comment_reaction_urn('urn:li:fsd_comment:(456,urn:li:activity:123)') == 'urn:li:comment:(urn:li:activity:123,456)'
    assert reactions._comment_graphql_thread_urn('urn:li:fsd_comment:(456,urn:li:activity:123)') == 'urn:li:comment:(activity:123,456)'
    assert reactions._extract_comment_object_urn('urn:li:comment:123') == 'urn:li:comment:123'


def test_like_post_proxy_request(monkeypatch):
    result, captured = asyncio.run(_run_like_post(monkeypatch))
    assert result.success is True
    assert result.already_liked is False
    assert result.mode == 'proxy'
    assert result.target_urn == 'urn:li:activity:123'
    assert result.object_urn == 'urn:li:activity:123'
    assert captured['kwargs']['method'] == 'POST'
    assert captured['kwargs']['url'] == reactions._DASH_REACTIONS_URL
    body = json.loads(captured['kwargs']['body'])
    assert body['variables']['entity']['reactionType'] == 'LIKE'
    assert body['variables']['threadUrn'] == 'urn:li:activity:123'
    assert body['queryId'] == reactions._DASH_REACTIONS_QUERY_ID
    assert body['includeWebMetadata'] is True


def test_like_comment_proxy_request(monkeypatch):
    result, captured = asyncio.run(_run_like_comment(monkeypatch))
    assert result.success is True
    assert result.mode == 'proxy'
    assert result.target_urn == 'urn:li:comment:(urn:li:activity:123,456)'
    assert result.object_urn == 'urn:li:activity:123'
    assert captured['kwargs']['url'] == reactions._DASH_REACTIONS_URL
    body = json.loads(captured['kwargs']['body'])
    assert body['variables']['entity']['reactionType'] == 'LIKE'
    assert body['variables']['threadUrn'] == 'urn:li:comment:(activity:123,456)'
    assert body['queryId'] == reactions._DASH_REACTIONS_QUERY_ID
    assert body['includeWebMetadata'] is True
