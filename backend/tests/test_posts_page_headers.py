from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.linkedin.services.posts as posts_module
from app.linkedin.services.posts import LinkedInPostsService


class _FakeResponse:
    def __init__(self, text: str = '<html>ok</html>', status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


@pytest.mark.asyncio
async def test_get_profile_page_headers_mimic_browser_navigation() -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})

    headers = service.get_profile_page_headers()

    assert headers['accept'] == 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
    assert headers['sec-fetch-dest'] == 'document'
    assert headers['sec-fetch-mode'] == 'navigate'
    assert headers['sec-fetch-site'] == 'none'
    assert headers['sec-fetch-user'] == '?1'
    assert headers['upgrade-insecure-requests'] == '1'
    assert 'csrf-token' not in headers
    assert 'x-restli-protocol-version' not in headers
    assert 'cookie' in headers
    assert 'li_at=fake' in headers['cookie']


@pytest.mark.asyncio
async def test_fetch_post_html_uses_profile_page_headers(monkeypatch) -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None):
            captured['method'] = method
            captured['url'] = url
            captured['headers'] = headers
            return _FakeResponse(text='<html>post</html>')

    monkeypatch.setattr(posts_module.httpx, 'AsyncClient', FakeClient)

    html = await service.fetch_post_html('https://www.linkedin.com/feed/update/urn:li:activity:123/')

    assert html == '<html>post</html>'
    assert captured['method'] == 'GET'
    assert captured['url'] == 'https://www.linkedin.com/feed/update/urn:li:activity:123/'
    assert captured['headers']['accept'].startswith('text/html,application/xhtml+xml')
    assert captured['headers']['sec-fetch-dest'] == 'document'
    assert 'x-restli-protocol-version' not in captured['headers']
    assert 'csrf-token' not in captured['headers']


@pytest.mark.asyncio
async def test_fetch_profile_activity_html_uses_profile_page_headers(monkeypatch) -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            captured['url'] = url
            captured['headers'] = headers
            return _FakeResponse(text='<html>activity</html>')

    monkeypatch.setattr(posts_module.httpx, 'AsyncClient', FakeClient)

    html = await service._fetch_profile_activity_html('rafaelmoreiraai')

    assert html == '<html>activity</html>'
    assert captured['url'] == 'https://www.linkedin.com/in/rafaelmoreiraai/recent-activity/posts/'
    assert captured['headers']['accept'].startswith('text/html,application/xhtml+xml')
    assert captured['headers']['sec-fetch-mode'] == 'navigate'
    assert 'csrf-token' not in captured['headers']


@pytest.mark.asyncio
async def test_resolve_profile_identity_uses_profile_page_headers(monkeypatch) -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    seen = {}

    async def fake_extract_profile_id(profile_input, headers, timeout):
        seen['profile_input'] = profile_input
        seen['headers'] = headers
        seen['timeout'] = timeout
        assert headers['accept'].startswith('text/html,application/xhtml+xml')
        assert headers['sec-fetch-mode'] == 'navigate'
        assert 'x-restli-protocol-version' not in headers
        assert 'csrf-token' not in headers
        return 'profile-123'

    monkeypatch.setattr(posts_module, 'extract_profile_id', fake_extract_profile_id)

    identity = await service._resolve_profile_identity('https://www.linkedin.com/in/rafaelmoreiraai/')

    assert seen['profile_input'] == 'https://www.linkedin.com/in/rafaelmoreiraai/'
    assert seen['timeout'] == service.TIMEOUT
    assert identity['profile_id'] == 'profile-123'
    assert identity['vanity'] == 'rafaelmoreiraai'
    assert identity['profile_url'] == 'https://www.linkedin.com/in/rafaelmoreiraai/'
