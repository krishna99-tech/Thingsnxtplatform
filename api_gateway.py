from fastapi import APIRouter, Depends, HTTPException, status
from starlette.requests import HTTPConnection
from datetime import datetime
import logging
import os
import time
from collections import defaultdict
import redis.asyncio as redis
import uuid

# Import all sub-routers
from auth_routes import router as auth_router
from device_routes import router as device_router
from websocket_routes import router as websocket_router
from events import router as events_router

# Import dependencies for health check
from db import db
from websocket_manager import manager

logger = logging.getLogger(__name__)

# Initialize Redis Connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

# ============================================================
# 🛡️ Rate Limiter Dependency
# ============================================================
class RateLimiter:
    """
    Redis-based rate limiter using a sliding window (Sorted Sets).
    Applied as a dependency to protect API endpoints.
    """
    def __init__(self, requests_limit: int = 100, time_window: int = 60, excluded_paths: list = None):
        self.requests_limit = requests_limit
        self.time_window = time_window
        self.excluded_paths = excluded_paths or []
        self.requests = defaultdict(list)  # In-memory fallback
        self.use_redis = True

    async def __call__(self, conn: HTTPConnection):
        # 1. Exclude WebSockets (connection establishment)
        if conn.scope["type"] == "websocket":
            return

        # 2. Exclude specific paths
        if any(conn.url.path.startswith(p) for p in self.excluded_paths):
            return

        # Identify client by IP address
        # Support X-Forwarded-For for proxies (Load Balancers, Nginx)
        forwarded = conn.headers.get("X-Forwarded-For")
        real_ip = conn.headers.get("X-Real-IP")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif real_ip:
            client_ip = real_ip.strip()
        else:
            client_ip = conn.client.host if conn.client else "unknown"

        now = time.time()

        # Attempt Redis Strategy
        if self.use_redis:
            key = f"rate_limit:{client_ip}"
            member = f"{now}:{uuid.uuid4()}"
            try:
                async with redis_client.pipeline(transaction=True) as pipe:
                    # Remove requests older than the time window
                    await pipe.zremrangebyscore(key, 0, now - self.time_window)
                    # Add current request
                    await pipe.zadd(key, {member: now})
                    # Count requests in the current window
                    await pipe.zcard(key)
                    # Set expiry for the key to auto-cleanup
                    await pipe.expire(key, self.time_window + 5)
                    results = await pipe.execute()

                request_count = results[2]
                if request_count > self.requests_limit:
                    logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Please try again later.")
                return
            except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
                logger.warning(f"Redis unavailable ({e}). Switching to in-memory rate limiting.")
                self.use_redis = False
            except redis.RedisError as e:
                logger.error(f"Redis rate limiter error: {e}")
                # Fail open for other Redis errors to avoid blocking traffic
                return

        # Fallback: In-Memory Strategy
        # Clean up old requests for this IP (sliding window)
        self.requests[client_ip] = [t for t in self.requests[client_ip] if now - t < self.time_window]
        
        if len(self.requests[client_ip]) >= self.requests_limit:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Please try again later.")
            
        self.requests[client_ip].append(now)

# Initialize global rate limiter (e.g., 100 requests per minute)
limiter = RateLimiter(requests_limit=100, time_window=60, excluded_paths=["/health", "/docs", "/redoc"])

# Create the main API Gateway router with global rate limiting
api_gateway = APIRouter(dependencies=[Depends(limiter)])

# ============================================================
# 🔗 Route Aggregation
# ============================================================
# Include all service routers
api_gateway.include_router(auth_router)
api_gateway.include_router(device_router)
api_gateway.include_router(websocket_router)
api_gateway.include_router(events_router)


# ============================================================
# 🏥 Common Gateway Endpoints (Health, Root)
# ============================================================
@api_gateway.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "message": "ThingsNXT IoT Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }

@api_gateway.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "disconnected"
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_status,
        "websocket_connections": manager.get_connection_count(),
    }
