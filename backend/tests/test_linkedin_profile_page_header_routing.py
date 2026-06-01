import pytest

from app.linkedin.services.base import LinkedInServiceBase
from app.linkedin.services.connections import LinkedInConnectionService
from app.linkedin.services.profile import LinkedInProfileService
from app.linkedin.services.profile_about_skills import LinkedInProfileAboutSkillsService
from app.linkedin.services.profile_contact import LinkedInProfileContactService
from app.linkedin.services.profile_identity import LinkedInProfileIdentityService
from app.linkedin.services import profile as profile_module
from app.linkedin.services import connections as connections_module
from app.linkedin.services import profile_about_skills as about_skills_module
from app.linkedin.services import profile_contact as contact_module
from app.linkedin.services import profile_identity as identity_module
from app.linkedin.services import user_comments as user_comments_module


@pytest.mark.asyncio
async def test_base_profile_page_headers_mimic_browser_navigation() -> None:
    service = LinkedInServiceBase(
        csrf_token='"ajax:test"',
        linkedin_cookies={'JSESSIONID': '"ajax:test"', 'li_at': 'fake-token'},
    )

    headers = service.get_profile_page_headers()

    assert headers['accept'].startswith('text/html,application/xhtml+xml')
    assert headers['sec-fetch-dest'] == 'document'
    assert headers['sec-fetch-mode'] == 'navigate'
    assert headers['sec-fetch-site'] == 'none'
    assert headers['sec-fetch-user'] == '?1'
    assert headers['upgrade-insecure-requests'] == '1'
    assert 'csrf-token' not in headers
    assert 'x-restli-protocol-version' not in headers
    assert 'cookie' in headers and 'JSESSIONID' in headers['cookie']


@pytest.mark.asyncio
async def test_connection_requests_use_page_headers_for_profile_resolution_and_api_headers_for_write(monkeypatch) -> None:
    captured = {}

    async def fake_extract(profile_input, headers, timeout):
        captured['extract_headers'] = headers
        captured['extract_input'] = profile_input
        captured['extract_timeout'] = timeout
        return 'abc123'

    async def fake_make_request(self, url, method='GET', timeout=None, headers=None, debug_endpoint_type='unknown', **kwargs):
        captured['request_url'] = url
        captured['request_method'] = method
        captured['request_headers'] = headers
        captured['request_kwargs'] = kwargs
        return {'ok': True}

    monkeypatch.setattr(connections_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(LinkedInConnectionService, '_make_request', fake_make_request)

    service = LinkedInConnectionService(csrf_token='"ajax:test"')
    await service.send_simple_connection_request('https://www.linkedin.com/in/example/')

    assert captured['extract_headers'] == service.get_profile_page_headers()
    assert captured['request_method'] == 'POST'
    assert captured['request_headers']['csrf-token'] == 'ajax:test'
    assert captured['request_headers']['Content-Type'] == 'application/json'
    assert captured['request_kwargs']['json']['invitee']['inviteeUnion']['memberProfile'] == 'urn:li:fsd_profile:abc123'

    captured.clear()
    await service.send_connection_request_with_message('https://www.linkedin.com/in/example/', 'Olá!')

    assert captured['extract_headers'] == service.get_profile_page_headers()
    assert captured['request_method'] == 'POST'
    assert captured['request_headers']['csrf-token'] == 'ajax:test'
    assert captured['request_headers']['Content-Type'] == 'application/json'
    assert captured['request_kwargs']['json']['customMessage'] == 'Olá!'


@pytest.mark.asyncio
async def test_profile_html_fetch_uses_page_headers(monkeypatch) -> None:
    captured = {}

    class _FakeResponse:
        status_code = 200
        text = '<html></html>'

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured['client_kwargs'] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, cookies=None):
            captured['url'] = url
            captured['headers'] = headers
            captured['cookies'] = cookies
            return _FakeResponse()

    monkeypatch.setattr(profile_module.httpx, 'AsyncClient', _FakeClient)

    service = LinkedInProfileService(csrf_token='"ajax:test"')
    result = await service.get_headline_location_and_degree('example-vanity')

    assert result['headline'] == 'N/A'
    assert captured['url'] == 'https://www.linkedin.com/in/example-vanity/'
    assert captured['headers'] == service.get_profile_page_headers()
    assert captured['client_kwargs']['follow_redirects'] is True


@pytest.mark.asyncio
async def test_profile_scrape_experiences_and_recommendations_use_page_headers_for_resolution(monkeypatch) -> None:
    service = LinkedInProfileService(csrf_token='"ajax:test"')
    seen = {'experience': None, 'recommendations': None, 'headers': None}

    async def fake_extract(profile_input, headers, timeout):
        seen['headers'] = headers
        return 'abc123'

    async def fake_get_profile_experiences(self, profile_id):
        seen['experience'] = profile_id
        return []

    async def fake_get_profile_cards(self, profile_id):
        seen['recommendations'] = profile_id
        return []

    monkeypatch.setattr(profile_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(LinkedInProfileService, 'get_profile_experiences', fake_get_profile_experiences)
    monkeypatch.setattr(LinkedInProfileService, 'get_profile_cards', fake_get_profile_cards)

    experiences = await service.scrape_profile_experiences('https://www.linkedin.com/in/example/')
    assert experiences == []
    assert seen['headers'] == service.get_profile_page_headers()
    assert seen['experience'] == 'abc123'

    seen['headers'] = None
    recommendations = await service.scrape_profile_recommendations('https://www.linkedin.com/in/example/')
    assert recommendations == []
    assert seen['headers'] == service.get_profile_page_headers()
    assert seen['recommendations'] == 'abc123'


@pytest.mark.asyncio
async def test_profile_identity_uses_page_headers_for_resolution_and_html_fetch(monkeypatch) -> None:
    service = LinkedInProfileIdentityService(csrf_token='"ajax:test"')
    captured = {}

    async def fake_extract(profile_input, headers, timeout):
        captured['extract_headers'] = headers
        return 'abc123'

    async def fake_get_profile_identity_cards(self, profile_id):
        captured['identity_profile_id'] = profile_id
        return {
            'vanity_name': 'example-vanity',
            'first_name': 'Ana',
            'last_name': 'Silva',
            'follower_count': 42,
        }

    class _FakeResponse:
        status_code = 200
        text = '<html></html>'

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured['client_kwargs'] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, cookies=None):
            captured['html_url'] = url
            captured['html_headers'] = headers
            captured['html_cookies'] = cookies
            return _FakeResponse()

    monkeypatch.setattr(identity_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(LinkedInProfileIdentityService, 'get_profile_identity_cards', fake_get_profile_identity_cards)
    monkeypatch.setattr(identity_module.httpx, 'AsyncClient', _FakeClient)

    result = await service.scrape_profile_identity('https://www.linkedin.com/in/example/')

    assert result['linkedin_id'] == 'abc123'
    assert captured['extract_headers'] == service.get_profile_page_headers()
    assert captured['identity_profile_id'] == 'abc123'
    assert captured['html_url'] == 'https://www.linkedin.com/in/example-vanity/'
    assert captured['html_headers'] == service.get_profile_page_headers()
    assert captured['client_kwargs']['follow_redirects'] is True


@pytest.mark.asyncio
async def test_profile_contact_uses_page_headers_for_resolution(monkeypatch) -> None:
    service = LinkedInProfileContactService(csrf_token='"ajax:test"')
    captured = {}

    async def fake_extract(profile_input, headers, timeout):
        captured['extract_headers'] = headers
        return 'abc123'

    class _FakeIdentityService:
        def __init__(self, csrf_token, linkedin_cookies=None):
            self.csrf_token = csrf_token
            self.linkedin_cookies = linkedin_cookies or {}

        async def get_profile_identity_cards(self, profile_id):
            captured['identity_profile_id'] = profile_id
            return {'vanity_name': 'example-vanity'}

    async def fake_get_contact_info(self, member_identity):
        captured['member_identity'] = member_identity
        return {
            'email': 'N/A',
            'phone': 'N/A',
            'website': 'N/A',
            'birthday': 'N/A',
            'connected_date': 'N/A',
        }

    monkeypatch.setattr(contact_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(identity_module, 'LinkedInProfileIdentityService', _FakeIdentityService)
    monkeypatch.setattr(LinkedInProfileContactService, 'get_contact_info', fake_get_contact_info)

    result = await service.scrape_profile_contact('https://www.linkedin.com/in/example/')

    assert result['email'] == 'N/A'
    assert captured['extract_headers'] == service.get_profile_page_headers()
    assert captured['identity_profile_id'] == 'abc123'
    assert captured['member_identity'] == 'example-vanity'


@pytest.mark.asyncio
async def test_profile_about_skills_uses_page_headers_for_resolution(monkeypatch) -> None:
    service = LinkedInProfileAboutSkillsService(csrf_token='"ajax:test"')
    captured = {}

    async def fake_extract(profile_input, headers, timeout):
        captured['headers'] = headers
        return 'abc123'

    async def fake_get_about_and_skills(self, profile_id):
        captured['profile_id'] = profile_id
        return {'about': 'about text', 'top_skills': ['Python'], 'languages': []}

    monkeypatch.setattr(about_skills_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(LinkedInProfileAboutSkillsService, 'get_about_and_skills', fake_get_about_and_skills)

    result = await service.scrape_profile_about_skills('https://www.linkedin.com/in/example/')

    assert result == {'about': 'about text', 'skills': ['Python'], 'languages': []}
    assert captured['headers'] == service.get_profile_page_headers()
    assert captured['profile_id'] == 'abc123'


@pytest.mark.asyncio
async def test_user_comments_uses_page_headers_for_resolution(monkeypatch) -> None:
    service = user_comments_module.LinkedInUserCommentsService(csrf_token='"ajax:test"')
    captured = {}

    async def fake_extract(profile_input, headers, timeout):
        captured['headers'] = headers
        return 'abc123'

    async def fake_make_request(self, url, method='GET', timeout=None, headers=None, debug_endpoint_type='unknown', **kwargs):
        captured['request_url'] = url
        return {'data': {}}

    def fake_parse(self, data):
        return [], None, None

    async def fake_augment(self, comments):
        captured['augmented'] = list(comments)
        return None

    monkeypatch.setattr(user_comments_module, 'extract_profile_id', fake_extract)
    monkeypatch.setattr(user_comments_module.LinkedInUserCommentsService, '_make_request', fake_make_request)
    monkeypatch.setattr(user_comments_module.LinkedInUserCommentsService, '_parse_user_comments_response', fake_parse)
    monkeypatch.setattr(user_comments_module.LinkedInUserCommentsService, '_augment_parent_comment_details', fake_augment)

    result = await service.fetch_user_comments('https://www.linkedin.com/in/example/')

    assert result == []
    assert captured['headers'] == service.get_profile_page_headers()
    assert 'queryId=voyagerFeedDashProfileUpdates' in captured['request_url']
    assert captured['augmented'] == []
