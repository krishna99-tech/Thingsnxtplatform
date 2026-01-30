from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import List, Optional
from bson import ObjectId
from datetime import datetime
import asyncio
import secrets

from db import db, doc_to_dict
from auth_routes import get_current_user
from utils import send_email, get_password_hash, send_broadcast_email, send_user_alert_email # Reusing existing utils
from pydantic import BaseModel, EmailStr
import json
import os

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
    user_id: Optional[str] = None
    device_token: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None

class DeviceCreateAdmin(BaseModel):
    name: str = "Unnamed Device"
    user_id: str


class DeviceTransferRequest(BaseModel):
    user_id: str


class UserAlertRequest(BaseModel):
    user_id: str
    subject: str
    message: str

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
    """List ALL devices in the system with owner details (Admin only)."""
    devices = []
    async for d in db.devices.find({}):
        device_dict = doc_to_dict(d)
        
        # Fetch owner details with fallback for orphaned devices
        user_id = device_dict.get("user_id")
        if user_id and ObjectId.is_valid(user_id):
            owner = await db.users.find_one({"_id": ObjectId(user_id)})
            if owner:
                device_dict["owner_name"] = owner.get("username", "Unknown")
                device_dict["owner_email"] = owner.get("email", "N/A")
            else:
                device_dict["owner_name"] = "Orphaned (User Not Found)"
                device_dict["owner_email"] = "N/A"
        else:
            device_dict["user_id"] = None
            device_dict["owner_name"] = "System / Unassigned"
            device_dict["owner_email"] = "N/A"
            
        devices.append(device_dict)
    return devices

@router.post("/devices", response_model=DeviceOutAdmin)
async def create_device_admin(payload: DeviceCreateAdmin, current_user: dict = Depends(verify_admin)):
    """Register a new device for any user (Admin only)."""
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status_code=400, detail="Invalid target user ID")
    
    user = await db.users.find_one({"_id": ObjectId(payload.user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="Target user not found")

    new_device = {
        "name": payload.name,
        "user_id": ObjectId(payload.user_id),
        "status": "offline",
        "created_at": datetime.utcnow(),
        "last_active": None,
        "device_token": secrets.token_hex(16)
    }
    
    result = await db.devices.insert_one(new_device)
    new_device["_id"] = result.inserted_id
    
    # Log activity
    await db.admin_activity.insert_one({
        "action": "create_device",
        "admin_id": ObjectId(current_user["id"]),
        "device_name": payload.name,
        "target_user_id": ObjectId(payload.user_id),
        "timestamp": datetime.utcnow()
    })

    # Broadcast device addition via global event manager for real-time UI updates
    from event_manager import event_manager
    asyncio.create_task(event_manager.broadcast({
        "type": "device_added",
        "data": doc_to_dict(new_device),
        "timestamp": datetime.utcnow().isoformat()
    }))
    
    device_dict = doc_to_dict(new_device)
    device_dict["owner_name"] = user.get("username")
    device_dict["owner_email"] = user.get("email")
    
    return device_dict

    return device_dict

@router.patch("/devices/{device_id}/transfer", response_model=DeviceOutAdmin)
async def transfer_device_ownership(device_id: str, payload: DeviceTransferRequest, current_user: dict = Depends(verify_admin)):
    """Administrative ownership transfer of a device."""
    if not ObjectId.is_valid(device_id) or not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status_code=400, detail="Invalid ID format detected")
    
    device_oid = ObjectId(device_id)
    user_oid = ObjectId(payload.user_id)
    
    device = await db.devices.find_one({"_id": device_oid})
    if not device:
        raise HTTPException(status_code=404, detail="Signature not found in registry")
        
    user = await db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=404, detail="Target user identity not found")

    old_owner_id = device.get("user_id")
    
    # Execute transfer
    await db.devices.update_one(
        {"_id": device_oid},
        {"$set": {"user_id": user_oid}}
    )
    
    # Log high-priority activity
    await db.admin_activity.insert_one({
        "action": "transfer_device",
        "admin_id": ObjectId(current_user["id"]),
        "device_id": device_oid,
        "device_name": device.get("name"),
        "from_user": str(old_owner_id),
        "to_user": str(user_oid),
        "target_email": user.get("email"),
        "timestamp": datetime.utcnow()
    })
    
    # Broadcast update
    from event_manager import event_manager
    asyncio.create_task(event_manager.broadcast({
        "type": "device_transferred",
        "data": {
            "device_id": device_id,
            "new_owner_id": str(user_oid),
            "owner_name": user.get("username")
        },
        "timestamp": datetime.utcnow().isoformat()
    }))

    # Return refreshed device
    updated_device = await db.devices.find_one({"_id": device_oid})
    device_dict = doc_to_dict(updated_device)
    device_dict["owner_name"] = user.get("username")
    device_dict["owner_email"] = user.get("email")
    return device_dict

@router.get("/dashboards/{device_id}")
async def get_device_dashboards(device_id: str, current_user: dict = Depends(verify_admin)):
    """Get dashboards associated with this device or create a default one."""
    if not ObjectId.is_valid(device_id):
         raise HTTPException(status_code=400, detail="Invalid device ID")
    
    device_oid = ObjectId(device_id)
    device = await db.devices.find_one({"_id": device_oid})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # For now, we'll try to find any dashboard owned by the same user
    # or specifically linked to this device if we had a field.
    # We will look for widgets linked to this device and their parent dashboards.
    dashboards = []
    cursor = db.widgets.find({"device_id": device_oid})
    dashboard_ids = set()
    async for w in cursor:
        if w.get("dashboard_id"):
            dashboard_ids.add(w["dashboard_id"])
    
    if dashboard_ids:
        async for d in db.dashboards.find({"_id": {"$in": list(dashboard_ids)}}):
            dashboards.append(doc_to_dict(d))
            
    # If no dashboards found, return all dashboards for that user
    if not dashboards:
        async for d in db.dashboards.find({"user_id": device["user_id"]}):
            dashboards.append(doc_to_dict(d))

    return dashboards

@router.get("/devices/{device_id}/telemetry")
async def get_device_telemetry_admin(device_id: str, current_user: dict = Depends(verify_admin)):
    """Get latest telemetry for a device (Admin only)."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    device_oid = ObjectId(device_id)
    telemetry = await db.telemetry.find_one({"device_id": device_oid, "key": "telemetry_json"})
    
    if not telemetry:
        return {"data": {}, "timestamp": None}
        
    return {
        "data": telemetry.get("value", {}),
        "timestamp": telemetry.get("timestamp")
    }

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, current_user: dict = Depends(verify_admin)):
    """Delete a user and their data."""
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    uid = ObjectId(user_id)
    # Prevent deleting self
    if str(uid) == str(current_user["id"]):
         raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    user = await db.users.find_one({"_id": uid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.users.delete_one({"_id": uid})
    await db.devices.delete_many({"user_id": uid})
    await db.telemetry.delete_many({"user_id": uid}) # If user_id is stored in telemetry
    # Cleanup tokens
    await db.refresh_tokens.delete_many({"user_id": uid})
    
    # Log activity
    await db.admin_activity.insert_one({
        "action": "delete_user",
        "admin_id": ObjectId(current_user["id"]),
        "target_email": user.get("email"),
        "timestamp": datetime.utcnow()
    })
    
    return {"message": "User deleted successfully"}

# ============================================================
# 📢 Broadcast / Activity Logic
# ============================================================
@router.post("/broadcast")
async def send_broadcast_notification(
    payload: BroadcastRequest, 
    background_tasks: BackgroundTasks,
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
             # Dispatch via BackgroundTasks
             background_tasks.add_task(
                 send_broadcast_email,
                 user["email"],
                 payload.subject,
                 payload.message
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

    return {"message": f"Broadcast initiated for {count} users"}


@router.post("/users/alert")
async def send_user_alert(
    payload: UserAlertRequest, 
    background_tasks: BackgroundTasks, 
    current_user: dict = Depends(verify_admin)
):
    """Send a specific system alert/error message to a particular user."""
    if not ObjectId.is_valid(payload.user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")
        
    user = await db.users.find_one({"_id": ObjectId(payload.user_id)})
    if not user or not user.get("email"):
        raise HTTPException(status_code=404, detail="User not found or has no email associated")

    background_tasks.add_task(
        send_user_alert_email, 
        user["email"], 
        payload.subject, 
        payload.message
    )
    
    return {"message": f"Alert dispatched to {user['username']} ({user['email']})"}


# ============================================================
# 🛡️ SECURITY RULES MANAGEMENT
# ============================================================
@router.get("/security-rules")
async def get_security_rules(current_user: dict = Depends(verify_admin)):
    """Read the security_rules.json file."""
    path = os.path.join(os.path.dirname(__file__), "security_rules.json")
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read rules: {str(e)}")

@router.post("/security-rules")
async def update_security_rules(rules: dict, current_user: dict = Depends(verify_admin)):
    """Update the security_rules.json file."""
    path = os.path.join(os.path.dirname(__file__), "security_rules.json")
    try:
        with open(path, 'w') as f:
            json.dump(rules, f, indent=2)
        
        # Log activity
        await db.admin_activity.insert_one({
            "action": "update_security_rules",
            "admin_id": ObjectId(current_user["id"]),
            "timestamp": datetime.utcnow()
        })
        
        return {"message": "Security rules updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update rules: {str(e)}")


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


@router.get("/notifications")
async def get_notifications(current_user: dict = Depends(verify_admin)):
    """Get system notifications (formatted activity logs)."""
    cursor = db.admin_activity.find({}).sort("timestamp", -1).limit(15)
    notifications = []
    async for doc in cursor:
        notif = {
            "id": str(doc["_id"]),
            "message": f"Action: {doc.get('action')} - {doc.get('subject', 'System Event')}",
            "time": doc.get("timestamp"),
            "read": doc.get("read", False),
            "type": "info" if "error" not in str(doc.get("action")).lower() else "error"
        }
        notifications.append(notif)
    return notifications
