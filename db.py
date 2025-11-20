import logging
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()
logger = logging.getLogger(__name__)

# ==============================
# 🔌 MongoDB Connection
# ==============================
DEFAULT_DB_NAME = os.getenv("MONGO_DB_NAME", "iot_auth_db")
MONGO_URI = (
    os.getenv("MONGO_URI")
    or os.getenv("MONGODB_URI")
    or f"mongodb://127.0.0.1:27017/{DEFAULT_DB_NAME}"
)

if not os.getenv("MONGO_URI") and not os.getenv("MONGODB_URI"):
    logger.warning(
        "MONGO_URI not provided. Falling back to local instance at %s. "
        "Set MONGO_URI for production deployments.",
        MONGO_URI,
    )

# Async client for non-blocking Mongo access
client = AsyncIOMotorClient(MONGO_URI)
db = client[DEFAULT_DB_NAME]


# ==============================
# 📦 Initialize Database Indexes
# ==============================
async def init_db():
    """Create indexes for collections (async-safe) for optimal query performance."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # User indexes
        await db.users.create_index("email", unique=True)
        await db.users.create_index("username", unique=True)
        await db.users.create_index("is_active")
        logger.debug("✅ User indexes created")
        
        # Token indexes
        await db.refresh_tokens.create_index("token", unique=True)
        await db.refresh_tokens.create_index("user_id")
        await db.refresh_tokens.create_index("expires_at", expireAfterSeconds=0)  # TTL index
        await db.reset_tokens.create_index("token", unique=True)
        await db.reset_tokens.create_index("email")
        await db.reset_tokens.create_index("expires_at", expireAfterSeconds=0)  # TTL index
        logger.debug("✅ Token indexes created")
        
        # Device indexes
        await db.devices.create_index("device_token", unique=True)
        await db.devices.create_index("user_id")
        await db.devices.create_index([("user_id", 1), ("status", 1)])
        await db.devices.create_index("last_active")
        await db.devices.create_index("status")
        logger.debug("✅ Device indexes created")
        
        # Telemetry indexes
        await db.telemetry.create_index([("device_id", 1), ("key", 1)])
        await db.telemetry.create_index([("device_id", 1), ("timestamp", -1)])
        await db.telemetry.create_index("timestamp")
        logger.debug("✅ Telemetry indexes created")
        
        # Dashboard indexes
        await db.dashboards.create_index("user_id")
        await db.dashboards.create_index([("user_id", 1), ("created_at", -1)])
        logger.debug("✅ Dashboard indexes created")
        
        # Widget indexes
        await db.widgets.create_index("dashboard_id")
        await db.widgets.create_index("device_id")
        await db.widgets.create_index([("dashboard_id", 1), ("type", 1)])
        await db.widgets.create_index("config.virtual_pin")
        logger.debug("✅ Widget indexes created")
        
        # LED Schedule indexes
        await db.led_schedules.create_index("widget_id")
        await db.led_schedules.create_index("device_id")
        await db.led_schedules.create_index([("status", 1), ("execute_at", 1)])
        await db.led_schedules.create_index("execute_at")
        logger.debug("✅ LED Schedule indexes created")
        
        # Notification indexes
        await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
        await db.notifications.create_index([("user_id", 1), ("read", 1)])
        await db.notifications.create_index("created_at")
        logger.debug("✅ Notification indexes created")
        
        logger.info("✅ All MongoDB indexes initialized successfully")
    except Exception as e:
        logger.error(f"❌ Error initializing database indexes: {e}", exc_info=True)
        raise


# ==============================
# 🧩 Utility: Convert Mongo docs to JSON-safe dicts
# ==============================
def doc_to_dict(doc):
    """Convert MongoDB document (with ObjectId) into JSON-safe dict."""
    if not doc:
        return None

    def convert_value(v):
        # Handle nested ObjectIds
        if isinstance(v, ObjectId):
            return str(v)
        elif isinstance(v, list):
            return [convert_value(i) for i in v]
        elif isinstance(v, dict):
            return {k: convert_value(val) for k, val in v.items()}
        else:
            return v

    d = {k: convert_value(v) for k, v in dict(doc).items()}
    # Rename _id to id for API responses
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d
