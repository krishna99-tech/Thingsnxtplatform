from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

from db import db, doc_to_dict
from auth_routes import get_current_user
from utils import send_email, get_password_hash # Reusing existing utils
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/admin", tags=["Admin"])

# ============================================================
# 🛡️ Admin Dependency
# ============================================================
def verify_admin(current_user: dict = Depends(get_current_user)):
    """
    Dependency to ensure the user has admin privileges.
    Checks for 'is_admin' flag or compares against default admin username.
    """
    # 1. Check for specific admin flag
    if current_user.get("is_admin"):
        return current_user
        
    # 2. Fallback: Check against default environment variable (for safety)
    import os
    default_admin = os.getenv("DEFAULT_ADMIN_USER", "admin")
    
    if current_user.get("username") == default_admin:
        return current_user
        
    raise HTTPException(status_code=403, detail="Admin privileges required")

# ============================================================
# 📋 Models
# ============================================================
class UserOutAdmin(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

class DeviceOutAdmin(BaseModel):
    id: str
    name: str
    status: str
    last_active: Optional[datetime] = None
    user_id: str
    device_token: Optional[str] = None


class BroadcastRequest(BaseModel):
    subject: str
    message: str
    recipients: Optional[List[str]] = None  # List of emails, if None send to all

# ============================================================
# 👥 User Management Routes
# ============================================================
@router.get("/users", response_model=List[UserOutAdmin])
async def list_users(current_user: dict = Depends(verify_admin)):
    """List all registered users."""
    users = []
    async for user in db.users.find({}):
        u_dict = doc_to_dict(user)
        # ID is already converted by doc_to_dict
        users.append(u_dict)
    return users

@router.get("/devices", response_model=List[DeviceOutAdmin])
async def list_all_devices(current_user: dict = Depends(verify_admin)):
    """List ALL devices in the system (Admin only)."""
    devices = []
    async for d in db.devices.find({}):
        devices.append(doc_to_dict(d))
    return devices

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, current_user: dict = Depends(verify_admin)):
    """Delete a user and their data."""
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    uid = ObjectId(user_id)
    # Prevent deleting self
    if str(uid) == str(current_user["id"]):
         raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    await db.users.delete_one({"_id": uid})
    await db.devices.delete_many({"user_id": uid})
    await db.telemetry.delete_many({"user_id": uid}) # If user_id is stored in telemetry
    # Cleanup tokens
    await db.refresh_tokens.delete_many({"user_id": uid})
    
    return {"message": "User deleted successfully"}

# ============================================================
# 📢 Broadcast / Activity Logic
# ============================================================
@router.post("/broadcast")
async def send_broadcast_notification(
    payload: BroadcastRequest, 
    current_user: dict = Depends(verify_admin)
):
    """
    Send an email notification to users.
    """
    query = {}
    if payload.recipients:
        query = {"email": {"$in": payload.recipients}}
    
    count = 0
    async for user in db.users.find(query):
        if user.get("email"):
             # Send email in background (conceptually)
             # In a real app, use BackgroundTasks
             try:
                 # Use branded broadcast email function
                 from utils import send_broadcast_email
                 
                 send_broadcast_email(
                     to_email=user["email"],
                     subject=payload.subject,
                     message_content=payload.message
                 )
                 
                 # Log activity
                 await db.admin_activity.insert_one({
                     "action": "broadcast_email",
                     "admin_id": ObjectId(current_user["id"]),
                     "recipient": user["email"],
                     "subject": payload.subject,
                     "timestamp": datetime.utcnow()
                 })
                 count += 1
             except Exception as e:
                 print(f"Failed to send email to {user['email']}: {e}")

    return {"message": f"Broadcast sent to {count} users"}


# ============================================================
# 👤 USER DETAIL ENDPOINT
# ============================================================
@router.get("/users/{user_id}")
async def get_user_detail(user_id: str, current_user: dict = Depends(verify_admin)):
    """Get detailed information about a specific user including their devices and activity."""
    from bson import ObjectId
    
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    user_oid = ObjectId(user_id)
    user = await db.users.find_one({"_id": user_oid})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user's devices
    devices = []
    async for device in db.devices.find({"user_id": user_oid}):
        devices.append(doc_to_dict(device))
    
    # Get recent activity (last 20 actions)
    activity = []
    async for log in db.admin_activity.find({"recipient": user.get("email")}).sort("timestamp", -1).limit(20):
        activity.append(doc_to_dict(log))
    
    user_dict = doc_to_dict(user)
    user_dict["devices"] = devices
    user_dict["recent_activity"] = activity
    user_dict["device_count"] = len(devices)
    
    return user_dict


# ============================================================
# 📊 ANALYTICS ENDPOINT
# ============================================================
@router.get("/analytics")
async def get_analytics(current_user: dict = Depends(verify_admin)):
    """Get system analytics data for charts and metrics."""
    from datetime import timedelta
    
    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    
    # User growth (last 30 days)
    user_growth = []
    for i in range(30):
        date = thirty_days_ago + timedelta(days=i)
        next_date = date + timedelta(days=1)
        count = await db.users.count_documents({
            "created_at": {"$gte": date, "$lt": next_date}
        })
        user_growth.append({
            "date": date.strftime("%Y-%m-%d"),
            "count": count
        })
    
    # Device registration trends
    device_growth = []
    for i in range(30):
        date = thirty_days_ago + timedelta(days=i)
        next_date = date + timedelta(days=1)
        count = await db.devices.count_documents({
            "last_active": {"$gte": date, "$lt": next_date}
        })
        device_growth.append({
            "date": date.strftime("%Y-%m-%d"),
            "count": count
        })
    
    # Activity by type
    activity_by_type = {}
    async for log in db.admin_activity.find({"timestamp": {"$gte": thirty_days_ago}}):
        action = log.get("action", "unknown")
        activity_by_type[action] = activity_by_type.get(action, 0) + 1
    
    # Current stats
    total_users = await db.users.count_documents({})
    total_devices = await db.devices.count_documents({})
    online_devices = await db.devices.count_documents({"status": "online"})
    
    return {
        "user_growth": user_growth,
        "device_growth": device_growth,
        "activity_by_type": activity_by_type,
        "current_stats": {
            "total_users": total_users,
            "total_devices": total_devices,
            "online_devices": online_devices,
            "offline_devices": total_devices - online_devices
        }
    }


# ============================================================
# 📥 DATA EXPORT ENDPOINTS
# ============================================================
@router.get("/export/users")
async def export_users(current_user: dict = Depends(verify_admin)):
    """Export all users as JSON (frontend will convert to CSV)."""
    users = []
    async for user in db.users.find({}):
        user_dict = doc_to_dict(user)
        # Remove sensitive data
        user_dict.pop("password", None)
        users.append(user_dict)
    
    return {"data": users, "count": len(users)}


@router.get("/export/devices")
async def export_devices(current_user: dict = Depends(verify_admin)):
    """Export all devices as JSON."""
    devices = []
    async for device in db.devices.find({}):
        devices.append(doc_to_dict(device))
    
    return {"data": devices, "count": len(devices)}


@router.get("/export/activity")
async def export_activity(current_user: dict = Depends(verify_admin)):
    """Export activity logs as JSON."""
    logs = []
    async for log in db.admin_activity.find({}).sort("timestamp", -1).limit(1000):
        logs.append(doc_to_dict(log))
    
    return {"data": logs, "count": len(logs)}


@router.get("/activity")
async def get_admin_activity(current_user: dict = Depends(verify_admin)):
    """Get recent admin actions/notifications."""
    cursor = db.admin_activity.find({}).sort("timestamp", -1).limit(50)
    activities = []
    async for doc in cursor:
        activities.append(doc_to_dict(doc))
    return activities
