from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
import asyncio
import secrets
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any

from db import db
from utils import OFFLINE_TIMEOUT, doc_to_dict
from websocket_manager import manager
from auth_routes import get_current_user



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

    @validator("device_id")
    def validate_device_id(cls, v):
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError("Invalid device ID")
        return v


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

    # 2️⃣ Update device online status
    await db.devices.update_one(
        {"_id": device_id},
        {"$set": {"status": "online", "last_active": now}},
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

    # 4️⃣ Broadcast to WebSocket clients
    await manager.broadcast(
        user_id,
        {
            "type": "telemetry_update",
            "device_id": str(device_id),
            "timestamp": now.isoformat(),
            "data": payload,
        },
    )

    # 5️⃣ Include LED state if available
    led_state_doc = await db.telemetry.find_one({"device_id": device_id, "key": "led_state"})
    led_state = led_state_doc.get("value") if led_state_doc else None

    return {
        "message": "ok",
        "device_id": str(device_id),
        "led": led_state,
        "updated_data": payload,
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

    doc = {
        "dashboard_id": dashboard_id,
        "device_id": safe_oid(widget.device_id) if widget.device_id else None,
        "type": widget.type,
        "label": widget.label,
        "value": widget.value,
        "config": widget.config,
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
        config = widget.get("config", {})
        key = config.get("key")

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

        # ✅ Update widget's value and convert ObjectIds to str
        widget["value"] = value
        widget["_id"] = str(widget["_id"])
        widget["dashboard_id"] = str(widget["dashboard_id"])
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

    dashboard = await db.dashboards.find_one({"_id": widget["dashboard_id"], "user_id": user_id})
    if not dashboard:
        raise HTTPException(status_code=403, detail="Access denied")

    await db.widgets.delete_one({"_id": widget_oid})
    return {"message": "Widget deleted"}


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
        except Exception as e:
            print("auto-offline error:", e)
        await asyncio.sleep(OFFLINE_TIMEOUT)
