from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from auth_routes import router as auth_router
from device_routes import router as device_router, led_schedule_worker
from websocket_routes import router as websocket_router
import asyncio
import logging
import os
from websocket_manager import manager
from utils import OFFLINE_TIMEOUT
from datetime import datetime
from db import db, init_db

# Import the new routers and workers
from events import router as events_router
from event_manager import event_manager



# Configure logging
logging.basicConfig(
    level=logging.INFO if os.getenv("LOG_LEVEL", "INFO") == "INFO" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ThingsNXT IoT Platform API",
    version="1.0.0",
    description="Production-ready FastAPI backend for IoT device management, telemetry, dashboards, and real-time updates.",
    docs_url="/docs" if os.getenv("ENVIRONMENT", "development") == "development" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT", "development") == "development" else None,
)

# CORS Configuration - Update for production
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Note: Frontend expects routes without /api prefix based on config.js
app.include_router(auth_router)
app.include_router(device_router)
app.include_router(websocket_router)
app.include_router(events_router)


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    try:
        # Check database connection
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "disconnected"
    
    # Get WebSocket connection stats
    ws_connections = manager.get_connection_count()
    ws_users = len(manager.get_connected_users())
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_status,
        "websocket": {
            "total_connections": ws_connections,
            "connected_users": ws_users,
        },
        "version": "1.0.0",
    }


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "message": "ThingsNXT IoT Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


async def auto_offline_checker():
    """Automatically mark devices as offline if inactive beyond OFFLINE_TIMEOUT."""
    logger.info("Starting auto-offline checker background task")
    while True:
        try:
            now = datetime.utcnow()
            devices_cursor = db.devices.find({"status": "online"})
            offline_count = 0

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

                    # ❗ FIX: Broadcast to global SSE event manager
                    asyncio.create_task(event_manager.broadcast({
                        "type": "status_update",
                        "device_id": str(device["_id"]),
                        "status": "offline",
                        "timestamp": now.isoformat()
                    }))

                    await manager.broadcast(
                        str(device["user_id"]),
                        {
                            "type": "status_update",
                            "device_id": str(device["_id"]),
                            "status": "offline",
                            "timestamp": now.isoformat(),
                        },
                    )

                    offline_count += 1
                    logger.debug(f"Device {device['_id']} set to offline (inactive {inactive_time:.1f}s)")

            if offline_count > 0:
                logger.info(f"Marked {offline_count} device(s) as offline")

        except Exception as e:
            logger.error(f"Auto-offline checker error: {e}", exc_info=True)

        await asyncio.sleep(OFFLINE_TIMEOUT)


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting ThingsNXT IoT Platform Backend...")
    
    try:
        # Initialize database indexes
        await init_db()
        logger.info("✅ Database indexes initialized")



        # Start background tasks
        asyncio.create_task(auto_offline_checker())
        logger.info("✅ Auto-offline checker started")

        asyncio.create_task(led_schedule_worker())
        logger.info("✅ LED schedule worker started")

        logger.info("✅ Application startup complete")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}", exc_info=True)
        raise



@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on application shutdown."""
    logger.info("🛑 Shutting down ThingsNXT IoT Platform Backend...")
    # Any cleanup tasks can be added here
    logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
