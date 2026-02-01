import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from jinja2 import Environment, FileSystemLoader
from bson import ObjectId
import pytz

# ============================================================
# 🕒 Timezone Support
# ============================================================
try:
    from zoneinfo import ZoneInfo
    try:
        IST = ZoneInfo("Asia/Kolkata")
        ZoneInfo_available = True
    except Exception:
        IST = pytz.timezone("Asia/Kolkata")
        ZoneInfo_available = False
except ImportError:
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

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================
# ⚙️ Constants
# ============================================================
OFFLINE_TIMEOUT = int(os.getenv("OFFLINE_TIMEOUT", "20"))
NOTIFICATION_COOLDOWN = int(os.getenv("NOTIFICATION_COOLDOWN", "60"))
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
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "ThingsNXT")

pwd_context = CryptContext(schemes=[os.getenv("PWD_SCHEME", "bcrypt")], deprecated="auto")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is missing. "
        "Generate a secure key (see secretkey.py) and export SECRET_KEY before starting the server."
    )

# Setup Jinja2 environment
template_dir = Path(__file__).parent / "templates"
if not template_dir.is_dir():
    raise RuntimeError(f"Template directory not found at {template_dir}")
jinja_env = Environment(loader=FileSystemLoader(template_dir))
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
def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Generic function to send emails using SMTP."""
    try:
        if not EMAIL_USER or not EMAIL_PASSWORD:
            logger.warning(
                "Email credentials missing; skipping email to %s. "
                "Set EMAIL_USER and EMAIL_PASSWORD to enable email delivery.",
                to_email,
            )
            return False

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        
        sender_email = EMAIL_FROM or EMAIL_USER
        if EMAIL_FROM_NAME:
            message["From"] = formataddr((EMAIL_FROM_NAME, sender_email))
        else:
            message["From"] = sender_email
        message["To"] = to_email

        message.attach(MIMEText(text_body, "plain"))
        message.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM or EMAIL_USER, to_email, message.as_string())

        logger.info(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}", exc_info=True)
        return False

def send_reset_email(email: str, token: str) -> bool:
    """Sends a password reset email using Jinja2 templates."""
    try:
        # Standard configuration
        app_name = os.getenv("APP_NAME", "ThingsNXT")
        frontend_url = os.getenv("FRONTEND_URL", "https://thingsnxt.vercel.app")
        app_scheme = os.getenv("APP_SCHEME", "ThingsNXT")

        context = {
            "token": token,
            "app_name": app_name,
            "FRONTEND_URL": frontend_url,
            "web_reset_link": f"{frontend_url}/reset-password?token={token}",
            "app_reset_link": f"{app_scheme}://reset-password?token={token}" if app_scheme else None,
            "copyright_text": f"© {datetime.now().year} {app_name}. All rights reserved.",
        }

        subject = f"{app_name} — Password Reset Request"

        try:
            html_template = jinja_env.get_template("email_reset.html")
            text_template = jinja_env.get_template("email_reset.txt")
            html_body = html_template.render(context)
            text_body = text_template.render(context)
            
            return send_email(email, subject, html_body, text_body)
        except Exception as e:
            logger.error(f"Failed to render email template: {e}", exc_info=True)
            return False

    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {e}", exc_info=True)
        return False

def send_broadcast_email(to_email: str, subject: str, message_content: str) -> bool:
    """
    Sends a branded broadcast email using the 'email_broadcast.html' template.
    """
    try:
        app_name = os.getenv("APP_NAME", "ThingsNXT")
        frontend_url = os.getenv("FRONTEND_URL", "https://thingsnxt.vercel.app")

        # Context
        context = {
            "subject": subject,
            "message": message_content,
            "app_name": app_name,
            "FRONTEND_URL": frontend_url,
            "year": datetime.now().year,
        }

        # Render
        template = jinja_env.get_template("email_broadcast.html")
        html_body = template.render(context)
        
        # Plain text fallback
        text_body = f"{subject}\n\n{message_content}\n\n--\n{app_name}"

        return send_email(to_email, subject, html_body, text_body)

    except Exception as e:
        logger.error(f"Failed to send broadcast email to {to_email}: {e}", exc_info=True)
        return False

def send_welcome_email(email: str, username: str) -> bool:
    """Sends a welcome email to a newly registered user."""
    try:
        app_name = os.getenv("APP_NAME", "ThingsNXT")
        frontend_url = os.getenv("FRONTEND_URL", "https://thingsnxt.vercel.app")
        
        context = {
            "username": username,
            "app_name": app_name,
            "FRONTEND_URL": frontend_url,
            "year": datetime.now().year,
        }
        
        subject = f"Welcome to {app_name}!"
        template = jinja_env.get_template("email_welcome.html")
        html_body = template.render(context)
        text_body = f"Welcome to {app_name}, {username}!\nYour account is ready."
        
        return send_email(email, subject, html_body, text_body)
    except Exception as e:
        logger.error(f"Failed to send welcome email to {email}: {e}")
        return False

def send_user_alert_email(email: str, subject: str, message: str) -> bool:
    """Sends a system alert or error notification to a specific user."""
    try:
        app_name = os.getenv("APP_NAME", "ThingsNXT")
        frontend_url = os.getenv("FRONTEND_URL", "https://thingsnxt.vercel.app")
        app_scheme = os.getenv("APP_SCHEME", "ThingsNXT")
        
        context = {
            "subject": subject,
            "message": message,
            "app_name": app_name,
            "FRONTEND_URL": frontend_url,
            "app_login_link": f"{app_scheme}://login" if app_scheme else None,
            "year": datetime.now().year,
        }
        
        full_subject = f"SYSTEM ALERT: {subject}"
        template = jinja_env.get_template("email_alert.html")
        html_body = template.render(context)
        text_body = f"SYSTEM ALERT: {subject}\n\n{message}"
        
        return send_email(email, full_subject, html_body, text_body)
    except Exception as e:
        logger.error(f"Failed to send alert email to {email}: {e}")
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


# ============================================================
# 🕒 Timezone Helpers
# ============================================================
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
