from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.v1 import server_validation
from app.schemas.connection import GetConnectionsRequest
from app.schemas.post import (
    GetCommentersRequest,
    LikeCommentRequest,
    LikePostRequest,
    PostCommentRequest,
    ReplyToCommentRequest,
)
from app.schemas.profile import ScrapeProfileRequest


def test_server_call_defaults_are_server_side():
    assert GetCommentersRequest(post_url="https://www.linkedin.com/feed/update/urn:li:activity:1").server_call is True
    assert PostCommentRequest(post_url="https://www.linkedin.com/feed/update/urn:li:activity:1", comment_text="hi").server_call is True
    assert ReplyToCommentRequest(comment_urn="urn:li:fsd_comment:(1,urn:li:activity:1)", reply_text="ok").server_call is True
    assert LikePostRequest(post_url="https://www.linkedin.com/feed/update/urn:li:activity:1").server_call is True
    assert LikeCommentRequest(comment_urn="urn:li:fsd_comment:(1,urn:li:activity:1)").server_call is True
    assert GetConnectionsRequest().server_call is True
    assert ScrapeProfileRequest(profile_id="https://www.linkedin.com/in/example", server_call=True).server_call is True


async def _validate_server_only_mode() -> dict:
    original_get_feature_matrix = server_validation.get_feature_matrix
    original_check_if_main_server = server_validation.check_if_main_server

    class _FeatureMatrix:
        allows_server_execution = True

    try:
        server_validation.get_feature_matrix = lambda: _FeatureMatrix()  # type: ignore[assignment]
        server_validation.check_if_main_server = lambda: asyncio.sleep(0, result=True)  # type: ignore[assignment]
        await server_validation.validate_server_call_permission(True)
        return await server_validation.get_server_info()
    finally:
        server_validation.get_feature_matrix = original_get_feature_matrix  # type: ignore[assignment]
        server_validation.check_if_main_server = original_check_if_main_server  # type: ignore[assignment]


def test_server_validation_reports_server_only():
    info = asyncio.run(_validate_server_only_mode())
    assert info["server_call_allowed"] is True
    assert info["execution_mode"] == "server_only"
