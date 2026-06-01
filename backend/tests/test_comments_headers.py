from __future__ import annotations

import os

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("LINKEDIN_CLIENT_ID", "test")
os.environ.setdefault("LINKEDIN_CLIENT_SECRET", "test")

from app.linkedin.services.comments import LinkedInCommentsService


def test_comment_fetch_headers_are_minimal_and_do_not_include_content_type():
    service = LinkedInCommentsService(csrf_token='"ajax:test"')

    headers = service.get_commenter_fetch_headers()

    assert set(headers.keys()) == {
        "csrf-token",
        "accept",
        "x-restli-protocol-version",
        "cookie",
    }
    assert headers["csrf-token"] == "ajax:test"
    assert headers["accept"] == "application/vnd.linkedin.normalized+json+2.1"
    assert headers["x-restli-protocol-version"] == "2.0.0"
    assert headers["cookie"] == 'JSESSIONID="ajax:test";'
    assert "content-type" not in headers
    assert service.headers == headers
