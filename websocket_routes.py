from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException
from jose import jwt, JWTError
from db import db
from websocket_manager import manager
import os
import asyncio
import json
import logging
from typing import Optional
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

# WebSocket connection timeout (e.g., 45 seconds). Client should send a ping every 30s.
WS_TIMEOUT = 45


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.
    Requires authentication token in query params.
    Supports ping/pong for connection health checks.
    """
    token = websocket.query_params.get("token")
    user_id: Optional[str] = None
    
    try:
        # Validate token
        if not token:
            logger.warning("WebSocket connection attempt without token")
            await websocket.close(code=1008, reason="Missing authentication token")
            return

        # Decode and validate JWT
        try:
            payload = jwt.decode(
                token,
                os.getenv("SECRET_KEY"),
                algorithms=[os.getenv("ALGORITHM", "HS256")]
            )
        except jwt.ExpiredSignatureError:
            logger.warning("WebSocket connection with expired token")
            await websocket.close(code=4401, reason="Token expired")
            return
        except JWTError as e:
            logger.warning(f"WebSocket connection with invalid token: {e}")
            await websocket.close(code=4401, reason="Invalid token")
            return

        # Validate token type
        if payload.get("type") != "access":
            logger.warning("WebSocket connection with non-access token")
            await websocket.close(code=1008, reason="Invalid token type")
            return

        # Get user identifier
        username = payload.get("sub")
        if not username:
            logger.warning("WebSocket connection with token missing 'sub' claim")
            await websocket.close(code=1008, reason="Invalid token payload")
            return

        # Verify user exists
        user = await db.users.find_one({"$or": [{"username": username}, {"email": username}]})
        if not user:
            logger.warning(f"WebSocket connection for non-existent user: {username}")
            await websocket.close(code=1008, reason="User not found")
            return

        # Check if user is active
        if not user.get("is_active", True):
            logger.warning(f"WebSocket connection for inactive user: {username}")
            await websocket.close(code=1008, reason="User account inactive")
            return

        user_id = str(user["_id"])
        
        # Accept connection and register with manager
        await manager.connect(user_id, websocket)
        logger.info(f"WebSocket connected for user {user_id} ({username})")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connection established",
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Main message loop
        while True:
            try:
                # Wait for a message with a timeout.
                # If the client doesn't send a ping within this time, it will raise TimeoutError.
                msg = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WS_TIMEOUT
                )
                
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    logger.debug(f"User {user_id} received message type: {msg_type}")
                    
                    # Handle ping/pong for connection health
                    if msg_type == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    elif msg_type == "subscribe":
                        # Optional: Handle subscription requests
                        await websocket.send_json({
                            "type": "subscribed",
                            "channels": data.get("channels", []),
                        })
                    else:
                        logger.debug(f"Received unknown message type: {msg_type}")
                        
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from user {user_id}: {msg}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Invalid JSON format",
                    })
                    
            except asyncio.TimeoutError:
                logger.warning(f"WebSocket timeout for user {user_id}. Closing connection.")
                manager.disconnect(user_id, websocket)
                break
            except WebSocketDisconnect as e:
                logger.info(f"WebSocket disconnected for user {user_id} (code: {e.code}, reason: {e.reason})")
                manager.disconnect(user_id, websocket)
                break
            except Exception as e: # Catch other potential errors
                logger.error(f"Error processing WebSocket message for user {user_id}: {e}")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Internal server error",
                    })
                except:
                    pass
                manager.disconnect(user_id, websocket)
                break

    except WebSocketDisconnect:
        # This can catch disconnections that happen before the main loop
        logger.info(f"WebSocket disconnected for user {user_id} during setup.")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass
    finally:
        # Clean up connection
        if user_id:
            logger.info(f"WebSocket connection cleaned up for user {user_id}")
