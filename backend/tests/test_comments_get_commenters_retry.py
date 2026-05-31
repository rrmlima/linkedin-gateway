from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import httpx

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("LINKEDIN_CLIENT_ID", "test")
os.environ.setdefault("LINKEDIN_CLIENT_SECRET", "test")

from app.api.v1 import comments as comments_api


class _FakeCommentsService:
    def __init__(self, responses):
        self.headers = {"csrf-token": "ajax:test", "x-li-lang": "pt_BR"}
        self._responses = list(responses)
        self.request_calls = 0
        self.built_urls = []

    def _build_commenters_url(self, post_url, start=0, count=10, num_replies=1, pagination_token=None):
        url = "https://www.linkedin.com/voyager/api/graphql?fake=1"
        self.built_urls.append((post_url, start, count, num_replies, pagination_token, url))
        return url

    async def _make_request(self, url):
        self.request_calls += 1
        if not self._responses:
            raise AssertionError("No more queued responses")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _parse_commenters_response(self, data, include_replies):
        return [], None, None, None, []

    def _build_comment_relationships(self, comments, social_details):
        return {}

    def _extract_comment_id_from_urn(self, urn):
        return None


async def _run_get_commenters_retry(monkeypatch):
    fake_api_key = SimpleNamespace(user_id="user-1", instance_id="inst-1")
    refresh_calls = {"count": 0}
    get_service_calls = {"count": 0}

    request = httpx.Request("GET", "https://www.linkedin.com/voyager/api/graphql?fake=1")
    response = httpx.Response(403, request=request, text="Forbidden")
    first_exc = httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    first_service = _FakeCommentsService([first_exc])
    second_service = _FakeCommentsService([{"data": {}, "included": []}])
    services = [first_service, second_service]

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        get_service_calls["count"] += 1
        if not services:
            raise AssertionError("get_linkedin_service called too many times")
        return services.pop(0)

    async def fake_refresh(*args, **kwargs):
        refresh_calls["count"] += 1
        return {"csrf_token": "ajax:test", "cookies": {"JSESSIONID": '"ajax:test"'}}

    async def fake_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(comments_api, "validate_api_key_from_header_or_body", fake_validate)
    monkeypatch.setattr(comments_api, "get_linkedin_service", fake_get_service)
    monkeypatch.setattr(comments_api, "refresh_linkedin_session", fake_refresh)
    monkeypatch.setattr(comments_api, "apply_pagination_delay", fake_delay)

    payload = comments_api.GetCommentersRequest(
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:123",
        count=1,
        num_replies=1,
        api_key="abc",
        server_call=True,
    )

    result = await comments_api.get_post_commenters(
        payload,
        ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)),
        db=SimpleNamespace(),
        x_api_key=None,
    )

    return result, refresh_calls, get_service_calls, first_service, second_service


def test_get_commenters_server_call_refreshes_and_retries_on_403(monkeypatch):
    result, refresh_calls, get_service_calls, first_service, second_service = asyncio.run(_run_get_commenters_retry(monkeypatch))

    assert result.data == []
    assert refresh_calls["count"] == 1
    assert get_service_calls["count"] == 2
    assert first_service.request_calls == 1
    assert second_service.request_calls == 1
