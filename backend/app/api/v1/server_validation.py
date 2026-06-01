"""
Server validation helpers for API endpoints.

Provides validation functions for server-specific restrictions and capabilities.
"""
import logging
import os
import httpx
import uuid
from fastapi import HTTPException, status, Request
from app.core.edition import get_feature_matrix

logger = logging.getLogger(__name__)

# The official main/default production server URL (hardcoded - same as in client config)
MAIN_SERVER_URL = "https://lg.ainnovate.tech"

# Generate a unique server instance ID when the module loads
# This ID is used to verify if we're calling ourselves
SERVER_INSTANCE_ID = str(uuid.uuid4())

# Cache the result to avoid repeated checks
_is_default_server_cache = None


async def check_if_main_server() -> bool:
    """
    Check if this server instance is the main/default production server.
    
    Does this by making a call to the hardcoded main server URL and comparing
    the instance ID. If we call ourselves, we're the main server.
    
    Returns:
        bool: True if this is the main server, False otherwise
    """
    global _is_default_server_cache
    
    # Return cached result if available
    if _is_default_server_cache is not None:
        return _is_default_server_cache
    
    try:
        logger.info(f"[SERVER_CHECK] Checking if we are the main server by calling {MAIN_SERVER_URL}")
        
        # Make a request to the main server's instance-id endpoint
        # Use shorter timeout and better error handling
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{MAIN_SERVER_URL}/api/v1/server/instance-id",
                headers={"User-Agent": "LinkedInGateway-ServerCheck/1.0"}
            )
            
            if response.status_code == 200:
                data = response.json()
                remote_instance_id = data.get("instance_id")
                
                # If the remote instance ID matches ours, we called ourselves = we're the main server
                is_main = (remote_instance_id == SERVER_INSTANCE_ID)
                
                logger.info(
                    f"[SERVER_CHECK] Remote instance: {remote_instance_id[:8]}..., "
                    f"Our instance: {SERVER_INSTANCE_ID[:8]}..., "
                    f"Match: {is_main}"
                )
                
                _is_default_server_cache = is_main
                return is_main
            else:
                logger.warning(
                    f"[SERVER_CHECK] Got status {response.status_code} from main server check"
                )
                # If we can't reach the main server or get an error, we're likely a custom server
                _is_default_server_cache = False
                return False
                
    except httpx.TimeoutException:
        logger.info(f"[SERVER_CHECK] Timeout checking main server - assuming custom server")
        _is_default_server_cache = False
        return False
    except httpx.ConnectError as e:
        logger.info(f"[SERVER_CHECK] Cannot connect to main server (network error) - assuming custom server")
        _is_default_server_cache = False
        return False
    except Exception as e:
        logger.info(f"[SERVER_CHECK] Error checking main server: {type(e).__name__} - assuming custom server")
        # If we can't make the call, assume we're a custom server (safer default)
        _is_default_server_cache = False
        return False


async def validate_server_call_permission(server_call: bool) -> None:
    """
    Validate the requested execution mode.

    In server-only deployments, server-side execution is the normal path, so
    this helper now accepts both values and only logs the request for observability.
    Legacy proxy/browser mode may still exist in compatibility paths, but it is no
    longer blocked at the server boundary.
    """
    feature_matrix = get_feature_matrix()
    is_main = await check_if_main_server()

    logger.info(
        "[SERVER_VALIDATION] server_call=%s validated successfully (is_main=%s, allows_server_execution=%s)",
        server_call,
        is_main,
        feature_matrix.allows_server_execution,
    )


async def get_server_info() -> dict:
    """
    Get current server information.
    
    Returns:
        dict: Server information including type and capabilities
    """
    is_main = await check_if_main_server()
    
    return {
        "is_default_server": is_main,
        "server_call_allowed": True,
        "execution_mode": "server_only",
        "instance_id": SERVER_INSTANCE_ID
    }

