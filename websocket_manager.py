from typing import Dict, List, Set
from fastapi import WebSocket
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.
    Supports multiple connections per user and automatic cleanup of dead connections.
    """
    
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.connection_timestamps: Dict[str, Dict[WebSocket, datetime]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        """
        Accept and register a new WebSocket connection.
        
        Args:
            user_id: The user ID associated with this connection
            websocket: The WebSocket connection to register
        """
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
            self.connection_timestamps[user_id] = {}
        
        self.active_connections[user_id].append(websocket)
        self.connection_timestamps[user_id][websocket] = datetime.utcnow()
        
        logger.debug(f"Connection registered for user {user_id}. Total connections: {len(self.active_connections[user_id])}")

    def disconnect(self, user_id: str, websocket: WebSocket):
        """
        Remove a WebSocket connection.
        
        Args:
            user_id: The user ID associated with the connection
            websocket: The WebSocket connection to remove
        """
        if user_id in self.active_connections:
            try:
                self.active_connections[user_id].remove(websocket)
                if websocket in self.connection_timestamps.get(user_id, {}):
                    del self.connection_timestamps[user_id][websocket]
            except (ValueError, KeyError):
                pass
            
            # Clean up empty user entries
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
                if user_id in self.connection_timestamps:
                    del self.connection_timestamps[user_id]
                logger.debug(f"All connections removed for user {user_id}")

    async def broadcast(self, user_id: str, message: dict, exclude_websocket: WebSocket = None):
        """
        Broadcast a message to all connections for a specific user.
        
        Args:
            user_id: The user ID to broadcast to
            message: The message dictionary to send
            exclude_websocket: Optional WebSocket to exclude from broadcast
        """
        conns = self.active_connections.get(user_id, [])
        if not conns:
            logger.debug(f"No active connections for user {user_id}")
            return
        
        # Create a copy of the list to iterate over, allowing safe modification of the original list
        connections_to_broadcast = conns[:]
        dead_connections = []
        
        for ws in connections_to_broadcast:
            # Skip excluded websocket
            if exclude_websocket and ws == exclude_websocket:
                continue
                
            try:
                await ws.send_json(message)
            except Exception: # Catches various connection errors (e.g., ConnectionClosed, RuntimeError)
                # If sending fails, the connection is considered dead.
                dead_connections.append(ws)
        
        # Clean up dead connections
        for ws in dead_connections:
            logger.warning(f"Found dead connection for user {user_id}. Cleaning up.")
            self.disconnect(user_id, ws)

    async def broadcast_to_all(self, message: dict):
        """
        Broadcast a message to all connected users.
        
        Args:
            message: The message dictionary to send
        """
        all_user_ids = list(self.active_connections.keys())
        for user_id in all_user_ids:
            await self.broadcast(user_id, message)

    def get_connection_count(self, user_id: str = None) -> int:
        """
        Get the number of active connections.
        
        Args:
            user_id: Optional user ID to get count for specific user
            
        Returns:
            Number of active connections
        """
        if user_id:
            return len(self.active_connections.get(user_id, []))
        return sum(len(conns) for conns in self.active_connections.values())

    def get_connected_users(self) -> Set[str]:
        """
        Get set of all user IDs with active connections.
        
        Returns:
            Set of user IDs
        """
        return set(self.active_connections.keys())


# Global connection manager instance
manager = ConnectionManager()
