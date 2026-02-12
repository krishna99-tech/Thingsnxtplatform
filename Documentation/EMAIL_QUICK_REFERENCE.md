# Email System - Quick Reference

## 📧 Available Email Functions

```python
from utils import (
    send_email,                      # Core SMTP function
    send_welcome_email,              # New user welcome
    send_reset_email,                # Password reset
    send_user_alert_email,           # System alerts
    send_broadcast_email,            # Admin broadcasts
    send_device_status_email,        # Device online/offline 🆕
    send_device_registered_email     # Device registration 🆕
)
```

## 🚀 Quick Usage

### Device Registration

```python
send_device_registered_email(
    email="user@example.com",
    device_name="ESP32 Living Room",
    device_id="6123456abcdef12345678901",
    device_token="abc123def456ghi789"
)
```

### Device Status Change

```python
# Device online
send_device_status_email(
    email="user@example.com",
    device_name="ESP32 Living Room",
    device_id="6123456abcdef12345678901",
    status="online",
    timestamp="2026-02-12 16:00:00 UTC"
)

# Device offline
send_device_status_email(
    email="user@example.com",
    device_name="ESP32 Living Room",
    device_id="6123456abcdef12345678901",
    status="offline",
    timestamp="2026-02-12 16:05:00 UTC",
    last_active="2026-02-12 15:55:00 UTC"
)
```

### System Alert

```python
send_user_alert_email(
    email="user@example.com",
    subject="Low Battery",
    message="Device battery is at 5%"
)
```

## ⚙️ Configuration

```bash
# Required Environment Variables
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_FROM=noreply@thingsnxt.com
EMAIL_FROM_NAME=ThingsNXT
FRONTEND_URL=https://thingsnxt.vercel.app
```

## 📁 Email Templates

| Template                          | Purpose        | Variables                                 |
| --------------------------------- | -------------- | ----------------------------------------- |
| `email_welcome.html`              | New user       | username, app_name, FRONTEND_URL          |
| `email_reset.html`                | Password reset | token, web_reset_link, app_reset_link     |
| `email_alert.html`                | System alerts  | subject, message                          |
| `email_broadcast.html`            | Broadcasts     | subject, message                          |
| `email_device_status.html` 🆕     | Device status  | device_name, device_id, status, timestamp |
| `email_device_registered.html` 🆕 | Registration   | device_name, device_id, device_token      |

## 🔧 Integration Pattern

```python
# In your endpoint
@router.post("/devices")
async def add_device(
    device: DeviceCreate,
    background_tasks: BackgroundTasks,  # Add this!
    current_user: dict = Depends(get_current_user)
):
    # ... create device ...

    # Send email asynchronously
    user = await db.users.find_one({"_id": user_id})
    if user and user.get("email"):
        background_tasks.add_task(
            send_device_registered_email,
            user["email"],
            device.name,
            device_id,
            token
        )

    return device_dict
```

## 🧪 Testing

```python
# Test in Python shell
import asyncio
from utils import send_device_registered_email

asyncio.run(asyncio.to_thread(
    send_device_registered_email,
    "test@example.com",
    "Test Device",
    "6123456abcdef12345678901",
    "test_token"
))
```

## ✅ Checklist

- [ ] Configure SMTP credentials
- [ ] Test email delivery
- [ ] Integrate device registration email
- [ ] Integrate device status emails
- [ ] Test all email flows
- [ ] Configure SPF/DKIM (production)
- [ ] Monitor email delivery

## 📚 Documentation

- **EMAIL_SYSTEM_SUMMARY.md** - Complete system overview
- **EMAIL_INTEGRATION_GUIDE.md** - Integration code examples
- **EMAIL_REVIEW_SUMMARY.md** - What was changed

## 🎨 Email Design

- **Brand Colors**: ThingsNXT blue (#3b82f6)
- **Status Colors**: Green (online), Red (offline)
- **Responsive**: Mobile-friendly
- **Compatible**: Gmail, Outlook, Apple Mail

## 🔒 Security

- ✅ Device tokens sent only once
- ✅ Security warnings included
- ✅ SMTP with TLS
- ✅ No sensitive data in logs
- ✅ Notification cooldown (5 min)

## 📊 Email Types

| Type                 | When Sent       | Template                     |
| -------------------- | --------------- | ---------------------------- |
| Welcome              | User signup     | email_welcome.html           |
| Password Reset       | Forgot password | email_reset.html             |
| Device Registered 🆕 | Device created  | email_device_registered.html |
| Device Online 🆕     | Device connects | email_device_status.html     |
| Device Offline 🆕    | Device timeout  | email_device_status.html     |
| System Alert         | Critical event  | email_alert.html             |
| Broadcast            | Admin message   | email_broadcast.html         |

## 🚨 Troubleshooting

**Emails not sending?**

```python
# Check SMTP config
from utils import EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD
print(f"Host: {EMAIL_HOST}:{EMAIL_PORT}")
print(f"User: {EMAIL_USER}")
print(f"Pass: {'SET' if EMAIL_PASSWORD else 'NOT SET'}")
```

**Test SMTP connection:**

```python
import smtplib
from utils import EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD

with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
    server.starttls()
    server.login(EMAIL_USER, EMAIL_PASSWORD)
    print("✅ SMTP OK")
```

## 💡 Best Practices

1. **Always use BackgroundTasks** for email sending
2. **Check notification cooldown** before sending
3. **Validate email exists** before sending
4. **Log email operations** for debugging
5. **Use plain text fallbacks** for all emails
6. **Never log device tokens** or passwords

## 🎯 Quick Start

1. Set environment variables
2. Test SMTP connection
3. Send test email
4. Integrate into endpoints
5. Deploy to production

**Your email system is production-ready!** 🚀
