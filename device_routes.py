from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from bson import ObjectId
from datetime import datetime, timedelta
import asyncio
import secrets
import json
import logging
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List

from db import db
from utils import OFFLINE_TIMEOUT, doc_to_dict
from websocket_manager import manager
from auth_routes import get_current_user

logger = logging.getLogger(__name__)

# Timezone support
import pytz  # Import pytz first as fallback
try:
    from zoneinfo import ZoneInfo
    try:
        # Test if tzdata is available
        IST = ZoneInfo("Asia/Kolkata")
        ZoneInfo_available = True
    except Exception:
        # tzdata not available, fall back to pytz
        IST = pytz.timezone("Asia/Kolkata")
        ZoneInfo_available = False
except ImportError:
    # Python < 3.9 fallback
    try:
        from backports.zoneinfo import ZoneInfo
        try:
            IST = ZoneInfo("Asia/Kolkata")
            ZoneInfo_available = True
        except Exception:
            IST = pytz.timezone("Asia/Kolkata")
            ZoneInfo_available = False
    except ImportError:
        IST = pytz.timezone("Asia/Kolkata")
        ZoneInfo_available = False
        ZoneInfo = None



router = APIRouter(tags=["Devices", "Telemetry", "Dashboards", "Widgets"])


# -------------------------------
# 🔹 Helper: Safe ObjectId convert
# -------------------------------
def safe_oid(value: str) -> Optional[ObjectId]:
    return ObjectId(value) if ObjectId.is_valid(value) else None


# =====================
# Pydantic Models
# =====================

class DeviceCreate(BaseModel):
    name: Optional[str] = Field("Unnamed Device", max_length=100)


class TelemetryData(BaseModel):
    device_token: str
    data: Dict[str, Any] = Field(default_factory=dict)


class DashboardCreate(BaseModel):
    name: str
    description: Optional[str] = ""


class WidgetCreate(BaseModel):
    dashboard_id: str
    device_id: Optional[str] = None
    type: Optional[str] = "telemetry"
    label: Optional[str] = None
    value: Optional[Any] = None
    config: Dict[str, Any] = Field(default_factory=dict)

    @validator("dashboard_id")
    def validate_dashboard_id(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid dashboard ID")
        return v


class LedScheduleCreate(BaseModel):
    state: bool = Field(..., description="Target LED state")
    execute_at: datetime = Field(..., description="IST datetime to apply the state")
    label: Optional[str] = Field(
        None, max_length=100, description="Optional label for the scheduled task"
    )

    @validator("execute_at")
    def validate_execute_at(cls, v: datetime) -> datetime:
        # Convert to UTC for storage if timezone-aware, otherwise assume IST
        if v.tzinfo is None:
            # Assume input is in IST if no timezone info
            if ZoneInfo_available:
                v = datetime.fromtimestamp(v.timestamp(), tz=IST)
            else:
                v = IST.localize(v)
        else:
            # If timezone-aware, ensure it's in IST
            if ZoneInfo_available:
                if v.tzinfo != IST:
                    v = v.astimezone(IST)
            else:
                # For pytz, need to check differently
                if v.tzinfo != IST:
                    v = v.astimezone(IST)
        
        # Ensure both are timezone-aware before comparison
        now_ist = get_ist_now()
        # Both should be timezone-aware now, safe to compare
        if v <= now_ist:
            raise ValueError("Schedule time must be in the future")
        
        # Return UTC for storage (timezone-naive UTC)
        utc_dt = ist_to_utc(v)
        # Return timezone-naive UTC for MongoDB storage
        if utc_dt.tzinfo:
            return utc_dt.replace(tzinfo=None)
        return utc_dt


class LedTimerCreate(BaseModel):
    state: bool = Field(..., description="Target LED state when timer elapses")
    duration_seconds: int = Field(..., gt=0, le=24 * 60 * 60, description="Timer duration in seconds (max 24h)")
    label: Optional[str] = Field(
        None, max_length=100, description="Optional label for the timer"
    )


# Notification storage for SSE
notification_streams: Dict[str, asyncio.Queue] = {}


# -------------------------------
# 🔔 NOTIFICATION HELPERS
# -------------------------------
async def create_notification(
    user_id: ObjectId,
    title: str,
    message: str,
    notification_type: str = "info",
    details: Optional[str] = None,
    device_id: Optional[ObjectId] = None,
    widget_id: Optional[ObjectId] = None,
) -> None:
    """Create and store a notification, then push to SSE streams."""
    now = datetime.utcnow()
    
    notification_doc = {
        "user_id": user_id,
        "title": title,
        "message": message,
        "type": notification_type,  # info, warning, success, error
        "details": details,
        "device_id": device_id,
        "widget_id": widget_id,
        "read": False,
        "created_at": now,
    }
    
    result = await db.notifications.insert_one(notification_doc)
    notification_id = result.inserted_id
    
    # Prepare notification payload for SSE
    notification_payload = {
        "id": str(notification_id),
        "title": title,
        "message": message,
        "type": notification_type,
        "details": details,
        "time": now.strftime("%I:%M %p"),
        "timestamp": now.isoformat(),
        "device_id": str(device_id) if device_id else None,
        "widget_id": str(widget_id) if widget_id else None,
    }
    
    # Push to SSE stream for this user
    user_id_str = str(user_id)
    if user_id_str in notification_streams:
        try:
            await notification_streams[user_id_str].put(notification_payload)
        except Exception as e:
            print(f"Error pushing notification to SSE stream: {e}")
    
    # Also broadcast via WebSocket
    await manager.broadcast(
        user_id_str,
        {
            "type": "notification",
            "notification": notification_payload,
        },
    )


# -------------------------------
# 🔹 Helpers
# -------------------------------
async def compute_next_virtual_pin(dashboard_id: ObjectId) -> str:
    """Return the lowest available virtual pin (v0, v1, ...) for LED widgets on a dashboard.
    Reuses deleted pins (e.g., if v0 is deleted, next widget gets v0, not v1).
    """
    used_pins = set()
    async for widget in db.widgets.find(
        {
            "dashboard_id": dashboard_id,
            "type": "led",
            "config.virtual_pin": {"$regex": r"^v\d+$", "$options": "i"},
        }
    ):
        pin = widget.get("config", {}).get("virtual_pin")
        if isinstance(pin, str) and pin.lower().startswith("v"):
            suffix = pin[1:]
            if suffix.isdigit():
                used_pins.add(int(suffix))
    
    # Find the lowest available index starting from 0
    index = 0
    while index in used_pins:
        index += 1
    return f"v{index}"


def get_ist_now():
    """Get current time in Asia/Kolkata timezone."""
    if ZoneInfo_available:
        return datetime.now(IST)
    else:
        return pytz.UTC.localize(datetime.utcnow()).astimezone(IST)


def utc_to_ist(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to IST."""
    if ZoneInfo_available:
        if utc_dt.tzinfo is None:
            utc_dt = datetime.fromtimestamp(utc_dt.timestamp(), tz=ZoneInfo("UTC"))
        return utc_dt.astimezone(IST)
    else:
        if utc_dt.tzinfo is None:
            utc_dt = pytz.UTC.localize(utc_dt)
        return utc_dt.astimezone(IST)


def ist_to_utc(ist_dt: datetime) -> datetime:
    """Convert IST datetime to UTC."""
    if ZoneInfo_available:
        if ist_dt.tzinfo is None:
            ist_dt = datetime.fromtimestamp(ist_dt.timestamp(), tz=IST)
        return ist_dt.astimezone(ZoneInfo("UTC"))
    else:
        if ist_dt.tzinfo is None:
            ist_dt = IST.localize(ist_dt)
        return ist_dt.astimezone(pytz.UTC)


async def apply_led_state(device_id: ObjectId, state: bool, virtual_pin: Optional[str] = None) -> None:
    """Update LED state for a device, persist telemetry, and broadcast to clients.
    
    Args:
        device_id: The device ObjectId
        state: LED state (True/False)
        virtual_pin: Optional virtual pin identifier (e.g., "v0", "v1"). If None, uses "led" key.
    """
    now = datetime.utcnow()
    payload_state = 1 if state else 0

    # Determine the key to use in telemetry
    led_key = virtual_pin.lower() if virtual_pin else "led"
    
    telemetry = await db.telemetry.find_one({"device_id": device_id, "key": "telemetry_json"})
    if telemetry:
        existing = telemetry.get("value", {}) or {}
        existing[led_key] = payload_state
        # Don't set generic "led" key when using virtual pins to avoid cross-contamination
        await db.telemetry.update_one(
            {"_id": telemetry["_id"]},
            {"$set": {"value": existing, "timestamp": now}},
        )
    else:
        telemetry_data = {led_key: payload_state}
        await db.telemetry.insert_one(
            {
                "device_id": device_id,
                "key": "telemetry_json",
                "value": telemetry_data,
                "timestamp": now,
            }
        )

    # Update individual LED state record if using virtual pin
    if virtual_pin:
        await db.telemetry.update_one(
            {"device_id": device_id, "key": f"led_state_{virtual_pin.lower()}"},
            {"$set": {"value": payload_state, "timestamp": now}},
            upsert=True,
        )
    
    # Also maintain backward compatibility with general led_state
    await db.telemetry.update_one(
        {"device_id": device_id, "key": "led_state"},
        {"$set": {"value": payload_state, "timestamp": now}},
        upsert=True,
    )

    device = await db.devices.find_one({"_id": device_id})
    if device:
        await db.devices.update_one(
            {"_id": device_id},
            {"$set": {"last_active": now}},
        )
        # Only broadcast the specific virtual pin key, not generic "led"
        # This ensures each LED widget only responds to its own virtual pin
        broadcast_data = {led_key: payload_state}
        await manager.broadcast(
            str(device["user_id"]),
            {
                "type": "telemetry_update",
                "device_id": str(device_id),
                "timestamp": now.isoformat(),
                "data": broadcast_data,
            },
        )


async def ensure_led_widget_access(widget_oid: ObjectId, user_id: ObjectId):
    """Validate widget ownership and LED requirements."""
    widget = await db.widgets.find_one({"_id": widget_oid})
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    dashboard = await db.dashboards.find_one(
        {"_id": widget["dashboard_id"], "user_id": user_id}
    )
    if not dashboard:
        raise HTTPException(status_code=403, detail="Access denied")

    if widget.get("type") != "led":
        raise HTTPException(status_code=400, detail="Widget is not an LED widget")

    device_id = widget.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="LED widget is not linked to a device")

    return widget, device_id


# ============================================================
# NEW: PATCH widget (update label / config / value)
# ============================================================
@router.patch("/widgets/{widget_id}")
async def patch_widget(
    widget_id: str,
    body: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
):
    """
    Partial update for a widget (label, config, value).
    Returns updated widget and broadcasts widget_update to the user's websocket clients.
    """
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid widget ID")

    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID")

    widget = await db.widgets.find_one({"_id": widget_oid})
    if not widget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # Verify the widget belongs to one of the user's dashboards
    dashboard = await db.dashboards.find_one({"_id": widget["dashboard_id"], "user_id": user_id})
    if not dashboard:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Allowed fields for partial update
    allowed = {"label", "config", "value"}
    update_fields: Dict[str, Any] = {}
    for k in allowed:
        if k in body:
            update_fields[k] = body[k]

    if not update_fields:
        # Nothing to update
        return {"message": "no_changes", "widget": doc_to_dict(widget)}

    # Lowercase virtual_pin in config if present
    if "config" in update_fields and isinstance(update_fields["config"], dict):
        cfg = dict(update_fields["config"])
        if "virtual_pin" in cfg and isinstance(cfg["virtual_pin"], str):
            cfg["virtual_pin"] = cfg["virtual_pin"].lower()
        update_fields["config"] = cfg

    update_fields["updated_at"] = datetime.utcnow()

    try:
        await db.widgets.update_one({"_id": widget_oid}, {"$set": update_fields})
        updated_widget = await db.widgets.find_one({"_id": widget_oid})
        widget_payload = doc_to_dict(updated_widget)

        # broadcast widget update to user's websocket clients
        dashboard_id = widget_payload.get("dashboard_id")
        if dashboard_id is None and updated_widget.get("dashboard_id"):
            dashboard_id = str(updated_widget["dashboard_id"])

        await manager.broadcast(
            str(user_id),
            {
                "type": "widget_update",
                "dashboard_id": dashboard_id,
                "widget": widget_payload,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        return widget_payload
    except Exception as e:
        logger.error(f"Failed to patch widget {widget_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update widget")


# ============================================================
# 🚦 LED STATE CONTROL
# ============================================================
@router.post("/widgets/{widget_id}/state", status_code=status.HTTP_200_OK)
async def set_led_state(
    widget_id: str,
    body: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
):
    """
    Set LED widget state (ON/OFF).
    Broadcasts update via WebSocket for real-time UI updates.
    """
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid widget ID"
        )

    user_id = safe_oid(current_user["id"])
    try:
        widget, device_id = await ensure_led_widget_access(widget_oid, user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accessing LED widget {widget_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to access widget"
        )

    desired_state = body.get("state")
    if desired_state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter"
        )

    try:
        bool_state = bool(int(desired_state)) if isinstance(desired_state, (int, str)) else bool(desired_state)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state value. Must be 0/1 or true/false"
        ) from exc

    try:
        # Get virtual pin from widget config
        widget_config = widget.get("config", {}) or {}
        virtual_pin = widget_config.get("virtual_pin")
        
        # Apply LED state (updates database and broadcasts)
        await apply_led_state(device_id, bool_state, virtual_pin)
        
        # Update widget value
        now = datetime.utcnow()
        await db.widgets.update_one(
            {"_id": widget["_id"]},
            {"$set": {"value": 1 if bool_state else 0, "updated_at": now}},
        )

        # Broadcast widget update via WebSocket
        updated_widget = await db.widgets.find_one({"_id": widget["_id"]})
        if updated_widget:
            widget_payload = doc_to_dict(updated_widget)
            dashboard_id = widget_payload.get("dashboard_id")
            if dashboard_id is None and updated_widget.get("dashboard_id"):
                dashboard_id = str(updated_widget["dashboard_id"])
            
            await manager.broadcast(
                str(user_id),
                {
                    "type": "widget_update",
                    "dashboard_id": dashboard_id,
                    "widget": widget_payload,
                    "timestamp": now.isoformat(),
                },
            )
        
        logger.debug(f"LED widget {widget_id} set to {'ON' if bool_state else 'OFF'}")
        return {
            "message": "ok",
            "state": 1 if bool_state else 0,
            "virtual_pin": virtual_pin,
            "timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"Error setting LED state for widget {widget_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update LED state"
        )


# ============================================================
# 🚀 DEVICE ROUTES
# ============================================================
@router.get("/devices")
async def get_devices(current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    devices = []
    async for d in db.devices.find({"user_id": user_id}):
        devices.append(doc_to_dict(d))
    return devices


@router.post("/devices")
async def add_device(device: DeviceCreate, current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    token = secrets.token_hex(16)
    now = datetime.utcnow()

    device_doc = {
        "user_id": user_id,
        "name": device.name,
        "status": "offline",
        "last_active": now,
        "device_token": token,
    }

    result = await db.devices.insert_one(device_doc)
    new_device = await db.devices.find_one({"_id": result.inserted_id})
    return doc_to_dict(new_device)


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str, current_user: dict = Depends(get_current_user)):
    device_oid = safe_oid(device_id)
    if device_oid is None:
        raise HTTPException(status_code=400, detail="Invalid device ID")
    user_id = safe_oid(current_user["id"])
    device = await db.devices.find_one({"_id": device_oid, "user_id": user_id})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await db.telemetry.delete_many({"device_id": device_oid})
    await db.devices.delete_one({"_id": device_oid})
    
    # Broadcast device removal via WebSocket
    await manager.broadcast(
        str(user_id),
        {
            "type": "device_removed",
            "device_id": str(device_oid),
            "data": {"id": str(device_oid)},
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    
    return {"message": "Device deleted"}


# ============================================================
# 📡 TELEMETRY ROUTES
# ============================================================
@router.post("/telemetry")
async def push_telemetry(data: TelemetryData):
    token = data.device_token
    if not token:
        raise HTTPException(status_code=400, detail="Missing device_token")

    device = await db.devices.find_one({"device_token": token})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device_id = device["_id"]
    user_id = str(device["user_id"])
    now = datetime.utcnow()
    payload = data.data or {}

    # 1️⃣ Update telemetry_json record
    telemetry = await db.telemetry.find_one({"device_id": device_id, "key": "telemetry_json"})
    if telemetry:
        existing = telemetry.get("value", {})
        existing.update(payload)
        await db.telemetry.update_one(
            {"_id": telemetry["_id"]},
            {"$set": {"value": existing, "timestamp": now}},
        )
    else:
        await db.telemetry.insert_one(
            {"device_id": device_id, "key": "telemetry_json", "value": payload, "timestamp": now}
        )

    # 2️⃣ Update device online status (with improved logic)
    device_before = await db.devices.find_one({"_id": device_id})
    was_offline = device_before and device_before.get("status") != "online"
    
    await db.devices.update_one(
        {"_id": device_id},
        {"$set": {"status": "online", "last_active": now}},
    )
    
    # Notify if device came back online
    if was_offline:
        device = await db.devices.find_one({"_id": device_id})
        if device:
            await manager.broadcast(
                str(device["user_id"]),
                {
                    "type": "status_update",
                    "device_id": str(device_id),
                    "status": "online",
                    "timestamp": now.isoformat(),
                },
            )
            # Create notification for device coming online
            await create_notification(
                device["user_id"],
                "Device Online",
                f"{device.get('name', 'Device')} is now online",
                "success",
                f"Device reconnected at {now.strftime('%I:%M %p')}",
                device_id,
            )

    # 3️⃣ ✅ Update all widgets linked to this device
    async for widget in db.widgets.find({"device_id": device_id}):
        config = widget.get("config", {})
        key = config.get("key")
        if key and key in payload:
            await db.widgets.update_one(
                {"_id": widget["_id"]},
                {
                    "$set": {
                        "value": payload[key],
                        "updated_at": now
                    }
                }
            )

    # 4️⃣ Broadcast to WebSocket clients for real-time updates
    try:
        await manager.broadcast(
            user_id,
            {
                "type": "telemetry_update",
                "device_id": str(device_id),
                "timestamp": now.isoformat(),
                "data": payload,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast telemetry update for device {device_id}: {e}")

    # 5️⃣ Include LED state if available (for backward compatibility)
    led_state_doc = await db.telemetry.find_one({"device_id": device_id, "key": "led_state"})
    led_state = led_state_doc.get("value") if led_state_doc else None

    logger.debug(f"Telemetry updated for device {device_id}: {len(payload)} keys")
    return {
        "message": "ok",
        "device_id": str(device_id),
        "led": led_state,
        "updated_data": payload,
        "timestamp": now.isoformat(),
    }



@router.get("/telemetry/latest")
async def get_latest_telemetry_by_token(device_token: str):
    device = await db.devices.find_one({"device_token": device_token})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    telemetry = await db.telemetry.find_one({"device_id": device["_id"], "key": "telemetry_json"})
    if not telemetry:
        return {"device_id": str(device["_id"]), "data": {}, "timestamp": None}

    return {
        "device_id": str(device["_id"]),
        "data": telemetry.get("value", {}),
        "timestamp": telemetry.get("timestamp"),
    }


# ============================================================
# 📊 DASHBOARD ROUTES
# ============================================================
@router.post("/dashboards")
async def create_dashboard(data: DashboardCreate, current_user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Dashboard name required")

    doc = {
        "user_id": safe_oid(current_user["id"]),
        "name": name,
        "description": data.description,
        "created_at": now,
    }

    result = await db.dashboards.insert_one(doc)
    saved = await db.dashboards.find_one({"_id": result.inserted_id})
    return doc_to_dict(saved)


@router.get("/dashboards")
async def list_dashboards(current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    dashboards = []
    async for d in db.dashboards.find({"user_id": user_id}).sort("created_at", -1):
        dashboards.append(doc_to_dict(d))
    return dashboards


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, current_user: dict = Depends(get_current_user)):
    dashboard_oid = safe_oid(dashboard_id)
    user_oid = safe_oid(current_user["id"])
    if dashboard_oid is None:
        raise HTTPException(status_code=400, detail="Invalid dashboard ID")

    dashboard = await db.dashboards.find_one({"_id": dashboard_oid, "user_id": user_oid})
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    await db.widgets.delete_many({"dashboard_id": dashboard_oid})
    await db.dashboards.delete_one({"_id": dashboard_oid})
    return {"message": "Dashboard deleted"}


# ============================================================
# 🧩 WIDGET ROUTES
# ============================================================
@router.post("/widgets")
async def create_widget(widget: WidgetCreate, current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    dashboard_id = safe_oid(widget.dashboard_id)
    if user_id is None or dashboard_id is None:
        raise HTTPException(status_code=400, detail="Invalid user/dashboard ID")

    dashboard = await db.dashboards.find_one({"_id": dashboard_id, "user_id": user_id})
    if not dashboard:
        raise HTTPException(status_code=403, detail="Access denied")

    config = dict(widget.config or {})
    if widget.type == "led":
        existing_pin = config.get("virtual_pin")
        if not existing_pin:
            config["virtual_pin"] = await compute_next_virtual_pin(dashboard_id)
        else:
            config["virtual_pin"] = existing_pin.lower()
        config.setdefault("schedules", [])

    doc = {
        "dashboard_id": dashboard_id,
        "device_id": safe_oid(widget.device_id) if widget.device_id else None,
        "type": widget.type,
        "label": widget.label,
        "value": widget.value,
        "config": config,
    }

    result = await db.widgets.insert_one(doc)
    new_widget = await db.widgets.find_one({"_id": result.inserted_id})
    return doc_to_dict(new_widget)



@router.get("/widgets/{dashboard_id}")
async def get_widgets(dashboard_id: str):
    dashboard = await db.dashboards.find_one({"_id": ObjectId(dashboard_id)})
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widgets = []
    async for widget in db.widgets.find({"dashboard_id": ObjectId(dashboard_id)}):
        device_id = widget.get("device_id")
        config: Dict[str, Any] = dict(widget.get("config", {}) or {})
        key = config.get("key")
        virtual_pin = config.get("virtual_pin")

        # Default value
        value = widget.get("value")

        # ✅ Try to get latest telemetry if device_id & key exist
        if device_id and key:
            telemetry = await db.telemetry.find_one(
                {"device_id": ObjectId(device_id), "key": "telemetry_json"}
            )
            if telemetry and isinstance(telemetry.get("value"), dict):
                telemetry_value = telemetry["value"].get(key)
                if telemetry_value is not None:
                    value = telemetry_value

        if widget.get("type") == "led":
            if isinstance(virtual_pin, str):
                config["virtual_pin"] = virtual_pin.lower()
            next_schedule_doc = await db.led_schedules.find_one(
                {"widget_id": widget["_id"], "status": "pending"},
                sort=[("execute_at", 1)],
            )
            if next_schedule_doc:
                # Convert UTC to IST for display
                execute_at_utc = next_schedule_doc["execute_at"]
                if isinstance(execute_at_utc, datetime):
                    if execute_at_utc.tzinfo is None:
                        if ZoneInfo_available:
                            execute_at_utc = datetime.fromtimestamp(execute_at_utc.timestamp(), tz=ZoneInfo("UTC"))
                        else:
                            execute_at_utc = pytz.UTC.localize(execute_at_utc)
                    execute_at_ist = utc_to_ist(execute_at_utc)
                    widget["next_schedule"] = execute_at_ist.isoformat()
                else:
                    widget["next_schedule"] = execute_at_utc

        # ✅ Update widget's value and convert ObjectIds to str
        widget["value"] = value
        widget["_id"] = str(widget["_id"])
        widget["dashboard_id"] = str(widget["dashboard_id"])
        if widget.get("device_id"):
            widget["device_id"] = str(widget["device_id"])
        widget["config"] = config

        widgets.append(widget)

    return widgets



@router.delete("/widgets/{widget_id}")
async def delete_widget(widget_id: str, current_user: dict = Depends(get_current_user)):
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(status_code=400, detail="Invalid widget ID")

    user_id = safe_oid(current_user["id"])
    widget = await db.widgets.find_one({"_id": widget_oid})
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    dashboard = await db.dashboards.find_one({"_id": widget["dashboard_id"], "user_id": user_id})
    if not dashboard:
        raise HTTPException(status_code=403, detail="Access denied")

    dashboard_id = str(widget["dashboard_id"])
    await db.widgets.delete_one({"_id": widget_oid})
    
    # Broadcast widget deletion via WebSocket
    await manager.broadcast(
        str(user_id),
        {
            "type": "widget_deleted",
            "widget_id": widget_id,
            "dashboard_id": dashboard_id,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    
    return {"message": "Widget deleted"}


# ============================================================
# ⏰ LED SCHEDULING
# ============================================================
@router.post("/widgets/{widget_id}/schedule")
async def create_led_schedule(
    widget_id: str,
    schedule: LedScheduleCreate,
    current_user: dict = Depends(get_current_user),
):
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(status_code=400, detail="Invalid widget ID")

    user_id = safe_oid(current_user["id"])
    widget, device_id = await ensure_led_widget_access(widget_oid, user_id)

    schedule_doc = {
        "widget_id": widget_oid,
        "device_id": device_id,
        "dashboard_id": widget["dashboard_id"],
        "state": schedule.state,
        "execute_at": schedule.execute_at,
        "status": "pending",
        "label": schedule.label,
        "created_at": datetime.utcnow(),
        "created_by": user_id,
    }

    result = await db.led_schedules.insert_one(schedule_doc)
    # Convert back to IST for response
    execute_at_ist = utc_to_ist(schedule.execute_at)
    return {
        "message": "scheduled",
        "schedule_id": str(result.inserted_id),
        "execute_at": execute_at_ist.isoformat(),
        "execute_at_ist": execute_at_ist.isoformat(),
        "state": schedule.state,
    }


@router.post("/widgets/{widget_id}/timer")
async def create_led_timer(
    widget_id: str,
    timer: LedTimerCreate,
    current_user: dict = Depends(get_current_user),
):
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(status_code=400, detail="Invalid widget ID")

    user_id = safe_oid(current_user["id"])
    widget, device_id = await ensure_led_widget_access(widget_oid, user_id)

    # Calculate execute_at in IST, then convert to UTC for storage
    now_ist = get_ist_now()
    execute_at_ist = now_ist + timedelta(seconds=timer.duration_seconds)
    execute_at = ist_to_utc(execute_at_ist)
    schedule_doc = {
        "widget_id": widget_oid,
        "device_id": device_id,
        "dashboard_id": widget["dashboard_id"],
        "state": timer.state,
        "execute_at": execute_at,
        "status": "pending",
        "label": timer.label or "Timer",
        "created_at": datetime.utcnow(),
        "created_by": user_id,
        "duration_seconds": timer.duration_seconds,
    }

    result = await db.led_schedules.insert_one(schedule_doc)
    # Convert back to IST for response
    execute_at_ist = utc_to_ist(execute_at)
    return {
        "message": "timer_scheduled",
        "schedule_id": str(result.inserted_id),
        "execute_at": execute_at_ist.isoformat(),
        "execute_at_ist": execute_at_ist.isoformat(),
        "state": timer.state,
        "duration_seconds": timer.duration_seconds,
    }


# ============================================================
# 📅 LED SCHEDULE MANAGEMENT
# ============================================================
@router.get("/widgets/{widget_id}/schedule")
async def list_led_schedules(
    widget_id: str,
    current_user: dict = Depends(get_current_user),
):
    widget_oid = safe_oid(widget_id)
    if widget_oid is None:
        raise HTTPException(status_code=400, detail="Invalid widget ID")

    user_id = safe_oid(current_user["id"])
    await ensure_led_widget_access(widget_oid, user_id)

    schedules: List[Dict[str, Any]] = []
    cursor = db.led_schedules.find({"widget_id": widget_oid}).sort("execute_at", 1)
    async for sched in cursor:
        sched_dict = doc_to_dict(sched)
        # Convert execute_at from UTC to IST for display
        if sched_dict.get("execute_at"):
            if isinstance(sched_dict["execute_at"], str):
                # Parse ISO string
                try:
                    utc_dt = datetime.fromisoformat(sched_dict["execute_at"].replace("Z", "+00:00"))
                except:
                    utc_dt = datetime.fromisoformat(sched_dict["execute_at"])
            else:
                utc_dt = sched_dict["execute_at"]
            if utc_dt.tzinfo is None:
                if ZoneInfo_available:
                    utc_dt = datetime.fromtimestamp(utc_dt.timestamp(), tz=ZoneInfo("UTC"))
                else:
                    utc_dt = pytz.UTC.localize(utc_dt)
            ist_dt = utc_to_ist(utc_dt)
            sched_dict["execute_at"] = ist_dt.isoformat()
            sched_dict["execute_at_ist"] = ist_dt.isoformat()
        schedules.append(sched_dict)
    return {"schedules": schedules}


@router.delete("/widgets/{widget_id}/schedule/{schedule_id}")
async def cancel_led_schedule(
    widget_id: str,
    schedule_id: str,
    current_user: dict = Depends(get_current_user),
):
    widget_oid = safe_oid(widget_id)
    schedule_oid = safe_oid(schedule_id)
    if widget_oid is None or schedule_oid is None:
        raise HTTPException(status_code=400, detail="Invalid ID")

    user_id = safe_oid(current_user["id"])
    widget, _ = await ensure_led_widget_access(widget_oid, user_id)
    dashboard_id = str(widget["dashboard_id"])

    result = await db.led_schedules.update_one(
        {"_id": schedule_oid, "widget_id": widget_oid, "status": "pending"},
        {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Schedule not found or already processed")

    await manager.broadcast(
        str(user_id),
        {
            "type": "led_schedule_cancelled",
            "widget_id": widget_id,
            "dashboard_id": dashboard_id,
            "schedule_id": schedule_id,
        },
    )

    return {"message": "cancelled"}


# ============================================================
# 🕒 LED SCHEDULE EXECUTION WORKER
# ============================================================
async def led_schedule_worker():
    """
    Background worker to execute pending LED schedules.
    Runs continuously, checking for schedules that need to be executed.
    """
    logger.info("LED schedule worker started")
    while True:
        try:
            now_utc = datetime.utcnow()
            cursor = db.led_schedules.find(
                {"status": "pending", "execute_at": {"$lte": now_utc}}
            ).sort("execute_at", 1)

            executed_count = 0
            async for schedule in cursor:
                schedule_id = schedule["_id"]
                widget_id = schedule.get("widget_id")
                device_id = schedule.get("device_id")
                created_by = schedule.get("created_by")

                if not widget_id or not device_id:
                    await db.led_schedules.update_one(
                        {"_id": schedule_id},
                        {
                            "$set": {
                                "status": "failed",
                                "error": "Missing widget/device reference",
                                "executed_at": now,
                            }
                        },
                    )
                    continue

                widget_oid = widget_id if isinstance(widget_id, ObjectId) else safe_oid(str(widget_id))
                device_oid = device_id if isinstance(device_id, ObjectId) else safe_oid(str(device_id))
                if widget_oid is None or device_oid is None:
                    await db.led_schedules.update_one(
                        {"_id": schedule_id},
                        {
                            "$set": {
                                "status": "failed",
                                "error": "Invalid widget/device reference",
                                "executed_at": now,
                            }
                        },
                    )
                    continue

                widget = await db.widgets.find_one({"_id": widget_oid})
                if not widget:
                    await db.led_schedules.update_one(
                        {"_id": schedule_id},
                        {
                            "$set": {
                                "status": "failed",
                                "error": "Widget missing",
                                "executed_at": now,
                            }
                        },
                    )
                    continue

                try:
                    # Get virtual pin from widget config
                    widget_config = widget.get("config", {}) or {}
                    virtual_pin = widget_config.get("virtual_pin")
                    schedule_label = schedule.get("label", "LED Schedule")
                    schedule_state = bool(schedule.get("state"))
                    duration_seconds = schedule.get("duration_seconds")
                    
                    # Apply LED state (updates database and broadcasts)
                    await apply_led_state(device_oid, schedule_state, virtual_pin)
                    
                    # Update widget value
                    await db.widgets.update_one(
                        {"_id": widget_oid},
                        {"$set": {"value": 1 if schedule_state else 0, "updated_at": now_utc}},
                    )
                    
                    # Broadcast widget update
                    updated_widget = await db.widgets.find_one({"_id": widget_oid})
                    if updated_widget and created_by:
                        widget_payload = doc_to_dict(updated_widget)
                        dashboard_id = widget_payload.get("dashboard_id")
                        if dashboard_id is None and updated_widget.get("dashboard_id"):
                            dashboard_id = str(updated_widget["dashboard_id"])
                        
                        try:
                            await manager.broadcast(
                                str(created_by),
                                {
                                    "type": "widget_update",
                                    "dashboard_id": dashboard_id,
                                    "widget": widget_payload,
                                    "timestamp": now_utc.isoformat(),
                                },
                            )
                        except Exception as e:
                            logger.warning(f"Failed to broadcast widget update: {e}")
                    
                    # Create notification for schedule execution
                    device = await db.devices.find_one({"_id": device_oid})
                    if device and created_by:
                        notification_type = "success" if schedule_state else "info"
                        state_text = "ON" if schedule_state else "OFF"
                        schedule_type = "Timer" if duration_seconds else "Schedule"
                        
                        try:
                            await create_notification(
                                created_by,
                                f"LED {schedule_type} Executed",
                                f"LED turned {state_text}: {schedule_label}",
                                notification_type,
                                f"Widget: {widget.get('label', 'LED')}\nDevice: {device.get('name', 'Device')}\nVirtual Pin: {virtual_pin or 'N/A'}\nExecuted at: {now_utc.strftime('%I:%M %p')}",
                                device_oid,
                                widget_oid,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to create notification: {e}")

                    # Mark schedule as completed
                    await db.led_schedules.update_one(
                        {"_id": schedule_id},
                        {
                            "$set": {
                                "status": "completed",
                                "executed_at": now_utc,
                            }
                        },
                    )

                    # Broadcast schedule execution event
                    if created_by:
                        dashboard_id = str(updated_widget["dashboard_id"]) if updated_widget and updated_widget.get("dashboard_id") else None
                        try:
                            await manager.broadcast(
                                str(created_by),
                                {
                                    "type": "led_schedule_executed",
                                    "widget_id": str(widget_oid),
                                    "dashboard_id": dashboard_id,
                                    "schedule_id": str(schedule_id),
                                    "state": bool(schedule.get("state")),
                                    "executed_at": now_utc.isoformat(),
                                },
                            )
                        except Exception as e:
                            logger.warning(f"Failed to broadcast schedule execution: {e}")
                    
                    executed_count += 1
                    logger.debug(f"LED schedule {schedule_id} executed successfully")
                    
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error(f"Error executing LED schedule {schedule_id}: {exc}", exc_info=True)
                    await db.led_schedules.update_one(
                        {"_id": schedule_id},
                        {
                            "$set": {
                                "status": "failed",
                                "error": str(exc),
                                "executed_at": datetime.utcnow(),
                            }
                        },
                    )
            
            if executed_count > 0:
                logger.info(f"LED schedule worker executed {executed_count} schedule(s)")
                
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"LED schedule worker error: {exc}", exc_info=True)

        await asyncio.sleep(1)


# ============================================================
# 🔔 NOTIFICATION ROUTES
# ============================================================
@router.get("/notifications/health")
async def notifications_health():
    """Health check for notifications endpoint."""
    return {"status": "ok", "message": "Notifications routes are active"}


@router.get("/notifications")
async def get_notifications(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    unread_only: bool = False,
):
    """Get notifications for the current user."""
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    query = {"user_id": user_id}
    if unread_only:
        query["read"] = False
    
    notifications = []
    cursor = db.notifications.find(query).sort("created_at", -1).limit(limit)
    async for notif in cursor:
        notifications.append(doc_to_dict(notif))
    
    return {"notifications": notifications}


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a notification as read."""
    user_id = safe_oid(current_user["id"])
    notif_oid = safe_oid(notification_id)
    
    if user_id is None or notif_oid is None:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    result = await db.notifications.update_one(
        {"_id": notif_oid, "user_id": user_id},
        {"$set": {"read": True, "read_at": datetime.utcnow()}},
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "marked as read"}


@router.put("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: dict = Depends(get_current_user),
):
    """Mark all notifications as read for the current user."""
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    await db.notifications.update_many(
        {"user_id": user_id, "read": False},
        {"$set": {"read": True, "read_at": datetime.utcnow()}},
    )
    
    return {"message": "all notifications marked as read"}


@router.get("/notifications/stream")
async def notification_stream(request: Request, current_user: dict = Depends(get_current_user)):
    """Server-Sent Events (SSE) stream for real-time notifications."""
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    user_id_str = str(user_id)
    
    # Create a queue for this user if it doesn't exist
    if user_id_str not in notification_streams:
        notification_streams[user_id_str] = asyncio.Queue()
    
    async def event_generator():
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'message': 'Notification stream connected'})}\n\n"
            
            # Send recent unread notifications
            recent_notifications = []
            async for notif in db.notifications.find(
                {"user_id": user_id, "read": False}
            ).sort("created_at", -1).limit(10):
                notif_dict = doc_to_dict(notif)
                notif_dict["time"] = notif_dict.get("created_at", "").split("T")[1][:5] if notif_dict.get("created_at") else ""
                recent_notifications.append(notif_dict)
            
            if recent_notifications:
                yield f"data: {json.dumps({'type': 'initial', 'notifications': recent_notifications})}\n\n"
            
            # Keep connection alive and send new notifications
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for notification with timeout
                    notification = await asyncio.wait_for(
                        notification_streams[user_id_str].get(),
                        timeout=30.0
                    )
                    yield f"data: {json.dumps({'type': 'notification', 'notification': notification})}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup: remove queue if empty (optional, can keep for reconnection)
            if user_id_str in notification_streams:
                # Don't delete immediately, allow reconnection
                pass
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# ⏱ AUTO-OFFLINE BACKGROUND TASK
# ============================================================
async def auto_offline_checker():
    """Automatically set devices offline after timeout"""
    while True:
        try:
            now = datetime.utcnow()
            async for device in db.devices.find({"status": "online"}):
                last_active = device.get("last_active")
                if last_active and (now - last_active).total_seconds() > OFFLINE_TIMEOUT:
                    await db.devices.update_one(
                        {"_id": device["_id"]},
                        {"$set": {"status": "offline"}},
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
                    # Create notification for device going offline
                    await create_notification(
                        device["user_id"],
                        "Device Offline",
                        f"{device.get('name', 'Device')} is now offline",
                        "warning",
                        f"Device last seen: {last_active.strftime('%I:%M %p') if last_active else 'Unknown'}\nTimeout: {OFFLINE_TIMEOUT} seconds",
                        device["_id"],
                    )
        except Exception as e:
            print("auto-offline error:", e)
        await asyncio.sleep(OFFLINE_TIMEOUT)
