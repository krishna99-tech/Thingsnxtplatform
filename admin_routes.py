from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from typing import List, Optional, Dict, Any
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
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication failed")

    # 1. Check for specific admin flag
    if current_user.get("is_admin"):
        return current_user
        
    # 2. Fallback: Check against default environment variable
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
    role: Any = "User" # Use Any to prevent validation crash if DB has objects
    access_right: Optional[str] = "Standard"
    is_active: bool = True
    created_at: Optional[Any] = None
    last_login: Optional[Any] = None

    class Config:
        from_attributes = True

class DeviceOutAdmin(BaseModel):
    id: str
    name: str
    status: str
    type: Optional[str] = "sensor"
    location: Optional[str] = None
    battery: Optional[int] = None
    value: Optional[Any] = None
    last_active: Optional[Any] = None
    user_id: Optional[str] = None
    device_token: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None

class PaginatedResponse(BaseModel):
    total: int
    page: int
    limit: int
    pages: int

class UserListResponse(PaginatedResponse):
    data: List[UserOutAdmin]

class DeviceListResponse(PaginatedResponse):
    data: List[DeviceOutAdmin]

class ActivityListResponse(PaginatedResponse):
    data: List[Dict[str, Any]]

class DeviceCreateAdmin(BaseModel):
    name: str = "Unnamed Device"
    user_id: str

class UserCreateRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    role: str = "User"

class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[Any] = None
    access_right: Optional[str] = None
    is_active: Optional[bool] = None

class DeviceUpdateRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None

class DeviceControlRequest(BaseModel):
    command: str
    params: Dict[str, Any] = {}

class DeviceTransferRequest(BaseModel):
    user_id: str

class BulkDeleteRequest(BaseModel):
    deviceIds: List[str]

class UserAlertRequest(BaseModel):
    user_id: str
    subject: str
    message: str

class BroadcastRequest(BaseModel):
    subject: str
    message: str
    recipients: Optional[List[str]] = None

# ============================================================
# 🛠️ Helpers
# ============================================================
def sanitize_user(user_dict: dict) -> dict:
    """Ensure user dictionary fields are compatible with response models."""
    if not user_dict:
        return {}
    
    # Ensure role is a string
    role = user_dict.get("role", "User")
    if isinstance(role, dict):
        user_dict["role"] = role.get("role", "User")
    elif not isinstance(role, str):
        user_dict["role"] = str(role)

    # Ensure defaults
    if "is_active" not in user_dict:
        user_dict["is_active"] = True
    
    if "access_right" not in user_dict:
        user_dict["access_right"] = "Standard"
    
    return user_dict

# ============================================================
# 👥 User Management Routes
# ============================================================
@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(verify_admin)
):
    """List registered users with pagination and search."""
    query = {}
    if search:
        query = {
            "$or": [
                {"username": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"full_name": {"$regex": search, "$options": "i"}}
            ]
        }

    total = await db.users.count_documents(query)
    skip = (page - 1) * limit

    users = []
    cursor = db.users.find(query).sort("created_at", -1).skip(skip).limit(limit)
    async for user in cursor:
        u_dict = doc_to_dict(user)
        users.append(sanitize_user(u_dict))
    
    return {
        "data": users,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if limit > 0 else 0
    }

@router.post("/users", response_model=UserOutAdmin)
async def create_user_admin(payload: UserCreateRequest, current_user: dict = Depends(verify_admin)):
    """Create a new user (Admin only)."""
    if await db.users.find_one({"$or": [{"email": payload.email}, {"username": payload.username}]}):
        raise HTTPException(status_code=400, detail="User already exists")
    
    hashed_pw = get_password_hash(payload.password)
    user_doc = {
        "username": payload.username,
        "email": payload.email,
        "hashed_password": hashed_pw,
        "full_name": payload.full_name,
        "role": payload.role,
        "is_admin": payload.role == "Admin",
        "is_active": True,
        "created_at": datetime.utcnow()
    }
    res = await db.users.insert_one(user_doc)
    user_doc["_id"] = res.inserted_id
    return sanitize_user(doc_to_dict(user_doc))

@router.put("/users/{user_id}", response_model=UserOutAdmin)
async def update_user_admin(user_id: str, payload: UserUpdateRequest, current_user: dict = Depends(verify_admin)):
    """Update user details."""
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    update_data = {k: v for k, v in payload.dict(exclude_unset=True).items()}
    if "role" in update_data:
        # Handle role object if sent by accident
        if isinstance(update_data["role"], dict):
            update_data["role"] = update_data["role"].get("role", "User")
        update_data["is_admin"] = (update_data["role"] == "Admin")
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
    updated = await db.users.find_one({"_id": ObjectId(user_id)})
    return sanitize_user(doc_to_dict(updated))

@router.patch("/users/{user_id}/role")
async def update_user_role(user_id: str, payload: dict, current_user: dict = Depends(verify_admin)):
    """Update user role."""
    role = payload.get("role")
    if not role:
         raise HTTPException(status_code=400, detail="Role required")
    
    # Handle role object
    if isinstance(role, dict):
        role = role.get("role", "User")
        
    await db.users.update_one(
        {"_id": ObjectId(user_id)}, 
        {"$set": {"role": role, "is_admin": role == "Admin"}}
    )
    return {"message": "Role updated"}

@router.get("/devices", response_model=DeviceListResponse)
async def list_all_devices(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by device name, token, or owner"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    current_user: dict = Depends(verify_admin)
):
    """List ALL devices with pagination, search, and owner details (Admin only)."""
    
    # Build Aggregation Pipeline
    pipeline = []

    # 1. Match Status
    if status_filter:
        pipeline.append({"$match": {"status": status_filter}})

    # 2. Lookup Owner
    pipeline.append({
        "$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "_id",
            "as": "owner"
        }
    })
    pipeline.append({"$unwind": {"path": "$owner", "preserveNullAndEmptyArrays": True}})

    # 3. Search (Device Name, Token, or Owner Details)
    if search:
        pipeline.append({
            "$match": {
                "$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"device_token": search},
                    {"owner.username": {"$regex": search, "$options": "i"}},
                    {"owner.email": {"$regex": search, "$options": "i"}}
                ]
            }
        })

    # 4. Facet for Pagination & Count
    pipeline.append({
        "$facet": {
            "metadata": [{"$count": "total"}],
            "data": [
                {"$sort": {"created_at": -1}},
                {"$skip": (page - 1) * limit},
                {"$limit": limit}
            ]
        }
    })

    result = await db.devices.aggregate(pipeline).to_list(length=1)
    
    metadata = result[0]["metadata"]
    data = result[0]["data"]
    
    total = metadata[0]["total"] if metadata else 0
    
    devices = []
    for d in data:
        device_dict = doc_to_dict(d)
        
        # Extract owner details from the joined data
        owner = d.get("owner")
        if owner:
            device_dict["owner_name"] = owner.get("username", "Unknown")
            device_dict["owner_email"] = owner.get("email", "N/A")
            # Clean up nested owner dict from response
            if "owner" in device_dict:
                del device_dict["owner"]
        else:
            device_dict["user_id"] = None
            device_dict["owner_name"] = "System / Unassigned"
            device_dict["owner_email"] = "N/A"
            if "owner" in device_dict:
                del device_dict["owner"]
            
        devices.append(device_dict)
        
    return {
        "data": devices,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if limit > 0 else 0
    }

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

@router.get("/devices/{device_id}", response_model=DeviceOutAdmin)
async def get_device_detail_admin(device_id: str, current_user: dict = Depends(verify_admin)):
    """Get detailed information about a specific device."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    device_oid = ObjectId(device_id)
    device = await db.devices.find_one({"_id": device_oid})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    user = await db.users.find_one({"_id": device.get("user_id")})
    device_dict = doc_to_dict(device)
    if user:
        device_dict["owner_name"] = user.get("username")
        device_dict["owner_email"] = user.get("email")
    
    return device_dict

@router.put("/devices/{device_id}", response_model=DeviceOutAdmin)
async def update_device_admin(device_id: str, payload: DeviceUpdateRequest, current_user: dict = Depends(verify_admin)):
    """Update device details as administrator."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    update_data = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    await db.devices.update_one({"_id": ObjectId(device_id)}, {"$set": update_data})
    updated = await db.devices.find_one({"_id": ObjectId(device_id)})
    
    user = await db.users.find_one({"_id": updated.get("user_id")})
    device_dict = doc_to_dict(updated)
    if user:
        device_dict["owner_name"] = user.get("username")
        device_dict["owner_email"] = user.get("email")
    
    return device_dict

@router.delete("/devices/{device_id}")
async def delete_device_admin(device_id: str, current_user: dict = Depends(verify_admin)):
    """Delete a device as administrator."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    oid = ObjectId(device_id)
    device = await db.devices.find_one({"_id": oid})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    await db.devices.delete_one({"_id": oid})
    await db.telemetry.delete_many({"device_id": oid})
    
    # Log activity
    await db.admin_activity.insert_one({
        "action": "delete_device",
        "admin_id": ObjectId(current_user["id"]),
        "device_name": device.get("name"),
        "timestamp": datetime.utcnow()
    })
    
    return {"message": "Device deleted successfully"}

@router.post("/devices/{device_id}/control")
async def control_device_admin(device_id: str, payload: DeviceControlRequest, current_user: dict = Depends(verify_admin)):
    """Expert control over a device from admin console."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    oid = ObjectId(device_id)
    device = await db.devices.find_one({"_id": oid})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Logic for control (e.g. broadcasting to WebSocket if device is listening)
    from websocket_manager import manager
    await manager.broadcast(
        str(device["user_id"]),
        {
            "type": "control_command",
            "device_id": device_id,
            "command": payload.command,
            "params": payload.params,
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    # Additional logic for specific commands if needed (e.g. toggle_power)
    if payload.command == "toggle_power":
        status_val = payload.params.get("status", "offline")
        await db.devices.update_one({"_id": oid}, {"$set": {"status": status_val}})
        # Broadcast status update
        from event_manager import event_manager
        asyncio.create_task(event_manager.broadcast({
            "type": "status_update",
            "device_id": device_id,
            "status": status_val,
            "timestamp": datetime.utcnow().isoformat()
        }))

    return {"message": f"Command {payload.command} dispatched"}

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
# ============================================================
# 👤 USER DETAIL ENDPOINT
# ============================================================
@router.get("/users/{user_id}", response_model=UserOutAdmin)
async def get_user_detail(user_id: str, current_user: dict = Depends(verify_admin)):
    """Get detailed information about a specific user including their devices and activity."""
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
    async for log in db.admin_activity.find({
        "$or": [
            {"target_user_id": user_oid},
            {"recipient": user.get("email")},
            {"target_email": user.get("email")}
        ]
    }).sort("timestamp", -1).limit(20):
        activity.append(doc_to_dict(log))
    
    user_dict = doc_to_dict(user)
    user_dict["devices"] = devices
    user_dict["recent_activity"] = activity
    user_dict["device_count"] = len(devices)
    
    return sanitize_user(user_dict)


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

@router.get("/analytics/stats")
async def get_analytics_stats(current_user: dict = Depends(verify_admin)):
    """Alias for analytics stats used by frontend."""
    return await get_analytics(current_user)


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

@router.post("/export/device/{device_id}")
async def export_device_data(device_id: str, payload: dict, current_user: dict = Depends(verify_admin)):
    """Export device telemetry as JSON."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    device_oid = ObjectId(device_id)
    telemetry = []
    # Mocking date range filtering for now
    async for t in db.telemetry.find({"device_id": device_oid}).sort("timestamp", -1).limit(1000):
        telemetry.append(doc_to_dict(t))
    
    return {"data": telemetry, "count": len(telemetry)}

@router.post("/devices/bulk-delete")
async def bulk_delete_devices(payload: BulkDeleteRequest, current_user: dict = Depends(verify_admin)):
    """Delete multiple devices at once."""
    oids = [ObjectId(did) for did in payload.deviceIds if ObjectId.is_valid(did)]
    if not oids:
        raise HTTPException(status_code=400, detail="No valid device IDs provided")
    
    await db.devices.delete_many({"_id": {"$in": oids}})
    await db.telemetry.delete_many({"device_id": {"$in": oids}})
    
    return {"message": f"Deleted {len(oids)} devices"}

@router.post("/devices/bulk-update")
async def bulk_update_devices_admin(payload: dict, current_user: dict = Depends(verify_admin)):
    """Update multiple devices status or config."""
    device_ids = payload.get("deviceIds", [])
    updates = payload.get("updates", {})
    
    oids = [ObjectId(did) for did in device_ids if ObjectId.is_valid(did)]
    if not oids:
        raise HTTPException(status_code=400, detail="No valid device IDs or updates provided")
    
    await db.devices.update_many({"_id": {"$in": oids}}, {"$set": updates})
    
    return {"message": f"Updated {len(oids)} devices"}


@router.get("/activity", response_model=ActivityListResponse)
async def get_admin_activity(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(verify_admin)
):
    """Get recent admin actions/notifications with pagination and date filtering."""
    query = {}
    if start_date or end_date:
        query["timestamp"] = {}
        if start_date:
            query["timestamp"]["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            # End of day for end_date
            query["timestamp"]["$lte"] = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)

    total = await db.admin_activity.count_documents(query)
    skip = (page - 1) * limit
    
    cursor = db.admin_activity.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    activities = []
    async for doc in cursor:
        activities.append(doc_to_dict(doc))
    return {
        "data": activities,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit
    }


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

@router.get("/alerts")
async def get_alerts(current_user: dict = Depends(verify_admin)):
    """Get active alerts (devices with warning status)."""
    cursor = db.devices.find({"status": "warning"})
    alerts = []
    async for d in cursor:
        d_dict = doc_to_dict(d)
        d_dict["message"] = "Device status warning"
        alerts.append(d_dict)
    return alerts

@router.get("/analytics/devices/{device_id}/metrics")
async def get_device_metrics_admin(device_id: str, range: str = "24h", current_user: dict = Depends(verify_admin)):
    """Get device metrics for charts."""
    if not ObjectId.is_valid(device_id):
        raise HTTPException(status_code=400, detail="Invalid device ID")
    
    # Simple mock/fetch logic to match frontend expectation
    # In production, query time-series data
    telemetry = await db.telemetry.find_one({"device_id": ObjectId(device_id), "key": "telemetry_json"})
    if not telemetry:
        return []
    
    # Return a single point for now, or mock history
    val = telemetry.get("value", {})
    return [{"time": datetime.utcnow().strftime("%H:%M"), **val}]
