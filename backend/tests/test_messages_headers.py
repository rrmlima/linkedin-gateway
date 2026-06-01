from __future__ import annotations

import asyncio
import html
import json
import os
from types import SimpleNamespace

import httpx

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("LINKEDIN_CLIENT_ID", "test")
os.environ.setdefault("LINKEDIN_CLIENT_SECRET", "test")

from app.api.v1 import messages
from app.linkedin.services.messages import LinkedInMessageService
import app.linkedin.utils.my_profile_id as my_profile_id_utils


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.last_get = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.last_get = {"url": url, "headers": headers}
        data_content = json.dumps(
            {
                "data": {
                    "data": {
                        "identityDashProfilesByMemberIdentity": {
                            "*elements": ["urn:li:fsd_profile:target-123"]
                        }
                    }
                }
            }
        )
        html_doc = (
            '<code id="bpr-guid-1">'
            f"{html.escape(data_content)}"
            '</code><code id="datalet-bpr-guid-1">'
            'voyagerIdentityDashProfiles vanityName:alice'
            '</code>'
        )
        return _FakeResponse(html_doc)


class _FakeMessageService:
    def __init__(self):
        self.headers = LinkedInMessageService(csrf_token='"ajax:test"').headers
        self.prepare_calls = []

    def get_profile_page_headers(self):
        return self.headers

    async def prepare_send_message_request(self, target_profile_id, message_text, my_profile_id=None):
        self.prepare_calls.append((target_profile_id, message_text, my_profile_id))
        return (
            "https://www.linkedin.com/voyager/api/voyagerMessagingCreateConversation",
            {"message": message_text, "targetProfileId": target_profile_id, "myProfileId": my_profile_id},
            None,
        )


async def _run_send_message_proxy(monkeypatch):
    fake_api_key = SimpleNamespace(user_id="user-1", instance_id="inst-1")
    service = _FakeMessageService()
    captured = {}

    async def fake_validate(*args, **kwargs):
        return fake_api_key

    async def fake_get_service(db, api_key, service_cls):
        return service

    async def fake_get_my_profile_id(*args, **kwargs):
        return "my-profile-id"

    async def fake_proxy_http_request(**kwargs):
        captured["kwargs"] = kwargs
        return {"status_code": 200, "body": '{"success": true}'}

    monkeypatch.setattr(messages, "validate_api_key_from_header_or_body", fake_validate)
    monkeypatch.setattr(messages, "get_linkedin_service", fake_get_service)
    monkeypatch.setattr(messages, "proxy_http_request", fake_proxy_http_request)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(my_profile_id_utils, "get_my_profile_id_with_fallbacks", fake_get_my_profile_id)

    payload = messages.SendMessageRequest(
        profile_identifier="alice",
        message_text="oi tudo bem",
        api_key="abc",
        server_call=False,
    )

    result = await messages.send_direct_message(  # type: ignore[arg-type]
        payload,
        ws_handler=SimpleNamespace(connection_manager=SimpleNamespace(is_instance_connected=lambda _: True)),
        db=SimpleNamespace(),
        x_api_key=None,
    )

    return result, service, captured


def test_message_service_headers_keep_messaging_contract():
    service = LinkedInMessageService(csrf_token='"ajax:test"')

    headers = service.headers

    assert headers["csrf-token"] == "ajax:test"
    assert headers["accept"] == "*/*"
    assert headers["content-type"] == "text/plain;charset=UTF-8"
    assert headers["cookie"] == 'JSESSIONID="ajax:test";'
    assert headers["x-restli-protocol-version"] == "2.0.0"


def test_send_direct_message_proxy_keeps_messaging_headers(monkeypatch):
    result, service, captured = asyncio.run(_run_send_message_proxy(monkeypatch))

    assert result.success is True
    assert service.prepare_calls == [("target-123", "oi tudo bem", "my-profile-id")]
    assert captured["kwargs"]["headers"]["csrf-token"] == "ajax:test"
    assert captured["kwargs"]["headers"]["accept"] == "*/*"
    assert captured["kwargs"]["headers"]["content-type"] == "text/plain;charset=UTF-8"
    assert "cookie" not in captured["kwargs"]["headers"]
