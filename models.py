from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from bson import ObjectId
from utils import IST, ZoneInfo_available, ist_to_utc, get_ist_now, ZoneInfo


# -------------------------------
# Device Models
# -------------------------------
class DeviceCreate(BaseModel):
    name: Optional[str] = Field("Unnamed Device", max_length=100)


class DeviceUpdate(BaseModel):
    name: Optional[str]
    status: Optional[str]


class DeviceBulkStatusUpdate(BaseModel):
    device_ids: List[str]
    status: str

    @validator("status")
    def validate_status(cls, v):
        if v not in ["online", "offline"]:
            raise ValueError("Status must be either 'online' or 'offline'")
        return v


# -------------------------------
# Telemetry Models
# -------------------------------
class TelemetryData(BaseModel):
    device_token: str
    data: Dict[str, Any] = Field(default_factory=dict)


# -------------------------------
# Dashboard Models
# -------------------------------
class DashboardCreate(BaseModel):
    name: str
    description: Optional[str] = ""


class WidgetLayout(BaseModel):
    id: str
    width: Optional[int] = 1
    height: Optional[int] = 1


class DashboardLayoutUpdate(BaseModel):
    layout: List[WidgetLayout]


# -------------------------------
# Widget Models
# -------------------------------
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


# -------------------------------
# LED Schedule Models
# -------------------------------
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
        if v <= now_ist:
            raise ValueError("Schedule time must be in the future")
        
        # Return UTC for storage (timezone-naive UTC)
        utc_dt = ist_to_utc(v)
        if utc_dt.tzinfo:
            return utc_dt.replace(tzinfo=None)
        return utc_dt


class LedTimerCreate(BaseModel):
    state: bool = Field(..., description="Target LED state when timer elapses")
    duration_seconds: int = Field(..., gt=0, le=24 * 60 * 60, description="Timer duration in seconds (max 24h)")
    label: Optional[str] = Field(
        None, max_length=100, description="Optional label for the timer"
    )


# -------------------------------
# Webhook Models
# -------------------------------
class WebhookCreate(BaseModel):
    url: str = Field(..., description="Webhook URL to receive events")
    events: List[str] = Field(default=["telemetry_update"], description="List of events to subscribe to")
    secret: Optional[str] = Field(None, description="Optional secret for webhook signature")
    device_id: Optional[str] = Field(None, description="Optional device ID to filter events")
