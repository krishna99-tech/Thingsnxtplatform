from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth_routes import router as auth_router
from device_routes import router as device_router
from websocket_routes import router as websocket_router
import asyncio
from websocket_manager import manager
from utils import OFFLINE_TIMEOUT
from datetime import datetime
from db import db, init_db


app = FastAPI(
    title="Smart IoT + Auth API",
    version="1.0.0",
    description="FastAPI backend for IoT device management, telemetry, and auth system."
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(device_router)
app.include_router(websocket_router)


async def auto_offline_checker():
    """Automatically mark devices as offline if inactive beyond OFFLINE_TIMEOUT."""
    while True:
        try:
            now = datetime.utcnow()
            devices_cursor = db.devices.find({"status": "online"})

            async for device in devices_cursor:
                last_active = device.get("last_active")
                if not last_active:
                    continue

                inactive_time = (now - last_active).total_seconds()
                if inactive_time > OFFLINE_TIMEOUT:
                    await db.devices.update_one(
                        {"_id": device["_id"]},
                        {"$set": {"status": "offline"}}
                    )

                    await manager.broadcast(
                        str(device["user_id"]),
                        {
                            "type": "status_update",
                            "device_id": str(device["_id"]),
                            "status": "offline",
                            "timestamp": now.isoformat(),
                        },
                    )

                    print(f"⚙️ Device {device['_id']} set to offline (inactive {inactive_time:.1f}s)")

        except Exception as e:
            print("auto-offline error:", e)

        await asyncio.sleep(OFFLINE_TIMEOUT)


@app.on_event("startup")
async def startup_event():
    print("🚀 Starting Smart IoT Backend...")
    await init_db()  # initialize indexes
    asyncio.create_task(auto_offline_checker())
    print("✅ MongoDB connected & background tasks started.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
