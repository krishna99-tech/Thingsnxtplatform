import asyncio
import json
from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from auth_routes import get_current_user
from event_manager import event_manager

router = APIRouter(
    prefix="/events",
    tags=["Events"],
    dependencies=[Depends(get_current_user)] # Protect this whole router
)

@router.get("/stream")
async def event_stream(request: Request):
    """
    Establishes a Server-Sent Events (SSE) connection for global device updates.
    Clients connect here to receive real-time updates on device status, etc.
    """
    # Each client gets its own queue to receive messages.
    queue = asyncio.Queue()
    await event_manager.subscribe(queue)

    async def event_generator():
        try:
            # Send a connection confirmation message
            yield f"data: {json.dumps({'type': 'connected', 'message': 'Global event stream connected'})}\n\n"
            while True:
                # Check if the client has disconnected.
                if await request.is_disconnected():
                    break
                
                # Wait for a message from the event manager.
                message = await queue.get()
                yield message
        finally:
            # Clean up when the connection is closed.
            event_manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no", # Important for Nginx proxies
        },
    )