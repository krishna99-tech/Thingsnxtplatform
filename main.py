from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from db import init_db, db
# Import API Gateway and Background Tasks
from api_gateway import api_gateway
from device_routes import led_schedule_worker, auto_offline_checker

from admin_routes import router as admin_router
from utils import get_password_hash
from datetime import datetime



# Configure logging
logging.basicConfig(
    level=logging.INFO if os.getenv("LOG_LEVEL", "INFO") == "INFO" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# ============================================================
# 🔄 Lifespan Context Manager (FastAPI 0.93+)
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
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

        # Seed Default Admin
        admin_user = os.getenv("DEFAULT_ADMIN_USER", "admin")
        admin_pass = os.getenv("DEFAULT_ADMIN_PASS", "admin123")
        existing_admin = await db.users.find_one({"username": admin_user})
        
        if not existing_admin:
            logger.info(f"Creating default admin user: {admin_user}")
            await db.users.insert_one({
                "username": admin_user,
                "email": "admin@thingsnxt.com",
                "hashed_password": get_password_hash(admin_pass),
                "full_name": "System Administrator",
                "is_active": True,
                "is_admin": True,
                "created_at": datetime.utcnow()
            })
        else:
            if not existing_admin.get("is_admin"):
                 await db.users.update_one({"_id": existing_admin["_id"]}, {"$set": {"is_admin": True}})


        logger.info("✅ Application startup complete")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}", exc_info=True)
        raise
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down ThingsNXT IoT Platform Backend...")
    # Any cleanup tasks can be added here
    logger.info("✅ Shutdown complete")


# ============================================================
# 🚀 FastAPI Application
# ============================================================
app = FastAPI(
    title="ThingsNXT IoT Platform API",
    version="1.0.0",
    description="Production-ready FastAPI backend for IoT device management, telemetry, dashboards, and real-time updates.",
    docs_url="/docs" if os.getenv("ENVIRONMENT", "development").lower() == "development" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT", "development").lower() == "development" else None,
    lifespan=lifespan,  # Use lifespan context manager instead of deprecated on_event
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

# ============================================================
# 🚀 Integrate API Gateway
# ============================================================
# Note: Frontend expects routes without /api prefix based on config.js
# The gateway aggregates Auth, Device, WebSocket, and Event routes.
app.include_router(api_gateway)
app.include_router(admin_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
