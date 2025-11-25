import os
import logging
from dotenv import load_dotenv
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bson import ObjectId

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================
# ⚙️ Constants
# ============================================================
OFFLINE_TIMEOUT = int(os.getenv("OFFLINE_TIMEOUT", "20"))
SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
RESET_TOKEN_EXPIRE_HOURS = int(os.getenv("RESET_TOKEN_EXPIRE_HOURS", "2"))

EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER","electrogadgedc@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD","ybkcseepobjiwfpr")
EMAIL_FROM = os.getenv("EMAIL_FROM") or EMAIL_USER

pwd_context = CryptContext(schemes=[os.getenv("PWD_SCHEME", "bcrypt")], deprecated="auto")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is missing. "
        "Generate a secure key (see secretkey.py) and export SECRET_KEY before starting the server."
    )

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
        if not EMAIL_USER or not EMAIL_PASSWORD:
            logger.warning(
                "Email credentials missing; skipping password reset email for %s. "
                "Set EMAIL_USER and EMAIL_PASSWORD to enable email delivery.",
                email,
            )
            return False

        # Use environment variables for production-ready configuration
        app_name = os.getenv("APP_NAME", "ThingsNXT IoT Platform")
        company_name = os.getenv("COMPANY_NAME", "ThingsNXT")
        frontend_url = os.getenv("FRONTEND_URL", "https://thingsnxt.vercel.app")
        app_scheme = os.getenv("APP_SCHEME")  # No default, button will be conditional
        copyright_text = f"© {datetime.now().year} {company_name}"

        web_reset_link = f"{frontend_url}/reset-password?token={token}"

        # Conditionally create the "Reset on App" button
        # This prevents showing a dead link if no APP_SCHEME is configured
        app_button_html = ""
        if app_scheme:
            app_reset_link = f"{app_scheme}://reset-password?token={token}"
            app_button_html = f'<a href="{app_reset_link}" style="flex:1;display:inline-block;background:#34c759;color:#fff;text-decoration:none;font-weight:bold;padding:12px 16px;border-radius:6px;font-size:14px;text-align:center;">📱 Reset on App</a>'

        message = MIMEMultipart("alternative")
        message["Subject"] = f"{app_name} — Password Reset Request"
        message["From"] = EMAIL_FROM or EMAIL_USER
        message["To"] = email

        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background: #FAFAFA; padding: 32px;">
                <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.1);padding:32px;">
                    <h2 style="color:#007aff;margin-bottom:6px;margin-top:0;">{app_name}</h2>
                    <h3 style="color:#333;margin-bottom:18px;margin-top:0;">Password Reset Request</h3>
                    <p style="font-size:15px;color:#222;line-height:1.6;">
                        You requested to reset your password for <b>{app_name}</b>.
                        <br>If you did not request this, you can safely ignore this email.
                    </p>
                    <p style="font-size:15px;margin-top:18px;color:#333;">Use this <b>reset code</b>:</p>
                    <div style="background:#f4f4f4;padding:18px 0;border-radius:7px;text-align:center;font-size:20px;letter-spacing:2px;margin:12px 0 20px 0;color:#111;border:1px solid #eee;font-weight:bold;font-family:monospace;">
                        {token}
                    </div>
                    <p style="color:#444;font-size:15px;">Choose how to reset your password:</p>
                    <div style="display:flex;gap:10px;margin:15px 0;">
                        <a href="{web_reset_link}" style="flex:1;display:inline-block;background:#007aff;color:#fff;text-decoration:none;font-weight:bold;padding:12px 16px;border-radius:6px;font-size:14px;text-align:center;">🌐 Reset on Web</a>
                        {app_button_html}
                    </div>
                    <hr style="margin:24px 0;border:none;border-top:1px solid #eee;">
                    <div style="font-size:12px;color:#888;text-align:center;line-height:18px;">
                        {copyright_text}<br>
                        <a href="{frontend_url}" style="color:#aaa;text-decoration:none;">{frontend_url}</a>
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
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM or EMAIL_USER, email, message.as_string())
        
        logger.info(f"Password reset email sent successfully to {email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {e}", exc_info=True)
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
