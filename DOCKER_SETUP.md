# Docker Setup Guide for ThingsNXT Platform

## Overview
This document describes the Docker setup for the ThingsNXT IoT Platform backend.

## Files Structure

### Dockerfile
- **Base Image**: Python 3.10-slim
- **Security**: Runs as non-root user (appuser)
- **Health Check**: Built-in health check endpoint
- **Production Ready**: Multi-worker uvicorn configuration

### docker-compose.yml
- **Services**: 
  - FastAPI Backend (with health checks)
- **Networks**: Isolated bridge network
- **External MongoDB**: Configured to connect to external MongoDB instance

## Quick Start

### 1. Environment Variables
Create a `.env` file in the project root:

```env
# MongoDB Configuration - External MongoDB
# Option 1: Connect to MongoDB on host machine (default)
MONGO_USER=admin
MONGO_PASSWORD=changeme
MONGO_DB_NAME=iot_auth_db

# Option 2: Connect to remote MongoDB (use full connection string)
# MONGO_URI=mongodb://username:password@mongodb-host:27017/database?authSource=admin
# Example for MongoDB Atlas:
# MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/database?retryWrites=true&w=majority

# JWT Configuration
SECRET_KEY=your-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
RESET_TOKEN_EXPIRE_HOURS=2

# Application Configuration
ENVIRONMENT=development
LOG_LEVEL=INFO
ALLOWED_ORIGINS=*
OFFLINE_TIMEOUT=20

# Email Configuration (Optional)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com
APP_NAME=ThingsNXT IoT Platform
FRONTEND_URL=http://localhost:3000
COMPANY_NAME=ThingsNXT
```

### 2. Build and Run

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop services
docker-compose down

# Stop and remove volumes (WARNING: Deletes data)
docker-compose down -v
```

### 3. Access Services

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs (development only)
- **Health Check**: http://localhost:8000/health
- **MongoDB**: External (configured via MONGO_URI or host.docker.internal)

## API Gateway Integration

The API gateway is fully integrated and provides:

1. **Route Aggregation**: All routes from:
   - `auth_routes.py` - Authentication endpoints
   - `device_routes.py` - Device, telemetry, dashboard, widget endpoints
   - `websocket_routes.py` - WebSocket connections
   - `events.py` - Server-Sent Events

2. **Rate Limiting**: 
   - 100 requests per minute per IP
   - Excludes: `/health`, `/docs`, `/redoc`
   - WebSocket connections excluded automatically

3. **Health Monitoring**:
   - Database connection status
   - WebSocket connection count
   - Service status

## Production Considerations

### Security
1. **Change Default Passwords**: Update MongoDB credentials
2. **Generate Strong SECRET_KEY**: Use `python secretkey.py` or generate a secure key
3. **Restrict CORS**: Set `ALLOWED_ORIGINS` to specific domains
4. **Environment Variables**: Never commit `.env` file

### Performance
1. **Worker Count**: Adjust `--workers` in Dockerfile CMD based on CPU cores
2. **MongoDB Indexes**: Automatically created on startup
3. **Rate Limiting**: Adjust limits in `api_gateway.py` based on needs

### Monitoring
- Health check endpoint: `/health`
- Logs: `docker-compose logs -f`
- MongoDB logs: `docker-compose logs -f mongodb`

## External MongoDB Configuration

### Option 1: MongoDB on Host Machine
If MongoDB is running on your host machine (outside Docker):
- The container uses `host.docker.internal` to connect to the host
- Ensure MongoDB is accessible on `localhost:27017` from your host
- Default connection string: `mongodb://admin:changeme@host.docker.internal:27017/iot_auth_db?authSource=admin`

### Option 2: Remote MongoDB (MongoDB Atlas, Cloud, etc.)
Set the full connection string in `.env`:
```env
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/database?retryWrites=true&w=majority
```

### Option 3: MongoDB on Different Host
```env
MONGO_URI=mongodb://username:password@mongodb-host:27017/database?authSource=admin
```

## Troubleshooting

### MongoDB Connection Issues
```bash
# Test connection from container
docker-compose exec api python -c "from db import db; import asyncio; asyncio.run(db.command('ping'))"

# Check if MongoDB is accessible from host
# On Linux/Mac:
mongosh "mongodb://admin:changeme@localhost:27017/iot_auth_db?authSource=admin"

# Check API logs for connection errors
docker-compose logs api | grep -i mongo
```

### Host Machine MongoDB Access
If MongoDB is on the host machine, ensure:
1. MongoDB is running and listening on `0.0.0.0:27017` (not just `127.0.0.1`)
2. Firewall allows connections on port 27017
3. MongoDB authentication is configured correctly

### API Not Starting
```bash
# Check API logs
docker-compose logs api

# Rebuild container
docker-compose build --no-cache api
docker-compose up -d api
```

### Port Conflicts
If port 8000 or 27017 are in use:
```yaml
# In docker-compose.yml, change ports:
ports:
  - "8001:8000"  # Use 8001 instead of 8000
```

## Development vs Production

### Development
- Docs enabled at `/docs`
- Debug logging
- Hot reload (if using local development)

### Production
- Docs disabled
- Info level logging
- Multiple workers
- Health checks enabled
- Rate limiting active

## MongoDB Connection Examples

### Local MongoDB (Host Machine)
```env
MONGO_USER=admin
MONGO_PASSWORD=yourpassword
MONGO_DB_NAME=iot_auth_db
```
Connection will be: `mongodb://admin:yourpassword@host.docker.internal:27017/iot_auth_db?authSource=admin`

### MongoDB Atlas (Cloud)
```env
MONGO_URI=mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/iot_auth_db?retryWrites=true&w=majority
```

### Remote MongoDB Server
```env
MONGO_URI=mongodb://username:password@192.168.1.100:27017/iot_auth_db?authSource=admin
```

### MongoDB with Replica Set
```env
MONGO_URI=mongodb://username:password@host1:27017,host2:27017,host3:27017/iot_auth_db?replicaSet=rs0&authSource=admin
```

