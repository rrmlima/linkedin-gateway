"""
API endpoints for handling post reactions.

This endpoint supports two execution modes:
1. server_call=True: Execute LinkedIn API call directly from backend
2. server_call=False (default): Execute via browser extension as transparent HTTP proxy
"""

import logging
import json
import random
import asyncio
import re
from typing import List, Dict, Any
from urllib.parse import quote
from uuid import uuid4, UUID

import httpx

from fastapi import APIRouter, Depends, HTTPException, Body, status, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.db.models.user import User
from app.schemas.post import (
    GetReactionsRequest,
    ReactorDetail,
    GetReactionsResponse
)
from app.ws.events import WebSocketEventHandler
from app.db.dependencies import get_db
from app.api.dependencies import get_ws_handler
from app.auth.dependencies import validate_api_key_from_header_or_body
# Import LinkedIn services
from app.linkedin.services.reactions import LinkedInReactionsService
from app.linkedin.helpers import get_linkedin_service, proxy_http_request
from app.core.linkedin_rate_limit import apply_pagination_delay
from app.linkedin.utils.my_profile_id import get_my_profile_id_with_fallbacks
from app.linkedin.utils.parsers import parse_linkedin_post_url
from app.schemas.post import LikeCommentRequest, LikePostRequest, LikeResponse

# Configure logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

router = APIRouter()


class DebugProxyHttpRequest(BaseModel):
    url: str
    method: str = "GET"
    body: str | None = None
    headers: dict[str, str] | None = None
    api_key: str | None = None


_COMMENT_COMPONENT_RE = re.compile(r"^urn:li:(?:comment|fsd_comment):\(([^,]+),([^\)]+)\)$")
_DASH_REACTIONS_QUERY_ID = "voyagerSocialDashReactions.b731222600772fd42464c0fe19bd722b"
_DASH_REACTIONS_URL = (
    "https://www.linkedin.com/voyager/api/graphql"
    f"?action=execute&queryId={_DASH_REACTIONS_QUERY_ID}"
)


def _extract_comment_components(comment_urn: str) -> tuple[str, str] | None:
    normalized = comment_urn.strip()
    match = _COMMENT_COMPONENT_RE.search(normalized)
    if not match:
        return None
    first, second = match.group(1).strip(), match.group(2).strip()
    if first.startswith("urn:li:") and not second.startswith("urn:li:"):
        return first, second
    if second.startswith("urn:li:") and not first.startswith("urn:li:"):
        return second, first
    return None


def _normalize_comment_reaction_urn(comment_urn: str) -> str:
    components = _extract_comment_components(comment_urn)
    if not components:
        return comment_urn.strip()
    thread_urn, comment_id = components
    return f"urn:li:comment:({thread_urn},{comment_id})"


def _extract_comment_object_urn(comment_urn: str) -> str:
    components = _extract_comment_components(comment_urn)
    if components:
        return components[0]
    return comment_urn.strip()


def _comment_graphql_thread_urn(comment_urn: str) -> str:
    normalized = _normalize_comment_reaction_urn(comment_urn)
    return normalized.replace("urn:li:activity:", "activity:")


def _graphql_like_payload(*, target_urn: str, object_urn: str) -> dict[str, Any]:
    thread_urn = target_urn if target_urn == object_urn else _comment_graphql_thread_urn(target_urn)
    return {
        "variables": {
            "entity": {
                "reactionType": "LIKE",
            },
            "threadUrn": thread_urn,
        },
        "queryId": _DASH_REACTIONS_QUERY_ID,
        "includeWebMetadata": True,
    }


def _normalize_like_result(*, status_code: int, body_text: str, target_urn: str, object_urn: str, mode: str) -> LikeResponse:
    body_lower = (body_text or "").lower()
    already_liked = status_code == 409 or "already" in body_lower or "duplicate" in body_lower
    return LikeResponse(
        success=status_code < 400 or already_liked,
        already_liked=already_liked,
        target_urn=target_urn,
        object_urn=object_urn,
        mode=mode,
        status_code=status_code,
    )


async def _create_like(
    *,
    db: AsyncSession,
    api_key,
    ws_handler: WebSocketEventHandler | None,
    target_urn: str,
    object_urn: str,
    server_call: bool,
) -> LikeResponse:
    reactions_service = await get_linkedin_service(db, api_key, LinkedInReactionsService)
    profile_id = await get_my_profile_id_with_fallbacks(
        db=db,
        user_id=api_key.user_id,
        service=reactions_service,
        ws_handler=ws_handler,
        use_proxy=not server_call,
    )
    actor_urn = f"urn:li:person:{profile_id}"
    encoded_actor = quote(actor_urn, safe="")
    official_payload = {"root": target_urn, "reactionType": "LIKE"}
    graphql_payload = _graphql_like_payload(target_urn=target_urn, object_urn=object_urn)
    headers = {
        **reactions_service.headers,
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "content-type": "application/json; charset=UTF-8",
        "Linkedin-Version": "202603",
    }
    if not any(key.lower() == "x-restli-protocol-version" for key in headers):
        headers["x-restli-protocol-version"] = "2.0.0"

    if server_call:
        # Server-side write path should mirror the browser contract first.
        # LinkedIn Web uses the Voyager GraphQL reaction mutation captured
        # from the working browser request, so we try that before the REST
        # fallback. This keeps server-side aligned with the documented web
        # session model while preserving compatibility if LinkedIn changes.
        candidates = [
            (_DASH_REACTIONS_URL, graphql_payload),
            (f"https://api.linkedin.com/rest/reactions?actor={encoded_actor}", official_payload),
        ]
        last_result: LikeResponse | None = None
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for like_url, payload in candidates:
                response = await client.post(like_url, headers=headers, json=payload)
                body_text = response.text
                last_result = _normalize_like_result(
                    status_code=response.status_code,
                    body_text=body_text,
                    target_urn=target_urn,
                    object_urn=object_urn,
                    mode="server",
                )
                logger.info(
                    "[LIKE][SERVER] Candidate %s returned status %s body=%s",
                    like_url.split("?", 1)[0],
                    last_result.status_code,
                    body_text[:500],
                )
                if last_result.success:
                    return last_result

        return last_result or LikeResponse(
            success=False,
            already_liked=False,
            target_urn=target_urn,
            object_urn=object_urn,
            mode="server",
            status_code=0,
        )

    if ws_handler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WebSocket service not available",
        )

    # The browser-extension path is authenticated with the signed-in linkedin.com
    # session, so follow the exact GraphQL contract captured from LinkedIn Web.
    # Keep the documented REST endpoint only as a diagnostic fallback.
    candidates = [
        (_DASH_REACTIONS_URL, graphql_payload),
        (f"https://www.linkedin.com/rest/reactions?actor={encoded_actor}", official_payload),
    ]
    last_result: LikeResponse | None = None
    for like_url, payload in candidates:
        proxy_response = await proxy_http_request(
            ws_handler=ws_handler,
            user_id=str(api_key.user_id),
            url=like_url,
            method="POST",
            headers=headers,
            body=json.dumps(payload, ensure_ascii=False),
            response_type="json",
            include_credentials=True,
            timeout=60.0,
            instance_id=api_key.instance_id,
        )
        body_text = proxy_response.get("body")
        if isinstance(body_text, (dict, list)):
            body_text = json.dumps(body_text, ensure_ascii=False)
        last_result = _normalize_like_result(
            status_code=int(proxy_response.get("status_code", 0)),
            body_text=str(body_text or ""),
            target_urn=target_urn,
            object_urn=object_urn,
            mode="proxy",
        )
        logger.info(
            "[LIKE] Candidate %s returned status %s body=%s",
            like_url.split("?", 1)[0],
            last_result.status_code,
            str(body_text or "")[:500],
        )
        if last_result.success:
            return last_result

    return last_result or LikeResponse(
        success=False,
        already_liked=False,
        target_urn=target_urn,
        object_urn=object_urn,
        mode="proxy",
        status_code=0,
    )

@router.post("/posts/get-reactions", response_model=GetReactionsResponse, tags=["reactions"])
async def get_post_reactions(
    request_body: GetReactionsRequest = Body(...),
    ws_handler: WebSocketEventHandler = Depends(get_ws_handler),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", include_in_schema=False)
):
    """
    Fetch reactions for a specific LinkedIn post using API Key auth.
    
    Supports two execution modes:
    1. server_call=True: Direct server-side LinkedIn API call
    2. server_call=False (default): Transparent HTTP proxy via browser extension
    
    Authentication: Provide API key via X-API-Key header OR in request body
    
    Args:
        request_body: Request containing post_url, pagination params, and execution mode
        ws_handler: WebSocket handler for proxy mode
        db: Database session
        
    Returns:
        GetReactionsResponse with list of reactor details
        
    Raises:
        HTTPException 401: If API key is invalid
        HTTPException 404: If user is not connected via WebSocket (proxy mode)
        HTTPException 408: If the client does not respond within timeout (proxy mode)
        HTTPException 500: If server-side execution fails
        HTTPException 502: If proxy returns an error
        HTTPException 503: If WebSocket service is unavailable (proxy mode)
    """
    logger.info(f"[REACTIONS] Received request for post: {request_body.post_url}")

    # --- Validate API Key from Header or Body --- 
    try:
        api_key = await validate_api_key_from_header_or_body(
            api_key_from_body=request_body.api_key,
            api_key_header=x_api_key,
            db=db
        )
        logger.info(f"[REACTIONS] API Key validated for user ID: {api_key.user_id}")
    except HTTPException as auth_exc:
        raise auth_exc
    except Exception as e:
        logger.exception(f"[REACTIONS] Unexpected error during API key validation: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error during authentication")

    user_id_str = str(api_key.user_id)

    # Check WebSocket connection if using proxy mode
    if not request_body.server_call:
        if not ws_handler:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WebSocket service not available"
            )
        
        # Check if user has any active WebSocket connections
        if not ws_handler.connection_manager.is_instance_connected(api_key.instance_id):
            logger.warning(f"[REACTIONS] Instance {api_key.instance_id} not connected via WebSocket")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Browser instance not connected. Please check your extension.")
    
    # --- UNIFIED PAGINATION LOGIC ---
    mode = "SERVER_CALL" if request_body.server_call else "PROXY"
    logger.info(f"[REACTIONS][{mode}] Executing for user {user_id_str}")
    logger.info(f"[REACTIONS][{mode}] Parameters - post_url: {request_body.post_url}, count: {request_body.count}")
    
    try:
        # Get service to build URLs and parse responses (uses CSRF/cookies from api_key object)
        reactions_service = await get_linkedin_service(db, api_key, LinkedInReactionsService)
        
        # Simple pagination logic: Fetch batches of 10 until we get an empty response
        all_reactors = []
        start_index = 0
        pagination_token = None
        max_count = request_body.count
        fetch_all = (max_count == -1)
        batch_size = 10  # Always use 10
        actual_post_url = request_body.post_url
        
        logger.info(f"[REACTIONS][{mode}] Starting pagination: max_count={max_count}, batch_size={batch_size}")
        
        while True:
            # Check if we've reached max_count limit (if not fetching all)
            if not fetch_all:
                remaining = max_count - len(all_reactors)
                if remaining <= 0:
                    logger.info(f"[REACTIONS][{mode}] Reached max_count limit of {max_count}")
                    break
                batch_size = min(10, remaining)
            
            logger.info(f"[REACTIONS][{mode}] Fetching batch {len(all_reactors)//10 + 1}: start={start_index}, count={batch_size}, has_token={pagination_token is not None}")
            
            # Build the exact LinkedIn URL for this batch (await since it may convert activity to ugcPost)
            url = await reactions_service._build_reactions_url(
                post_url=actual_post_url,
                start=start_index,
                count=batch_size,
                pagination_token=pagination_token
            )
            
            # --- EXECUTE REQUEST (proxy or direct) ---
            if request_body.server_call:
                # Direct server-side call
                raw_json_data = await reactions_service._make_request(url)
            else:
                # Proxy via browser extension (route to specific instance)
                proxy_response = await proxy_http_request(
                    ws_handler=ws_handler,
                    user_id=user_id_str,
                    url=url,
                    method="GET",
                    headers=reactions_service.headers,
                    body=None,
                    response_type="json",
                    include_credentials=True,
                    timeout=60.0,
                    instance_id=api_key.instance_id  # Route to specific instance
                )
                
                logger.info(f"[REACTIONS][{mode}] Received response with status {proxy_response['status_code']}")
                
                # Check for HTTP errors
                if proxy_response['status_code'] >= 400:
                    error_msg = f"LinkedIn API returned status {proxy_response['status_code']}"
                    logger.error(f"[REACTIONS][{mode}] {error_msg}")
                    # If we have some results, return them; otherwise raise error
                    if all_reactors:
                        logger.warning(f"[REACTIONS][{mode}] Returning {len(all_reactors)} reactors collected before error")
                        break
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=error_msg
                        )
                
                # Parse the raw JSON body from proxy response
                raw_json_data = json.loads(proxy_response['body'])
            
            # --- PARSE RESPONSE (unified) ---
            reactors, pagination_token, total_reactions = reactions_service._parse_reactions_response(raw_json_data)
            
            if not reactors:
                logger.info(f"[REACTIONS][{mode}] Empty batch received, stopping pagination")
                break
            
            logger.info(f"[REACTIONS][{mode}] Batch contained {len(reactors)} reactors")
            all_reactors.extend(reactors)
            
            # Check if we've fetched all available reactions
            if not pagination_token:
                logger.info(f"[REACTIONS][{mode}] No more pagination token, finished fetching")
                break
            
            # Increment start index for next batch
            start_index += batch_size
            
            # Add configurable delay between requests
            await apply_pagination_delay(
                min_delay=request_body.min_delay,
                max_delay=request_body.max_delay,
                operation_name=f"REACTIONS-{mode}"
            )
        
        logger.info(f"[REACTIONS][{mode}] Successfully fetched total of {len(all_reactors)} reactors")
        
        # Convert to response models
        reactor_models = [ReactorDetail(**reactor) for reactor in all_reactors]
        
        return GetReactionsResponse(data=reactor_models)
        
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"[REACTIONS][{mode}] Validation error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"[REACTIONS][{mode}] Unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch reactions: {str(e)}"
        )


@router.post("/posts/_debug-proxy-http", tags=["reactions"])
async def debug_proxy_http(
    request_body: DebugProxyHttpRequest = Body(...),
    ws_handler: WebSocketEventHandler = Depends(get_ws_handler),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", include_in_schema=False)
):
    """Temporary authenticated LinkedIn-only proxy probe for reaction debugging."""
    if not request_body.url.startswith("https://www.linkedin.com/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only www.linkedin.com URLs are allowed")
    api_key = await validate_api_key_from_header_or_body(
        api_key_from_body=request_body.api_key,
        api_key_header=x_api_key,
        db=db,
    )
    proxy_response = await proxy_http_request(
        ws_handler=ws_handler,
        user_id=str(api_key.user_id),
        url=request_body.url,
        method=request_body.method.upper(),
        headers=request_body.headers or {},
        body=request_body.body,
        response_type="text",
        include_credentials=True,
        timeout=60.0,
        instance_id=api_key.instance_id,
    )
    body = proxy_response.get("body") or ""
    return {"status_code": proxy_response.get("status_code"), "body": body[:200000]}


@router.post("/posts/like-post", response_model=LikeResponse, tags=["reactions"])
async def like_post(
    request_body: LikePostRequest = Body(...),
    ws_handler: WebSocketEventHandler = Depends(get_ws_handler),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", include_in_schema=False)
):
    """Like a LinkedIn post after an approval click."""
    api_key = await validate_api_key_from_header_or_body(
        api_key_from_body=request_body.api_key,
        api_key_header=x_api_key,
        db=db,
    )
    target_urn = parse_linkedin_post_url(request_body.post_url)
    if not target_urn:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not parse post URN from post_url")
    return await _create_like(
        db=db,
        api_key=api_key,
        ws_handler=ws_handler,
        target_urn=target_urn,
        object_urn=target_urn,
        server_call=request_body.server_call,
    )


@router.post("/posts/like-comment", response_model=LikeResponse, tags=["reactions"])
async def like_comment(
    request_body: LikeCommentRequest = Body(...),
    ws_handler: WebSocketEventHandler = Depends(get_ws_handler),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", include_in_schema=False)
):
    """Like a LinkedIn comment after replying to it."""
    api_key = await validate_api_key_from_header_or_body(
        api_key_from_body=request_body.api_key,
        api_key_header=x_api_key,
        db=db,
    )
    target_urn = _normalize_comment_reaction_urn(request_body.comment_urn)
    object_urn = _extract_comment_object_urn(target_urn)
    return await _create_like(
        db=db,
        api_key=api_key,
        ws_handler=ws_handler,
        target_urn=target_urn,
        object_urn=object_urn,
        server_call=request_body.server_call,
    )

