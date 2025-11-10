from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


# -------------------------------
# Device Models
# -------------------------------
class DeviceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)


class DeviceUpdate(BaseModel):
    name: Optional[str]
    status: Optional[str]


# -------------------------------
# Telemetry Models
# -------------------------------
class TelemetryData(BaseModel):
    device_token: str
    data: Dict[str, Any]


# -------------------------------
# Widget Models
# -------------------------------
class WidgetCreate(BaseModel):
    dashboard_id: str
    device_id: Optional[str]
    type: str
    label: str
    config: Optional[Dict[str, Any]] = {}
