"""Per-user SSE queues fed by Kafka UI relay (parallel to global event_manager)."""

import asyncio
import json
from typing import Any, Dict, List

from fastapi import Request
from starlette.responses import StreamingResponse


class KafkaFeedManager:
    def __init__(self) -> None:
        self._by_user: Dict[str, List[asyncio.Queue]] = {}

    async def subscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        self._by_user.setdefault(user_id, []).append(queue)

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        subs = self._by_user.get(user_id)
        if subs and queue in subs:
            subs.remove(queue)
        if subs is not None and len(subs) == 0:
            self._by_user.pop(user_id, None)

    async def broadcast_user(self, user_id: str, message: Dict[str, Any]) -> None:
        sse = f"data: {json.dumps(message)}\n\n"
        for q in self._by_user.get(user_id, []):
            try:
                await q.put(sse)
            except Exception:
                pass

    def subscriber_count_for_user(self, user_id: str) -> int:
        return len(self._by_user.get(user_id, []))


kafka_feed_manager = KafkaFeedManager()


def kafka_live_stream_response(request: Request, user_id: str) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    async def gen():
        await kafka_feed_manager.subscribe(user_id, queue)
        try:
            yield f"data: {json.dumps({'type': 'kafka_connected', 'pipeline': 'iot.telemetry.enriched'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
        finally:
            kafka_feed_manager.unsubscribe(user_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
