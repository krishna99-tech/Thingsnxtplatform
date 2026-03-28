"""Public app configuration consumed by mobile/web clients (no secrets)."""

import os
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from db import db, doc_to_dict

router = APIRouter(prefix="/app", tags=["App configuration"])

PLATFORM_DOC_ID = "global"


def _default_public_config() -> Dict[str, Any]:
    return {
        "branding": {
            "app_display_name": os.getenv("APP_NAME", "ThingsNXT"),
            "company_name": os.getenv("COMPANY_NAME", "ThingsNXT"),
            "copyright_text": os.getenv("COPYRIGHT_TEXT", ""),
            "support_email": os.getenv("EMAIL_FROM", "") or os.getenv("SUPPORT_EMAIL", ""),
            "frontend_url": os.getenv("FRONTEND_URL", ""),
        },
        "mobile_app": {
            "maintenance_mode": False,
            "maintenance_message": "",
            "maintenance_blocking": False,
            "min_app_version": "",
            "feature_flags": {
                "connected_apps": True,
                "webhooks": True,
                "kafka_pipeline_card": True,
            },
        },
    }


@router.get("/config")
async def get_public_app_config():
    """
    Read-only platform settings for the ThingsNXT mobile app and other clients.
    Admin updates these via PUT /admin/platform-settings.
    """
    doc = await db.platform_settings.find_one({"_id": PLATFORM_DOC_ID})
    defaults = _default_public_config()
    if not doc:
        return defaults

    d = doc_to_dict(doc)
    out = {"branding": {**defaults["branding"], **(d.get("branding") or {})}}
    ma = d.get("mobile_app") or {}
    out["mobile_app"] = {
        **defaults["mobile_app"],
        **ma,
        "feature_flags": {
            **defaults["mobile_app"]["feature_flags"],
            **(ma.get("feature_flags") or {}),
        },
    }
    updated = d.get("updated_at")
    if updated:
        out["updated_at"] = updated.isoformat() if hasattr(updated, "isoformat") else str(updated)
    return out
