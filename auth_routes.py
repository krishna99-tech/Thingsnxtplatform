from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import secrets
import logging
from jose import JWTError, jwt
from typing import Optional
from bson import ObjectId

from db import db, doc_to_dict

logger = logging.getLogger(__name__)
from schemas import (
    UserCreate,
    UserLogin,
    TokenResp,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    TokenData,
    UserOut,
)
from utils import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    send_reset_email,
    send_welcome_email,
    SECRET_KEY,
    ALGORITHM,
    REFRESH_TOKEN_EXPIRE_DAYS,
    RESET_TOKEN_EXPIRE_HOURS,
)
from fastapi import BackgroundTasks

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


# ===============================================
# 🔐 Get Current User from JWT Token
# ===============================================
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub") or payload.get("email")
        token_type: str = payload.get("type")
        if email is None or (token_type and token_type != "access"):
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception

    user = await db.users.find_one({"$or": [{"email": token_data.email}, {"username": token_data.email}]})
    if not user:
        raise credentials_exception
    return doc_to_dict(user)



# ===============================================
# 🧩 Signup Route
# ===============================================
@router.post("/signup")
async def signup(user: UserCreate, background_tasks: BackgroundTasks):
    existing_user = await db.users.find_one(
        {"$or": [{"email": user.email}, {"username": user.username}]}
    )
    if existing_user:
        raise HTTPException(status_code=400, detail="Email or username already registered")

    hashed_pw = get_password_hash(user.password)
    user_doc = {
        "email": user.email,
        "username": user.username,
        "hashed_password": hashed_pw,
        "full_name": user.full_name,
        "is_active": True,
        "created_at": datetime.utcnow(),
    }
    insert_result = await db.users.insert_one(user_doc)
    user_doc["_id"] = insert_result.inserted_id

    access = create_access_token({"sub": user.username})
    refresh = create_refresh_token({"sub": user.username})
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    await db.refresh_tokens.insert_one(
        {"user_id": user_doc["_id"], "token": refresh, "expires_at": expires_at}
    )

    # Dispatch welcome email asynchronously
    background_tasks.add_task(send_welcome_email, user.email, user.username)

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": UserOut(**doc_to_dict(user_doc)),
    }



# ===============================================
# 🔑 Token (OAuth2 Login for Clients)
# ===============================================
@router.post("/token")
async def token(form_data: OAuth2PasswordRequestForm = Depends()):
    identifier = form_data.username
    user = await db.users.find_one({"$or": [{"username": identifier}, {"email": identifier}]})
    if not user:
        logger.warning(f"Login failed: User '{identifier}' not found")
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    if not verify_password(form_data.password, user["hashed_password"]):
        logger.warning(f"Login failed: Invalid password for user '{identifier}'")
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    if not user.get("is_active", True):
        logger.warning(f"Login failed: User '{identifier}' is inactive")
        raise HTTPException(status_code=400, detail="Inactive user")

    sub_value = user["username"]
    access = create_access_token({"sub": sub_value})
    refresh = create_refresh_token({"sub": sub_value})
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    await db.refresh_tokens.insert_one(
        {"user_id": user["_id"], "token": refresh, "expires_at": expires_at}
    )

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": UserOut(**doc_to_dict(user)),
    }


# ===============================================
# 🔐 Login (JSON Body Version)
# ===============================================


@router.post("/login")
async def login(user: UserLogin):
    user_db = await db.users.find_one(
        {"$or": [{"email": user.email}, {"username": user.email}]}
    )
    if not user_db:
        logger.warning(f"Login failed: User '{user.email}' not found")
        raise HTTPException(status_code=400, detail="Invalid email or password")

    if not verify_password(user.password, user_db["hashed_password"]):
        logger.warning(f"Login failed: Invalid password for user '{user.email}'")
        raise HTTPException(status_code=400, detail="Invalid email or password")

    if not user_db.get("is_active", True):
        logger.warning(f"Login failed: User '{user.email}' is inactive")
        raise HTTPException(status_code=400, detail="Inactive user")

    access = create_access_token({"sub": user_db["email"] or user_db["username"]})
    refresh = create_refresh_token({"sub": user_db["email"] or user_db["username"]})
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    await db.refresh_tokens.insert_one(
        {"user_id": user_db["_id"], "token": refresh, "expires_at": expires_at}
    )

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": UserOut(**doc_to_dict(user_db)),
    }


# ===============================================
# 🚪 Logout (Delete all refresh tokens)
# ===============================================
@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    await db.refresh_tokens.delete_many({"user_id": ObjectId(current_user["id"])})
    return {"message": "Logged out"}


# ===============================================
# 🔁 Refresh Token
# ===============================================
@router.post("/refresh", response_model=TokenResp)
async def refresh_token(refresh_token: str = Query(...)):
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        token_type: str = payload.get("type")
        if email is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        dbt = await db.refresh_tokens.find_one({"token": refresh_token})
        if not dbt:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        new_access = create_access_token({"sub": email})
        new_refresh = create_refresh_token({"sub": email})
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

        await db.refresh_tokens.update_one(
            {"_id": dbt["_id"]},
            {"$set": {"token": new_refresh, "expires_at": expires_at}},
        )

        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ===============================================
# 👤 Get Current User
# ===============================================
@router.get("/me", response_model=UserOut)
async def get_me(current_user: dict = Depends(get_current_user)):
    return current_user


# ===============================================
# ✏️ Update Current User
# ===============================================
@router.put("/me", response_model=UserOut)
async def update_me(
    user_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Update the current user's profile (username, email, full_name, etc.)."""
    user_id = ObjectId(current_user["id"])
    update_fields = {}
    if "username" in user_data and user_data["username"]:
        update_fields["username"] = user_data["username"]
    if "email" in user_data and user_data["email"]:
        update_fields["email"] = user_data["email"]
    if "full_name" in user_data:
        # Allow setting full_name to empty string or None
        update_fields["full_name"] = user_data["full_name"] if user_data["full_name"] else None
    if "notification_settings" in user_data:
        update_fields["notification_settings"] = user_data["notification_settings"]

    if not update_fields:
        raise HTTPException(status_code=400, detail="No update fields provided")

    await db.users.update_one({"_id": user_id}, {"$set": update_fields})
    updated_user = await db.users.find_one({"_id": user_id})
    return doc_to_dict(updated_user)

# ===============================================
# 🗑️ Delete Current User Account
# ===============================================
@router.delete("/me")
async def delete_me(current_user: dict = Depends(get_current_user)):
    """Deletes the current user's account and all associated data."""
    user_id = ObjectId(current_user["id"])

    # Perform deletions in parallel for efficiency
    await db.users.delete_one({"_id": user_id})
    await db.refresh_tokens.delete_many({"user_id": user_id})
    await db.devices.delete_many({"user_id": user_id})
    await db.dashboards.delete_many({"user_id": user_id})
    # Add other collections to clean up if necessary (e.g., widgets, schedules)
    # await db.widgets.delete_many({"user_id": user_id})

    return {"message": "Account and all associated data deleted successfully"}


# ===============================================
# ==============================================
@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    user = await db.users.find_one({"email": request.email})
    if user:
        token = secrets.token_urlsafe(32)[:8].upper()
        expires_at = datetime.utcnow() + timedelta(hours=RESET_TOKEN_EXPIRE_HOURS)
        await db.reset_tokens.insert_one(
            {"email": request.email, "token": token, "expires_at": expires_at, "used": False}
        )
        if not send_reset_email(request.email, token):
            raise HTTPException(status_code=500, detail="Failed to send reset email")
    return {"message": "If your email is registered, you will receive a reset code"}


# ===============================================
# 🔐 Verify Reset Token
# ===============================================
@router.get("/verify-reset-token")
async def verify_reset_token(token: str = Query(...)):
    """Check if a password reset token is valid and not expired."""
    token_data = await db.reset_tokens.find_one({"token": token, "used": False})

    if not token_data:
        raise HTTPException(status_code=404, detail="Token not found or has been used")

    if datetime.utcnow() > token_data["expires_at"]:
        raise HTTPException(status_code=400, detail="Token expired")

    return {"message": "Token is valid"}


# ===============================================
# 🔐 Reset Password
# ===============================================
@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    token_data = await db.reset_tokens.find_one({"token": request.token, "used": False})
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid or used token")

    if datetime.utcnow() > token_data["expires_at"]:
        raise HTTPException(status_code=400, detail="Token expired")

    hashed = get_password_hash(request.new_password)
    await db.users.update_one(
        {"email": token_data["email"]},
        {"$set": {"hashed_password": hashed}},
    )
    await db.reset_tokens.update_one(
        {"_id": token_data["_id"]}, {"$set": {"used": True}}
    )

    return {"message": "Password reset successful"}
