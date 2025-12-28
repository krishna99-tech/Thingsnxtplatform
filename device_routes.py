from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from bson import ObjectId
from datetime import datetime, timedelta
import asyncio
import secrets
import json
import logging
import httpx
import hmac
import hashlib
from typing import Optional, Dict, Any, List

from db import db
from utils import (
    OFFLINE_TIMEOUT, 
    doc_to_dict, 
    get_ist_now, 
    utc_to_ist, 
    ist_to_utc, 
    IST, 
    ZoneInfo_available,
    ZoneInfo
)
from websocket_manager import manager
from event_manager import event_manager # 👈 Import the global event manager
from auth_routes import get_current_user
from rules_engine import rules_engine # 👈 Import the new rules engine
from models import (
    DeviceCreate,
    TelemetryData,
    DashboardCreate,
    WidgetLayout,
    DashboardLayoutUpdate,
    WidgetCreate,
    LedScheduleCreate,
    LedTimerCreate,
    WebhookCreate,
    DeviceBulkStatusUpdate
)

logger = logging.getLogger(__name__)



router = APIRouter(tags=["Devices", "Telemetry", "Dashboards", "Widgets"])


# -------------------------------
# 🔹 Helper: Safe ObjectId convert
# -------------------------------
def safe_oid(value: str) -> Optional[ObjectId]:
    return ObjectId(value) if ObjectId.is_valid(value) else None


# ============================================================
# 🔒 FIREBASE-LIKE SECURITY RULES ENGINE
# ============================================================
# This class now delegates logic to rules_engine.py which loads
# rules from security_rules.json.
# ============================================================
class SecurityRules:
    @staticmethod
    async def verify_ownership(collection, resource_id: str, user_id: ObjectId, resource_name: str = "Resource") -> Dict[str, Any]:
        """
        Enforces .read/.write rule: auth.uid === resource.user_id
        """
        oid = safe_oid(resource_id)
        if not oid:
            raise HTTPException(status_code=400, detail=f"Invalid {resource_name} ID")

        resource = await collection.find_one({"_id": oid})
        if not resource:
            raise HTTPException(status_code=404, detail=f"{resource_name} not found")

        # Determine collection name for rules lookup (e.g. db.devices -> "devices")
        collection_name = collection.name
        
        is_allowed = await rules_engine.validate_rule(collection_name, ".write", user_id, resource)
        if not is_allowed:
             raise HTTPException(status_code=403, detail="Access denied by security rules")

        return resource

    @staticmethod
    async def verify_device_token(token: str) -> Dict[str, Any]:
        """
        Enforces telemetry .write rule: device_token === resource.token
        """
        if not token:
            raise HTTPException(status_code=400, detail="Missing device_token")
        device = await db.devices.find_one({"device_token": token})
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # Validate telemetry write rule using rules engine
        # Telemetry can be written by device owner OR by device token
        device_dict = doc_to_dict(device)
        is_allowed = await rules_engine.validate_rule(
            "telemetry", 
            ".write", 
            device_dict.get("user_id"), 
            device_dict,
            {"device_token": token}
        )
        if not is_allowed:
            raise HTTPException(status_code=403, detail="Access denied by security rules")
        
        return device


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
# 🔹 WEBHOOK HELPERS
# -------------------------------
async def trigger_webhooks(device_id: ObjectId, user_id: ObjectId, event_type: str, payload: Dict[str, Any]):
    """
    Trigger webhooks for a given event.
    Webhooks are filtered by user_id, device_id (if specified), and event_type.
    """
    try:
        # Find all active webhooks for this user
        query = {
            "user_id": user_id,
            "active": True,
            "events": {"$in": [event_type, "all"]}
        }
        
        # If device_id is provided, also check for device-specific webhooks
        if device_id:
            query["$or"] = [
                {"device_id": None},  # Global webhooks
                {"device_id": device_id}  # Device-specific webhooks
            ]
        
        async for webhook in db.webhooks.find(query):
            webhook_url = webhook.get("url")
            if not webhook_url:
                continue
            
            webhook_secret = webhook.get("secret")
            webhook_id = webhook.get("_id")
            
            # Prepare webhook payload
            webhook_payload = {
                "event": event_type,
                "timestamp": datetime.utcnow().isoformat(),
                "data": payload,
            }
            
            # Add signature if secret is provided
            headers = {"Content-Type": "application/json"}
            if webhook_secret:
                signature = hmac.new(
                    webhook_secret.encode(),
                    json.dumps(webhook_payload).encode(),
                    hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"
            
            # Send webhook asynchronously (fire and forget)
            asyncio.create_task(send_webhook(webhook_id, webhook_url, webhook_payload, headers))
            
    except Exception as e:
        logger.error(f"Error triggering webhooks: {e}", exc_info=True)


async def send_webhook(webhook_id: ObjectId, url: str, payload: Dict[str, Any], headers: Dict[str, str]):
    """
    Send a webhook HTTP POST request.
    Updates webhook stats on success/failure.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            # Update webhook stats
            await db.webhooks.update_one(
                {"_id": webhook_id},
                {
                    "$set": {
                        "last_triggered": datetime.utcnow(),
                        "last_status": "success",
                    },
                    "$inc": {"trigger_count": 1}
                }
            )
            logger.debug(f"Webhook {webhook_id} sent successfully to {url}")
            
    except httpx.TimeoutException:
        logger.warning(f"Webhook {webhook_id} timeout for {url}")
        await db.webhooks.update_one(
            {"_id": webhook_id},
            {
                "$set": {"last_status": "timeout"},
                "$inc": {"error_count": 1}
            }
        )
    except Exception as e:
        logger.error(f"Webhook {webhook_id} failed for {url}: {e}")
        await db.webhooks.update_one(
            {"_id": webhook_id},
            {
                "$set": {"last_status": "error", "last_error": str(e)},
                "$inc": {"error_count": 1}
            }
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
    # 1. Verify Widget Exists
    widget = await db.widgets.find_one({"_id": widget_oid})
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    # 2. Verify Dashboard Ownership (Rule: auth.uid == dashboard.user_id)
    await SecurityRules.verify_ownership(db.dashboards, str(widget["dashboard_id"]), user_id, "Dashboard")
    
    # 3. Verify Widget Type
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
    widget = await db.widgets.find_one({"_id": widget_oid})
    if not widget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # Rule: Write access requires Dashboard ownership
    await SecurityRules.verify_ownership(db.dashboards, str(widget["dashboard_id"]), user_id, "Dashboard")

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
    # Rule: .read (List only own devices)
    devices = []
    async for d in db.devices.find({"user_id": user_id}):
        devices.append(doc_to_dict(d))
    return devices


@router.post("/devices")
async def add_device(device: DeviceCreate, current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    # Rule: .write (Create device for self)
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
    
    # Convert to dict to ensure `id` field is a string for broadcasting
    new_device_dict = doc_to_dict(new_device)

    # Broadcast device addition via global SSE
    asyncio.create_task(event_manager.broadcast({
        "type": "device_added",
        "data": new_device_dict,
        "timestamp": now.isoformat()
    }))

    return new_device_dict


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str, current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    # Rule: .write (Delete own device)
    device = await SecurityRules.verify_ownership(db.devices, device_id, user_id, "Device")
    device_oid = device["_id"]

    await db.telemetry.delete_many({"device_id": device_oid})
    await db.devices.delete_one({"_id": device_oid})
    
    # Broadcast device removal via global SSE
    asyncio.create_task(event_manager.broadcast({
        "type": "device_removed",
        "device_id": str(device_oid),
        "timestamp": datetime.utcnow().isoformat()
    }))
    
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


@router.patch("/devices/bulk/status")
async def bulk_update_device_status(
    payload: DeviceBulkStatusUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Bulk update the status of multiple devices.
    """
    user_id = safe_oid(current_user["id"])
    
    # Filter valid ObjectIds
    device_oids = [safe_oid(did) for did in payload.device_ids if safe_oid(did)]
    
    if not device_oids:
        raise HTTPException(status_code=400, detail="No valid device IDs provided")

    now = datetime.utcnow()

    # Update only devices belonging to this user
    result = await db.devices.update_many(
        {"_id": {"$in": device_oids}, "user_id": user_id},
        {"$set": {"status": payload.status, "last_active": now}}
    )

    # Broadcast updates to the user via WebSocket
    # We iterate to send individual updates to maintain compatibility with existing frontend listeners
    for oid in device_oids:
        await manager.broadcast(
            str(user_id),
            {
                "type": "status_update",
                "device_id": str(oid),
                "status": payload.status,
                "timestamp": now.isoformat(),
            },
        )
        
        # Broadcast to global SSE event manager
        asyncio.create_task(event_manager.broadcast({
            "type": "status_update",
            "device_id": str(oid),
            "status": payload.status,
            "timestamp": now.isoformat()
        }))

    return {"message": "Devices updated", "modified_count": result.modified_count}


# ============================================================
# 📡 TELEMETRY ROUTES
# ============================================================
@router.post("/telemetry")
async def push_telemetry(data: TelemetryData):
    token = data.device_token
    # Rule: .write (Allow if token matches)
    device = await SecurityRules.verify_device_token(token)
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
    
    # Broadcast status update via WebSocket immediately
    await manager.broadcast(
        user_id,
        {
            "type": "status_update",
            "device_id": str(device_id),
            "status": "online",
            "timestamp": now.isoformat(),
        },
    )
    
    # Notify if device came back online
    if was_offline:
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
        
        # Also broadcast via global SSE event manager
        asyncio.create_task(event_manager.broadcast({
            "type": "telemetry_update",
            "device_id": str(device_id),
            "timestamp": now.isoformat(),
            "data": payload,
        }))
    except Exception as e:
        logger.error(f"Failed to broadcast telemetry update: {e}")

    # 5️⃣ Trigger webhooks if configured
    try:
        await trigger_webhooks(device_id, user_id, "telemetry_update", {
            "device_id": str(device_id),
            "timestamp": now.isoformat(),
            "data": payload,
        })
    except Exception as e:
        logger.error(f"Failed to trigger webhooks: {e}")

    logger.debug(f"Telemetry updated for device {device_id}: {len(payload)} keys")
    
    # Extract LED state if present
    led_state = payload.get("led") or payload.get("v0") or 0
    
    return {
        "message": "ok",
        "device_id": str(device_id),
        "led": led_state,
        "updated_data": payload, # This is for the device's response, not the broadcast
        "timestamp": now.isoformat(),
    }



@router.get("/telemetry/latest")
async def get_latest_telemetry_by_token(device_token: str):
    # Rule: .read (Allow if token matches)
    device = await SecurityRules.verify_device_token(device_token)
    
    telemetry = await db.telemetry.find_one({"device_id": device["_id"], "key": "telemetry_json"})
    if not telemetry:
        return {"device_id": str(device["_id"]), "data": {}, "timestamp": None}

    return {
        "device_id": str(device["_id"]),
        "data": telemetry.get("value", {}),
        "timestamp": telemetry.get("timestamp"),
    }


# ============================================================
# 📈 TELEMETRY HISTORY (for charts)
# ============================================================
@router.get("/telemetry/history")
async def get_telemetry_history(
    device_id: str,
    key: str,
    period: str = "24h",
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch historical telemetry data.
    """
    user_id = safe_oid(current_user["id"])
    # Rule: .read (Owner only)
    device = await SecurityRules.verify_ownership(db.devices, device_id, user_id, "Device")
    device_oid = device["_id"]

    # For this example, we'll query the main telemetry JSON record.
    # A more robust solution would involve a separate collection for historical data.
    # This is a placeholder implementation. A real implementation would query a
    # time-series collection. We will simulate this by returning the latest value.
    
    telemetry_record = await db.telemetry.find_one(
        {"device_id": device_oid, "key": "telemetry_json"}
    )

    current_value = telemetry_record.get("value", {}).get(key) if telemetry_record else None

    if current_value is None:
        return []

    # In a real app, you would query a time-series collection here.
    # For this demo, we simulate 24 hours of historical data based on the current value.
    import random
    now = datetime.utcnow()
    history = []
    for i in range(24):
        # Simulate some fluctuation around the current value
        simulated_value = current_value + (random.random() - 0.5) * (current_value * 0.1) # +/- 5%
        history.append({"timestamp": now - timedelta(hours=i), "value": simulated_value})
    return history

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
    user_oid = safe_oid(current_user["id"])
    # Rule: .write (Owner only)
    dashboard = await SecurityRules.verify_ownership(db.dashboards, dashboard_id, user_oid, "Dashboard")
    dashboard_oid = dashboard["_id"]
    
    await db.widgets.delete_many({"dashboard_id": dashboard_oid})
    await db.dashboards.delete_one({"_id": dashboard_oid})
    return {"message": "Dashboard deleted"}


@router.put("/dashboards/{dashboard_id}/layout")
async def update_dashboard_layout(
    dashboard_id: str,
    data: DashboardLayoutUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update the order and size of widgets in a dashboard."""
    user_oid = safe_oid(current_user["id"])
    # Rule: .write (Owner only)
    dashboard = await SecurityRules.verify_ownership(db.dashboards, dashboard_id, user_oid, "Dashboard")
    dashboard_oid = dashboard["_id"]

    try:
        # Use a bulk write for efficiency
        from pymongo import UpdateOne

        operations = []
        for index, widget_layout in enumerate(data.layout):
            widget_oid = safe_oid(widget_layout.id)
            if widget_oid:
                operations.append(
                    UpdateOne(
                        {"_id": widget_oid, "dashboard_id": dashboard_oid},
                        {"$set": {
                            "order": index,
                            "width": widget_layout.width,
                            "height": widget_layout.height,
                        }}
                    )
                )
        
        if operations:
            await db.widgets.bulk_write(operations)
        return {"message": "Layout updated successfully"}
    except Exception as e:
        logger.error(f"Error updating dashboard layout for {dashboard_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update layout")

# ============================================================
# 🧩 WIDGET ROUTES
# ============================================================
# ============================================================
# 🧩 WIDGET ROUTES
# ============================================================
@router.post("/widgets")
async def create_widget(widget: WidgetCreate, current_user: dict = Depends(get_current_user)):
    user_id = safe_oid(current_user["id"])
    # Rule: .write (Owner of dashboard only)
    dashboard = await SecurityRules.verify_ownership(db.dashboards, widget.dashboard_id, user_id, "Dashboard")
    dashboard_id = dashboard["_id"]

    config = dict(widget.config or {})

    # LED virtual pin assignment
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


# ============================================================
# 🚫 FIXED: NO TELEMETRY FETCH HERE → NO FLICKER
# ============================================================
@router.get("/widgets/{dashboard_id}")
async def get_widgets(dashboard_id: str):
    dashboard = await db.dashboards.find_one({"_id": ObjectId(dashboard_id)})
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widgets = []
    async for widget in db.widgets.find({"dashboard_id": ObjectId(dashboard_id)}):

        config: Dict[str, Any] = dict(widget.get("config", {}) or {})
        virtual_pin = config.get("virtual_pin")

        # IMPORTANT FIX:
        # DO NOT OVERRIDE widget.value FROM telemetry.
        # Let value be whatever is stored in widget doc.
        value = widget.get("value")

        # LED next schedule fetch (OK to keep)
        if widget.get("type") == "led":
            if isinstance(virtual_pin, str):
                config["virtual_pin"] = virtual_pin.lower()

            next_schedule_doc = await db.led_schedules.find_one(
                {"widget_id": widget["_id"], "status": "pending"},
                sort=[("execute_at", 1)],
            )
            if next_schedule_doc:
                execute_at_utc = next_schedule_doc["execute_at"]

                if isinstance(execute_at_utc, datetime):
                    # utc_to_ist handles naive datetimes by assuming they are UTC
                    execute_at_ist = utc_to_ist(execute_at_utc)
                    widget["next_schedule"] = execute_at_ist.isoformat()
                else:
                    widget["next_schedule"] = execute_at_utc

        # Convert ObjectIds to strings
        widget["_id"] = str(widget["_id"])
        widget["dashboard_id"] = str(widget["dashboard_id"])
        widget["value"] = value
        widget["config"] = config

        if widget.get("device_id"):
            widget["device_id"] = str(widget["device_id"])

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

    # Rule: .write (Owner of dashboard only)
    await SecurityRules.verify_ownership(db.dashboards, str(widget["dashboard_id"]), user_id, "Dashboard")

    dashboard_id = str(widget["dashboard_id"])
    await db.widgets.delete_one({"_id": widget_oid})

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


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Deletes a specific notification for the current user.
    """
    user_id = safe_oid(current_user.get("id"))
    notif_oid = safe_oid(notification_id)

    if not user_id or not notif_oid:
        raise HTTPException(status_code=400, detail="Invalid user or notification ID.")

    # Ensure the notification belongs to the user before deleting
    delete_result = await db.notifications.delete_one({
        "_id": notif_oid,
        "user_id": user_id
    })

    if delete_result.deleted_count == 0:
        # This can happen if the notification doesn't exist or doesn't belong to the user
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or you do not have permission to delete it."
        )

    # On success, FastAPI will return a 204 No Content response.


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
    logger.info("Starting auto-offline checker background task")
    while True:
        try:
            now = datetime.utcnow()
            # Find devices that are marked online
            async for device in db.devices.find({"status": "online"}):
                last_active = device.get("last_active")
                if last_active and (now - last_active).total_seconds() > OFFLINE_TIMEOUT:
                    # 1. Update DB
                    await db.devices.update_one(
                        {"_id": device["_id"]},
                        {"$set": {"status": "offline"}},
                    )

                    # 2. Broadcast to global SSE event manager (Global Stream)
                    asyncio.create_task(event_manager.broadcast({
                        "type": "status_update",
                        "device_id": str(device["_id"]),
                        "status": "offline",
                        "timestamp": now.isoformat()
                    }))

                    # 3. Broadcast status update via WebSocket (User Stream)
                    await manager.broadcast(
                        str(device["user_id"]),
                        {
                            "type": "status_update",
                            "device_id": str(device["_id"]),
                            "status": "offline",
                            "timestamp": now.isoformat(),
                        },
                    )
                    
                    # 4. Create notification for device going offline
                    await create_notification(
                        device["user_id"],
                        "Device Offline",
                        f"{device.get('name', 'Device')} is now offline",
                        "warning",
                        f"Device last seen: {last_active.strftime('%I:%M %p') if last_active else 'Unknown'}\nTimeout: {OFFLINE_TIMEOUT} seconds",
                        device["_id"],
                    )
                    
                    logger.debug(f"Device {device['_id']} set to offline")

        except Exception as exc:
            logger.error(f"Error in auto_offline_checker: {exc}", exc_info=True)
        
        # Sleep for the timeout duration before checking again
        await asyncio.sleep(OFFLINE_TIMEOUT)


# ============================================================
# 🔗 WEBHOOK ROUTES
# ============================================================
@router.post("/webhooks")
async def create_webhook(
    webhook: WebhookCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a webhook to receive real-time device events."""
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    # Validate URL
    if not webhook.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid webhook URL. Must start with http:// or https://")
    
    # Validate device_id if provided
    device_oid = None
    if webhook.device_id:
        device_oid = safe_oid(webhook.device_id)
        # Rule: .read (Verify device ownership)
        await SecurityRules.verify_ownership(db.devices, webhook.device_id, user_id, "Device")
    
    # Generate secret if not provided
    secret = webhook.secret or secrets.token_urlsafe(32)
    
    webhook_doc = {
        "user_id": user_id,
        "url": webhook.url,
        "events": webhook.events or ["telemetry_update"],
        "secret": secret,
        "device_id": device_oid,
        "active": True,
        "created_at": datetime.utcnow(),
        "trigger_count": 0,
        "error_count": 0,
    }
    
    result = await db.webhooks.insert_one(webhook_doc)
    new_webhook = await db.webhooks.find_one({"_id": result.inserted_id})
    return doc_to_dict(new_webhook)


@router.get("/webhooks")
async def list_webhooks(current_user: dict = Depends(get_current_user)):
    """List all webhooks for the current user."""
    user_id = safe_oid(current_user["id"])
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    webhooks = []
    async for webhook in db.webhooks.find({"user_id": user_id}).sort("created_at", -1):
        webhook_dict = doc_to_dict(webhook)
        # Don't expose secret in list
        if "secret" in webhook_dict:
            webhook_dict["secret"] = "***" if webhook_dict.get("secret") else None
        webhooks.append(webhook_dict)
    
    return {"webhooks": webhooks}


@router.get("/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get a specific webhook by ID."""
    user_id = safe_oid(current_user["id"])
    # Rule: .read (Owner only)
    webhook = await SecurityRules.verify_ownership(db.webhooks, webhook_id, user_id, "Webhook")
    
    webhook_dict = doc_to_dict(webhook)
    # Don't expose full secret
    if "secret" in webhook_dict and webhook_dict.get("secret"):
        webhook_dict["secret"] = "***"
    
    return webhook_dict


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a webhook."""
    user_id = safe_oid(current_user["id"])
    # Rule: .write (Owner only)
    webhook = await SecurityRules.verify_ownership(db.webhooks, webhook_id, user_id, "Webhook")
    webhook_oid = webhook["_id"]

    result = await db.webhooks.delete_one({"_id": webhook_oid, "user_id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    return {"message": "Webhook deleted"}


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: str,
    body: Dict[str, Any],
    current_user: dict = Depends(get_current_user),
):
    """Update a webhook (activate/deactivate, change URL, etc.)."""
    user_id = safe_oid(current_user["id"])
    # Rule: .write (Owner only)
    webhook = await SecurityRules.verify_ownership(db.webhooks, webhook_id, user_id, "Webhook")
    webhook_oid = webhook["_id"]
    
    # Allowed fields for update
    allowed = {"url", "events", "active", "secret"}
    update_fields = {}
    for k in allowed:
        if k in body:
            update_fields[k] = body[k]
    
    if not update_fields:
        return {"message": "no_changes", "webhook": doc_to_dict(webhook)}
    
    update_fields["updated_at"] = datetime.utcnow()
    
    await db.webhooks.update_one({"_id": webhook_oid}, {"$set": update_fields})
    updated_webhook = await db.webhooks.find_one({"_id": webhook_oid})
    
    webhook_dict = doc_to_dict(updated_webhook)
    if "secret" in webhook_dict and webhook_dict.get("secret"):
        webhook_dict["secret"] = "***"
    
    return webhook_dict