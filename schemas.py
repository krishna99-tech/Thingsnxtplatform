from pydantic import BaseModel, EmailStr, validator
from typing import Optional, Dict, Any
from datetime import datetime


# ===============================
# 🧩 User Creation Schema
# ===============================
class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    full_name: Optional[str] = None

    @validator("username")
    def username_alphanumeric(cls, v):
        assert all(c.isalnum() or c in "-_" for c in v), "Username can only contain letters, numbers, hyphens, and underscores"
        assert len(v) >= 3, "Username must be at least 3 characters"
        return v

    @validator("password")
    def password_strength(cls, v):
        assert len(v) >= 8, "Password must be at least 8 characters"
        assert len(v) <= 72, "Password must be at most 72 characters"
        assert any(c.islower() for c in v), "Password must contain at least one lowercase letter"
        assert any(c.isupper() for c in v), "Password must contain at least one uppercase letter"
        assert any(c.isdigit() for c in v), "Password must contain at least one digit"
        return v


# ===============================
# 🔐 User Login Schema
# ===============================
class UserLogin(BaseModel):
    email: EmailStr
    password: str


# ===============================
# 🎫 Token Response Schema
# ===============================
class TokenResp(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


# ===============================
# 🧾 Token Data
# ===============================
class TokenData(BaseModel):
    email: Optional[str] = None


# ===============================
# 📧 Forgot Password
# ===============================
class ForgotPasswordRequest(BaseModel):
    email: EmailStr


# ===============================
# 🔑 Reset Password
# ===============================
class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @validator("new_password")
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


# ===============================
# 👤 User Output Schema
# ===============================
class UserOut(BaseModel):
    id: str
    email: Optional[str]
    username: Optional[str]
    full_name: Optional[str]
    role: Optional[str] = "User"
    is_active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    notification_settings: Optional[Dict[str, Any]] = None


# ===============================
# 🚪 Logout Response Schema
# ===============================
class LogoutResponse(BaseModel):
    message: str
    tokens_deleted: int

