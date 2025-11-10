import os
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bson import ObjectId  # ✅ Add this import

# ============================================================
# ⚙️ Constants
# ============================================================
OFFLINE_TIMEOUT = int(os.getenv("OFFLINE_TIMEOUT", "20"))
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
RESET_TOKEN_EXPIRE_HOURS = int(os.getenv("RESET_TOKEN_EXPIRE_HOURS", "2"))

EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER", "electrogadgedc@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "eekhyavnslgbixtk")

pwd_context = CryptContext(schemes=[os.getenv("PWD_SCHEME", "bcrypt")], deprecated="auto")

# ============================================================
# 🔐 Password + JWT Utilities
# ============================================================
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ============================================================
# ✉️ Email Utility
# ============================================================
def send_reset_email(email: str, token: str):
    try:
        app_name = "ThingsNXT IoT Platform"
        company_url = "https://thingsnxt.vercel.app/"
        copyright_text = f"© {app_name} • Electro Gadgedc"

        reset_link = f"myapp://reset-password?token={token}"
        web_reset_link = f"{company_url}/reset-password?token={token}"

        message = MIMEMultipart("alternative")
        message["Subject"] = f"{app_name} — Password Reset Request"
        message["From"] = EMAIL_USER
        message["To"] = email

        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background: #FAFAFA; padding: 32px;">
                <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;box-shadow:0 2px 12px #0002;padding:32px;">
                    <h2 style="color:#007aff;margin-bottom:6px;">{app_name}</h2>
                    <h3 style="color:#333;margin-bottom:18px;">Password Reset Request</h3>
                    <p style="font-size:15px;color:#222;">
                        You requested to reset your password for <b>{app_name}</b>.
                        <br>If you did not request this, you can safely ignore this email.
                    </p>
                    <p style="font-size:15px;margin-top:18px;">Use this <b>reset code</b>:</p>
                    <div style="background:#f4f4f4;padding:18px 0;border-radius:7px;text-align:center;font-size:20px;letter-spacing:2px;margin:12px 0 20px 0;color:#111;border:1px solid #eee;font-weight:bold;">
                        {token}
                    </div>
                    <p style="color:#444;font-size:15px;">Or click this link to reset directly:</p>
                    <a href="{reset_link}" style="display:inline-block;background:#007aff;color:#fff;text-decoration:none;font-weight:bold;padding:12px 26px;border-radius:6px;font-size:16px;margin:10px 0;">Reset via App</a>
                    <br>
                    <a href="{web_reset_link}" style="color:#007aff;font-size:14px;margin-top:6px;display:inline-block;">Reset via Website</a>
                    <hr style="margin:24px 0;">
                    <div style="font-size:12px;color:#888;text-align:center;line-height:18px">
                        {copyright_text}<br>
                        <a href="{company_url}" style="color:#aaa;text-decoration:none;">{company_url.replace('https://', '')}</a>
                    </div>
                </div>
            </body>
        </html>
        """

        part = MIMEText(html, "html")
        message.attach(part)
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.ehlo()
            server.starttls()
            if EMAIL_USER and EMAIL_PASSWORD:
                server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, email, message.as_string())
        print(f"Email sent to {email}")
        return True
    except Exception as e:
        print("Email error:", e)
        return False

# ============================================================
# 🧩 MongoDB Helper
# ============================================================
def doc_to_dict(doc):
    """Convert MongoDB document (ObjectId, datetime) to serializable dict."""
    if not doc:
        return {}

    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
