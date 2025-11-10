from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import jwt
from db import db
from websocket_manager import manager
import os
from bson import ObjectId

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    user_id = None
    try:
        if not token:
            await websocket.close(code=1008)
            return

        payload = jwt.decode(token, os.getenv("SECRET_KEY"), algorithms=[os.getenv("ALGORITHM", "HS256")])
        if payload.get("type") != "access":
            await websocket.close(code=1008)
            return

        username = payload.get("sub")
        if not username:
            await websocket.close(code=1008)
            return

        user = await db.users.find_one({"username": username})
        if not user:
            await websocket.close(code=1008)
            return

        user_id = str(user["_id"])
        await manager.connect(user_id, websocket)

        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass

    except jwt.ExpiredSignatureError:
        await websocket.close(code=4401)
    except jwt.JWTError:
        await websocket.close(code=4401)
    except WebSocketDisconnect:
        pass
    finally:
        if user_id:
            manager.disconnect(user_id, websocket)
