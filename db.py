import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

# ==============================
# 🔌 MongoDB Connection
# ==============================
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Async client for non-blocking Mongo access
client = AsyncIOMotorClient(MONGO_URI)
db = client.iot_auth_db


# ==============================
# 📦 Initialize Database Indexes
# ==============================
async def init_db():
    """Create indexes for collections (async-safe)."""
    await db.users.create_index("email", unique=True)
    await db.users.create_index("username", unique=True)
    await db.refresh_tokens.create_index("token", unique=True)
    await db.reset_tokens.create_index("token", unique=True)
    await db.devices.create_index("device_token", unique=True)
    print("✅ MongoDB indexes initialized")


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
