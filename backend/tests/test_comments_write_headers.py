from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("LINKEDIN_CLIENT_ID", "test")
os.environ.setdefault("LINKEDIN_CLIENT_SECRET", "test")

from app.api.v1 import comments as comments_api
from app.linkedin.services.comments import LinkedInCommentsService


class _FakeCommentsService:
    def __init__(self):
        self.fetch_calls = 0
        self.write_calls = 0
        self.last_fetch_headers = None
        self.last_write_headers = None
        self.last_request_headers = None
        self.headers = {
            "csrf-token": "ajax:test",
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "cookie": 'JSESSIONID="ajax:test";',
        }

    def get_commenter_fetch_headers(self):
        self.fetch_calls += 1
        self.last_fetch_headers = {
            **self.headers,
        }
        return self.last_fetch_headers

    def get_comment_write_headers(self):
        self.write_calls += 1
        self.last_write_headers = {
            **self.headers,
            "content-type": "application/json; charset=UTF-8",
        }
        return self.last_write_headers

    async def prepare_post_comment_request(self, post_url, comment_text):
        return "https://www.linkedin.com/voyager/api/graphql?post=1", {"commentary": {"text": comment_text}, "threadUrn": post_url}

    async def prepare_reply_to_comment_request(self, comment_urn, reply_text):
        return "https://www.linkedin.com/voyager/api/graphql?reply=1", {"commentary": {"text": reply_text}, "threadUrn": comment_urn}

    async def _make_request(self, url, method="GET", headers=None, json=None):
        self.last_request_headers = headers
        return {"ok": True}


async def _exercise_post_comment(monkeypatch, server_call: bool):
    fake_api_key = SimpleNamespace(user_id="user-1", instance_id="inst-1")
    service = _FakeCommentsService()
    proxy_calls = []

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        return service

    async def fake_proxy_http_request(**kwargs):
        proxy_calls.append(kwargs)
        return {"status_code": 200, "body": {"success": True}}

    monkeypatch.setattr(comments_api, "validate_api_key_from_header_or_body", fake_validate)
    monkeypatch.setattr(comments_api, "get_linkedin_service", fake_get_service)
    monkeypatch.setattr(comments_api, "proxy_http_request", fake_proxy_http_request)

    payload = comments_api.PostCommentRequest(
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:123",
        comment_text="hello",
        api_key="abc",
        server_call=server_call,
    )

    result = await comments_api.post_comment_to_post(  # type: ignore[attr-defined, arg-type]
        payload,
        ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)),
        db=SimpleNamespace(),
        x_api_key=None,
    )

    return result, service, proxy_calls


async def _exercise_reply_to_comment(monkeypatch, server_call: bool):
    fake_api_key = SimpleNamespace(user_id="user-1", instance_id="inst-1")
    service = _FakeCommentsService()
    proxy_calls = []

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        return service

    async def fake_proxy_http_request(**kwargs):
        proxy_calls.append(kwargs)
        return {"status_code": 200, "body": {"success": True}}

    monkeypatch.setattr(comments_api, "validate_api_key_from_header_or_body", fake_validate)
    monkeypatch.setattr(comments_api, "get_linkedin_service", fake_get_service)
    monkeypatch.setattr(comments_api, "proxy_http_request", fake_proxy_http_request)

    payload = comments_api.ReplyToCommentRequest(
        comment_urn="urn:li:fsd_comment:(456,urn:li:activity:123)",
        reply_text="hello back",
        api_key="abc",
        server_call=server_call,
    )

    result = await comments_api.reply_to_comment(  # type: ignore[arg-type]
        payload,
        ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)),
        db=SimpleNamespace(),
        x_api_key=None,
    )

    return result, service, proxy_calls


def test_post_comment_uses_write_headers_in_proxy_and_server_call(monkeypatch):
    proxy_result, proxy_service, proxy_calls = asyncio.run(_exercise_post_comment(monkeypatch, server_call=False))
    server_result, server_service, server_proxy_calls = asyncio.run(_exercise_post_comment(monkeypatch, server_call=True))

    assert proxy_result == {"success": True}
    assert server_result == {"success": True}

    assert proxy_service.fetch_calls == 0
    assert proxy_service.write_calls == 1
    assert proxy_calls
    assert proxy_calls[0]["headers"] == proxy_service.last_write_headers
    assert proxy_calls[0]["headers"]["content-type"] == "application/json; charset=UTF-8"

    assert server_service.fetch_calls == 0
    assert server_service.write_calls == 1
    assert server_service.last_request_headers == server_service.last_write_headers
    assert server_service.last_request_headers["content-type"] == "application/json; charset=UTF-8"
    assert server_proxy_calls == []


def test_reply_to_comment_uses_write_headers_in_proxy_and_server_call(monkeypatch):
    proxy_result, proxy_service, proxy_calls = asyncio.run(_exercise_reply_to_comment(monkeypatch, server_call=False))
    server_result, server_service, server_proxy_calls = asyncio.run(_exercise_reply_to_comment(monkeypatch, server_call=True))

    assert proxy_result == {"success": True}
    assert server_result == {"success": True}

    assert proxy_service.fetch_calls == 0
    assert proxy_service.write_calls == 1
    assert proxy_calls
    assert proxy_calls[0]["headers"] == proxy_service.last_write_headers
    assert proxy_calls[0]["headers"]["content-type"] == "application/json; charset=UTF-8"

    assert server_service.fetch_calls == 0
    assert server_service.write_calls == 1
    assert server_service.last_request_headers == server_service.last_write_headers
    assert server_service.last_request_headers["content-type"] == "application/json; charset=UTF-8"
    assert server_proxy_calls == []


def test_real_comment_write_headers_match_browser_capture():
    service = LinkedInCommentsService(
        csrf_token='ajax:test',
        linkedin_cookies={
            'JSESSIONID': 'ajax:test',
            'li_at': 'token',
            'bcookie': 'b',
            'bscookie': 'bs',
            'lidc': 'ld',
        },
    )

    headers = service.get_comment_write_headers()

    assert headers['content-type'] == 'application/json; charset=UTF-8'
    assert headers['referer'] == 'https://www.linkedin.com/preload/'
    assert headers['x-li-pem-metadata'] == 'Voyager - Feed - Comments=create-a-comment-reply'
    assert headers['x-li-deco-include-micro-schema'] == 'true'
    assert headers['x-li-lang'] == 'pt_BR'
    assert headers['sec-fetch-site'] == 'same-origin'
    assert 'bcookie=' in headers['cookie']
    assert 'lidc=' in headers['cookie']


def test_prepare_reply_to_comment_normalizes_activity_to_ugc_post(monkeypatch):
    service = LinkedInCommentsService(
        csrf_token='ajax:test',
        linkedin_cookies={
            'JSESSIONID': 'ajax:test',
            'li_at': 'token',
            'bcookie': 'b',
            'bscookie': 'bs',
            'lidc': 'ld',
        },
    )

    async def fake_get_ugc_post_urn_from_activity(activity_id: str) -> str:
        assert activity_id == '123'
        return 'urn:li:ugcPost:999'

    monkeypatch.setattr(service, '_get_ugc_post_urn_from_activity', fake_get_ugc_post_urn_from_activity)

    url, payload = asyncio.run(
        service.prepare_reply_to_comment_request(
            'urn:li:fsd_comment:(456,urn:li:activity:123)',
            'hello back',
        )
    )

    assert url.endswith('voyagerSocialDashNormComments?decorationId=com.linkedin.voyager.dash.deco.social.NormComment-43')
    assert payload['threadUrn'] == 'urn:li:comment:(ugcPost:999,456)'
    assert payload['commentary']['text'] == 'hello back'
    assert payload['commentary']['attributesV2'] == []
    assert payload['commentary']['$type'] == 'com.linkedin.voyager.dash.common.text.TextViewModel'
