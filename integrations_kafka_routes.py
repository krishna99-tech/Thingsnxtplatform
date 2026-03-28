"""Authenticated HTTP/SSE bridge to Kafka-backed telemetry (clients never connect to brokers directly)."""

import logging

from fastapi import APIRouter, Depends, Request

from auth_routes import get_current_user
from kafka_feed_manager import kafka_live_stream_response
from kafka_service import kafka_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/kafka", tags=["Integrations — Kafka"])


@router.get("/status")
async def kafka_integration_status(user: dict = Depends(get_current_user)):
    """Connection info and counters for mobile/web integrations UI."""
    out = kafka_stats()
    out["viewer_id"] = str(user.get("id", ""))
    return out


@router.get("/live")
async def kafka_live_feed(request: Request, user: dict = Depends(get_current_user)):
    """
    Server-Sent Events stream of enriched telemetry records for the logged-in user.
    React Native can poll /status or use a fetch stream reader; browsers use EventSource.
    """
    uid = str(user.get("id", ""))
    return kafka_live_stream_response(request, uid)
