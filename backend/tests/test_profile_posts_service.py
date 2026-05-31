from __future__ import annotations

import asyncio
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


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requested.append((url, headers))
        return self.response


@pytest.mark.parametrize(
    'value, expected',
    [
        ('urn:li:activity:7462426912188112897', '7462426912188112897'),
        ('https://www.linkedin.com/feed/update/urn:li:activity:7462426912188112897/', '7462426912188112897'),
        ('https://www.linkedin.com/posts/rafaelmoreiraai_post-activity-7462426912188112897-0_GL/', '7462426912188112897'),
        ('urn:li:ugcPost:7455099217087197184', '7455099217087197184'),
        ('https://www.linkedin.com/posts/rafaelmoreiraai_post-ugcPost-7455099217087197184--bCZ', '7455099217087197184'),
    ],
)
def test_extract_tracking_id(value: str, expected: str) -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    assert service._extract_tracking_id(value) == expected


def test_parse_profile_activity_html_extracts_posts_from_code_blocks() -> None:
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    html = '''
    <html><body>
    <code id="bpr-guid-1">
    {&quot;included&quot;:[
      {
        &quot;$type&quot;:&quot;com.linkedin.voyager.dash.feed.Update&quot;,
        &quot;entityUrn&quot;:&quot;urn:li:activity:7462426912188112897&quot;,
        &quot;commentary&quot;:{&quot;text&quot;:{&quot;text&quot;:&quot;Planejamento industrial e parada de manutenção...&quot;}},
        &quot;actor&quot;:{&quot;name&quot;:{&quot;text&quot;:&quot;Rafael Moreira Lima&quot;},&quot;navigationUrl&quot;:&quot;https://www.linkedin.com/in/rafaelmoreiraai/&quot;},
        &quot;socialDetail&quot;:{&quot;totalSocialActivityCounts&quot;:{&quot;numLikes&quot;:10,&quot;numComments&quot;:2}}
      }
    ]}
    </code>
    </body></html>
    '''

    posts = service._parse_profile_activity_html(
        html,
        {'profile_id': 'ACoAAAXoHu8BUu_E0D-Fvled4xb6hn1N_EhH4nU', 'name': 'Rafael Moreira Lima'},
    )

    assert len(posts) == 1
    assert posts[0]['postId'] == 'urn:li:activity:7462426912188112897'
    assert posts[0]['postUrl'] == 'https://www.linkedin.com/feed/update/urn:li:activity:7462426912188112897/'
    assert posts[0]['authorName'] == 'Rafael Moreira Lima'
    assert posts[0]['authorProfileId'] == 'ACoAAAXoHu8BUu_E0D-Fvled4xb6hn1N_EhH4nU'
    assert posts[0]['likes'] == 10
    assert posts[0]['comments'] == 2
    assert 'Planejamento industrial' in posts[0]['postContent']


@pytest.mark.asyncio
async def test_fetch_profile_activity_html_uses_recent_activity_url(monkeypatch):
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    requested = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            requested['url'] = url
            requested['headers'] = headers
            return _FakeResponse(text='<html>ok</html>')

    monkeypatch.setattr(posts_module.httpx, 'AsyncClient', FakeClient)

    html = await service._fetch_profile_activity_html('rafaelmoreiraai')

    assert html == '<html>ok</html>'
    assert requested['url'] == 'https://www.linkedin.com/in/rafaelmoreiraai/recent-activity/posts/'


@pytest.mark.asyncio
async def test_fetch_posts_for_profile_uses_direct_activity_before_feed(monkeypatch):
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    calls = []

    async def fake_resolve(profile_input):
        calls.append('resolve')
        return {
            'profile_id': 'ACoAAAXoHu8BUu_E0D-Fvled4xb6hn1N_EhH4nU',
            'vanity': 'rafaelmoreiraai',
            'profile_url': 'https://www.linkedin.com/in/rafaelmoreiraai/',
        }

    async def fake_direct(identity, count):
        calls.append('direct')
        return [
            {
                'postId': 'urn:li:activity:7462426912188112897',
                'postUrl': 'https://www.linkedin.com/feed/update/urn:li:activity:7462426912188112897/',
                'authorName': 'Rafael Moreira Lima',
                'authorProfileId': 'ACoAAAXoHu8BUu_E0D-Fvled4xb6hn1N_EhH4nU',
                'postContent': 'Planejamento industrial...',
                'postDate': '2026-05-19T12:00:00Z',
                'postAge': '1d',
                'likes': 1,
                'comments': 2,
                'imageUrl': None,
                'isVideo': False,
                'hasVideo': False,
                'videoType': None,
            }
        ]

    async def fake_feed(profile_id, count, min_delay=0, max_delay=0):
        calls.append('feed')
        return []

    monkeypatch.setattr(service, '_resolve_profile_identity', fake_resolve)
    monkeypatch.setattr(service, '_fetch_profile_activity_posts', fake_direct)
    monkeypatch.setattr(service, '_scan_feed_for_profile_posts', fake_feed)

    result = await service.fetch_posts_for_profile('rafaelmoreiraai', count=10, min_delay=0, max_delay=0)

    assert calls == ['resolve', 'direct']
    assert len(result['posts']) == 1
    assert result['posts'][0]['postId'] == 'urn:li:activity:7462426912188112897'


@pytest.mark.asyncio
async def test_fetch_posts_for_profile_falls_back_to_feed_when_direct_empty(monkeypatch):
    service = LinkedInPostsService(csrf_token='csrf', linkedin_cookies={'li_at': 'fake'})
    calls = []

    async def fake_resolve(profile_input):
        calls.append('resolve')
        return {
            'profile_id': 'ACoAAAXoHu8BUu_E0D-Fvled4xb6hn1N_EhH4nU',
            'vanity': 'rafaelmoreiraai',
            'profile_url': 'https://www.linkedin.com/in/rafaelmoreiraai/',
        }

    async def fake_direct(identity, count):
        calls.append('direct')
        return []

    async def fake_feed(profile_id, count, min_delay=0, max_delay=0):
        calls.append('feed')
        return [
            {
                'postId': 'urn:li:activity:7462426912188112897',
                'postUrl': 'https://www.linkedin.com/feed/update/urn:li:activity:7462426912188112897/',
                'authorName': 'Rafael Moreira Lima',
                'authorProfileId': profile_id,
                'postContent': 'Planejamento industrial...',
                'postDate': '2026-05-19T12:00:00Z',
                'postAge': '1d',
                'likes': 0,
                'comments': 0,
                'imageUrl': None,
                'isVideo': False,
                'hasVideo': False,
                'videoType': None,
            }
        ]

    monkeypatch.setattr(service, '_resolve_profile_identity', fake_resolve)
    monkeypatch.setattr(service, '_fetch_profile_activity_posts', fake_direct)
    monkeypatch.setattr(service, '_scan_feed_for_profile_posts', fake_feed)

    result = await service.fetch_posts_for_profile('https://www.linkedin.com/in/rafaelmoreiraai/', count=10, min_delay=0, max_delay=0)

    assert calls == ['resolve', 'direct', 'feed']
    assert len(result['posts']) == 1
    assert result['posts'][0]['postId'] == 'urn:li:activity:7462426912188112897'
