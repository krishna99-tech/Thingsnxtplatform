from typing import Dict, List
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            try:
                self.active_connections[user_id].remove(websocket)
            except ValueError:
                pass
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def broadcast(self, user_id: str, message: dict):
        conns = self.active_connections.get(user_id, [])
        for ws in conns[:]:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(user_id, ws)

manager = ConnectionManager()
