"""
Web authentication endpoint - provides API key for web interface.

In single-user OSS mode, this endpoint returns the API key for the web interface
to authenticate WebSocket connections.
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cns.api.base import create_success_response


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/auth/key")
async def get_api_key(request: Request):
    """
    Return API key for web interface authentication.

    This is safe in single-user mode where the web interface runs on localhost.
    For multi-user deployments, use proper OAuth/session-based authentication.
    """
    api_key = request.app.state.api_key

    response = create_success_response(
        data={"api_key": api_key},
        meta={"mode": "single_user"}
    )

    return response.to_dict()
