# Email Integration Guide for Device Routes

## Quick Reference: Adding Emails to Your IoT Platform

This guide shows you exactly where and how to integrate the new email functions into your existing `device_routes.py`.

## 1. Import Email Functions

Add to the top of `device_routes.py`:

```python
from utils import (
    send_device_status_email,
    send_device_registered_email,
    send_user_alert_email
)
```

## 2. Device Registration Email

### Location: `add_device()` endpoint

**Endpoint:** `POST /devices`
**Line:** ~200-250 in device_routes.py

```python
@router.post("/devices", response_model=dict, tags=["Devices"])
async def add_device(
    device: DeviceCreate,
    background_tasks: BackgroundTasks,  # Add this parameter
    current_user: dict = Depends(get_current_user)
):
    user_id = ObjectId(current_user["id"])

    # Generate device token
    token = secrets.token_urlsafe(32)

    # Create device document
    new_device = {
        "user_id": user_id,
        "name": device.name,
        "token": token,
        "status": "offline",
        "created_at": datetime.utcnow(),
        "last_active": None,
    }

    result = await db.devices.insert_one(new_device)
    device_id = str(result.inserted_id)

    # 🆕 Send registration email asynchronously
    user = await db.users.find_one({"_id": user_id})
    if user and user.get("email"):
        background_tasks.add_task(
            send_device_registered_email,
            user["email"],
            device.name,
            device_id,
            token
        )

    return {
        "id": device_id,
        "name": device.name,
        "token": token,
        "status": "offline",
        "message": "Device created successfully. Check your email for setup instructions."
    }
```

## 3. Device Status Change Emails

### Location: `push_telemetry_v2()` endpoint

**Endpoint:** `POST /devices/{device_id}/telemetry`
**Line:** ~759-862 in device_routes.py

```python
@router.post("/devices/{device_id}/telemetry", tags=["Telemetry V2"])
async def push_telemetry_v2(
    device_id: str,
    data: TelemetryData,
    background_tasks: BackgroundTasks  # Add this parameter
):
    # ... existing validation code ...

    # Check if device just came online
    was_offline = device.get("status") == "offline"

    # Update device status to online
    await db.devices.update_one(
        {"_id": dev_oid},
        {
            "$set": {
                "status": "online",
                "last_active": now,
            }
        },
    )

    # 🆕 Send "device online" email if it was offline
    if was_offline:
        user = await db.users.find_one({"_id": device["user_id"]})
        if user and user.get("email"):
            # Check notification cooldown
            last_notification = device.get("last_notification_sent")
            cooldown_seconds = NOTIFICATION_COOLDOWN  # From utils.py

            should_notify = True
            if last_notification:
                time_since = (now - last_notification).total_seconds()
                should_notify = time_since > cooldown_seconds

            if should_notify:
                background_tasks.add_task(
                    send_device_status_email,
                    user["email"],
                    device["name"],
                    device_id,
                    "online",
                    now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    device.get("last_active").strftime("%Y-%m-%d %H:%M:%S UTC") if device.get("last_active") else None
                )

                # Update last notification time
                await db.devices.update_one(
                    {"_id": dev_oid},
                    {"$set": {"last_notification_sent": now}}
                )

    # ... rest of existing code ...
```

### Location: `auto_offline_checker()` background task

**Function:** Background worker that marks devices offline
**Line:** ~1800-1900 in device_routes.py

```python
async def auto_offline_checker():
    """Background task to mark devices offline after timeout."""
    while True:
        try:
            await asyncio.sleep(10)  # Check every 10 seconds

            now = datetime.utcnow()
            timeout_threshold = now - timedelta(seconds=OFFLINE_TIMEOUT)

            # Find devices that should be marked offline
            devices_to_mark_offline = await db.devices.find({
                "status": "online",
                "last_active": {"$lt": timeout_threshold}
            }).to_list(length=None)

            for device in devices_to_mark_offline:
                # Mark device offline
                await db.devices.update_one(
                    {"_id": device["_id"]},
                    {"$set": {"status": "offline"}}
                )

                # 🆕 Send "device offline" email
                user = await db.users.find_one({"_id": device["user_id"]})
                if user and user.get("email"):
                    # Check notification cooldown
                    last_notification = device.get("last_notification_sent")
                    cooldown_seconds = NOTIFICATION_COOLDOWN

                    should_notify = True
                    if last_notification:
                        time_since = (now - last_notification).total_seconds()
                        should_notify = time_since > cooldown_seconds

                    if should_notify:
                        # Send email in thread to avoid blocking
                        asyncio.create_task(asyncio.to_thread(
                            send_device_status_email,
                            user["email"],
                            device["name"],
                            str(device["_id"]),
                            "offline",
                            now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                            device.get("last_active").strftime("%Y-%m-%d %H:%M:%S UTC") if device.get("last_active") else None
                        ))

                        # Update last notification time
                        await db.devices.update_one(
                            {"_id": device["_id"]},
                            {"$set": {"last_notification_sent": now}}
                        )

                logger.info(f"Device {device['name']} marked offline (no activity for {OFFLINE_TIMEOUT}s)")

        except Exception as e:
            logger.error(f"Error in auto_offline_checker: {e}", exc_info=True)
```

## 4. Critical Device Alerts

### Example: Low Battery Alert

```python
@router.post("/devices/{device_id}/telemetry", tags=["Telemetry V2"])
async def push_telemetry_v2(
    device_id: str,
    data: TelemetryData,
    background_tasks: BackgroundTasks
):
    # ... existing code ...

    # 🆕 Check for critical conditions
    telemetry_data = data.data

    # Low battery alert
    if "battery" in telemetry_data:
        battery_level = float(telemetry_data["battery"])
        if battery_level < 10:  # Less than 10%
            user = await db.users.find_one({"_id": device["user_id"]})
            if user and user.get("email"):
                background_tasks.add_task(
                    send_user_alert_email,
                    user["email"],
                    f"Low Battery: {device['name']}",
                    f"Device '{device['name']}' battery is critically low at {battery_level}%.\n\n"
                    f"Please charge or replace the battery soon to avoid service interruption."
                )

    # High temperature alert
    if "temperature" in telemetry_data:
        temp = float(telemetry_data["temperature"])
        if temp > 80:  # Over 80°C
            user = await db.users.find_one({"_id": device["user_id"]})
            if user and user.get("email"):
                background_tasks.add_task(
                    send_user_alert_email,
                    user["email"],
                    f"High Temperature Alert: {device['name']}",
                    f"Device '{device['name']}' is reporting high temperature: {temp}°C.\n\n"
                    f"This may indicate a hardware issue. Please check the device immediately."
                )

    # ... rest of code ...
```

## 5. Testing Your Integration

### Test Device Registration Email

```python
# In Python shell or test script
import asyncio
from utils import send_device_registered_email

asyncio.run(asyncio.to_thread(
    send_device_registered_email,
    "your-email@example.com",
    "ESP32 Test Device",
    "6123456abcdef12345678901",
    "test_token_abc123def456"
))
```

### Test Device Status Email

```python
from utils import send_device_status_email
from datetime import datetime

# Test "online" email
asyncio.run(asyncio.to_thread(
    send_device_status_email,
    "your-email@example.com",
    "ESP32 Living Room",
    "6123456abcdef12345678901",
    "online",
    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
))

# Test "offline" email
asyncio.run(asyncio.to_thread(
    send_device_status_email,
    "your-email@example.com",
    "ESP32 Living Room",
    "6123456abcdef12345678901",
    "offline",
    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    "2026-02-12 15:30:00 UTC"  # Last active time
))
```

## 6. Configuration Checklist

Before deploying, ensure these environment variables are set:

```bash
# Email Configuration
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_FROM=noreply@thingsnxt.com
EMAIL_FROM_NAME=ThingsNXT

# Application URLs
FRONTEND_URL=https://thingsnxt.vercel.app
APP_SCHEME=ThingsNXT

# Notification Settings
NOTIFICATION_COOLDOWN=300  # 5 minutes between notifications
OFFLINE_TIMEOUT=20  # Seconds before marking device offline
```

## 7. Email Notification Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Device Registration                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    User creates device
                              │
                              ▼
                    Backend generates token
                              │
                              ▼
                    📧 Email sent with credentials
                              │
                              ▼
                    User configures ESP32


┌─────────────────────────────────────────────────────────────┐
│                    Device Status Changes                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ESP32 sends telemetry
                              │
                              ▼
                    Was device offline? ──No──► Continue
                              │
                             Yes
                              ▼
                    Check notification cooldown
                              │
                              ▼
                    Cooldown expired? ──No──► Skip email
                              │
                             Yes
                              ▼
                    📧 "Device Online" email sent
                              │
                              ▼
                    Update last_notification_sent


┌─────────────────────────────────────────────────────────────┐
│                    Device Goes Offline                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    Background worker checks
                              │
                              ▼
                    No telemetry for 20+ seconds
                              │
                              ▼
                    Mark device offline
                              │
                              ▼
                    Check notification cooldown
                              │
                              ▼
                    Cooldown expired? ──No──► Skip email
                              │
                             Yes
                              ▼
                    📧 "Device Offline" email sent
```

## 8. Best Practices

### ✅ Do's

1. **Use BackgroundTasks** for email sending (non-blocking)
2. **Check notification cooldown** to prevent spam
3. **Validate user email exists** before sending
4. **Log email sending** for debugging
5. **Include device context** in emails (name, ID, timestamp)
6. **Use plain text fallbacks** for all HTML emails

### ❌ Don'ts

1. **Don't block requests** waiting for email to send
2. **Don't send emails** without cooldown checks
3. **Don't include passwords** in emails
4. **Don't send to unverified** email addresses
5. **Don't log device tokens** in email functions
6. **Don't retry failed emails** infinitely

## 9. Monitoring

### Add Email Metrics

```python
# Track email statistics
email_stats = {
    "device_registered": 0,
    "device_online": 0,
    "device_offline": 0,
    "alerts": 0,
    "failed": 0
}

# Increment on send
def send_device_registered_email_tracked(*args, **kwargs):
    success = send_device_registered_email(*args, **kwargs)
    if success:
        email_stats["device_registered"] += 1
    else:
        email_stats["failed"] += 1
    return success

# Endpoint to view stats
@router.get("/admin/email-stats")
async def get_email_stats(current_user: dict = Depends(get_current_admin)):
    return email_stats
```

## 10. Troubleshooting

### Email Not Sending?

```python
# Add debug logging
import logging
logger = logging.getLogger(__name__)

# In your endpoint
logger.info(f"Attempting to send email to {user['email']}")
success = send_device_registered_email(...)
logger.info(f"Email send result: {success}")
```

### Check SMTP Connection

```python
import smtplib
from utils import EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD

try:
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        print("✅ SMTP connection successful!")
except Exception as e:
    print(f"❌ SMTP connection failed: {e}")
```

## Summary

Your email integration is ready! The system will now:

- ✅ Send welcome emails on signup (already integrated)
- ✅ Send device registration emails with credentials
- ✅ Send device online notifications
- ✅ Send device offline notifications
- ✅ Send critical alerts (battery, temperature, etc.)

All emails are:

- Professional and branded
- Mobile-responsive
- Non-blocking (background tasks)
- Rate-limited (cooldown)
- Logged for debugging

**Next Step:** Integrate the code snippets above into your `device_routes.py`! 🚀
