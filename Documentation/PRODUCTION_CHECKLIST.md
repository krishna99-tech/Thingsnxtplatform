# 🚀 Production Deployment Checklist

## Backend (Thingsnxtplatform)

- [ ] Update `.env.production` with secure credentials
- [ ] Set `ENVIRONMENT=production` in `.env`
- [ ] Configure MongoDB Atlas production cluster
- [ ] Set `ALLOWED_ORIGINS` with production domains
- [ ] Update `SECRET_KEY` with a strong random value
- [ ] Configure SMTP for password reset emails
- [ ] Enable HTTPS/SSL certificates
- [ ] Set up logging aggregation (Sentry, LogRocket, etc.)
- [ ] Configure rate limiting
- [ ] Set up monitoring and alerts
- [ ] Test all API endpoints with production data
- [ ] Verify WebSocket connections
- [ ] Test notification stream (SSE)
- [ ] Set up database backups

### Run production server:
```bash
export ENVIRONMENT=production
export LOG_LEVEL=info
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Or with gunicorn:
```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000
```

## Frontend (React Native)

- [ ] Update `frontend/screens/config.js` with production URLs
- [ ] Set `IS_DEV = false`
- [ ] Remove console.log statements (or use production build)
- [ ] Update `app.json` with production app details
- [ ] Build production APK/IPA
- [ ] Test on real devices
- [ ] Verify all API endpoints work
- [ ] Test notifications on mobile
- [ ] Test WebSocket reconnection
- [ ] Configure push notifications (optional)

### Production config.js:
```javascript
export const BASE_URL = "https://api.yourdomain.com";
export const API_BASE = BASE_URL;
export const WS_URL = "wss://api.yourdomain.com/ws";
export const IS_DEV = false;
```

## Security

- [ ] Enable CORS properly
- [ ] Use HTTPS everywhere
- [ ] Implement rate limiting
- [ ] Use strong secret keys
- [ ] Implement request validation
- [ ] Add authentication to all endpoints
- [ ] Use secure cookies (HTTPOnly, Secure flags)
- [ ] Implement CSRF protection
- [ ] Regular security audits
- [ ] Keep dependencies updated

## Monitoring

- [ ] Set up error tracking (Sentry)
- [ ] Monitor API response times
- [ ] Monitor WebSocket connections
- [ ] Monitor database performance
- [ ] Set up alerts for critical errors
- [ ] Monitor server resources
- [ ] Set up uptime monitoring

## Database

- [ ] Create production indexes
- [ ] Set up automatic backups
- [ ] Test backup restoration
- [ ] Monitor database size
- [ ] Set up database replication
- [ ] Enable authentication
- [ ] Configure network access controls

## Testing

- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Load testing completed
- [ ] Security testing completed
- [ ] User acceptance testing (UAT)
