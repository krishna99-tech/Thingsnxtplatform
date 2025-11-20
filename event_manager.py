import asyncio
import json
from typing import List, Dict, Any

class EventManager:
    """
    Manages active SSE connections and broadcasts messages.
    This is a simple in-memory implementation. For production with multiple
    workers, a more robust solution like Redis Pub/Sub would be needed.
    """
    def __init__(self):
        self.connections: List[asyncio.Queue] = []

    async def subscribe(self, queue: asyncio.Queue):
        """Adds a new client queue to the list of connections."""
        self.connections.append(queue)

    def unsubscribe(self, queue: asyncio.Queue):
        """Removes a client queue from the list."""
        if queue in self.connections:
            self.connections.remove(queue)

    async def broadcast(self, message: Dict[str, Any]):
        """Sends a message to all connected clients."""
        print(f"Broadcasting global event: {message}")
        # The SSE format is "data: <json_string>\n\n"
        sse_message = f"data: {json.dumps(message)}\n\n"
        for queue in self.connections:
            await queue.put(sse_message)

# Singleton instance to be used across the application
event_manager = EventManager()