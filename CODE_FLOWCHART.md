# ThingsNXT Platform - Complete Function Flowchart
---
config:
  theme: mc
  look: neo
  layout: dagre
---

flowchart TB
    subgraph Main["🚀 main.py"]
        START[FastAPI App Start]
        STARTUP[startup_event]
        SHUTDOWN[shutdown_event]
        START --> STARTUP
        STARTUP --> INIT_DB[init_db]
        STARTUP --> AUTO_OFFLINE[auto_offline_checker]
        STARTUP --> LED_WORKER[led_schedule_worker]
    end

    subgraph API_Gateway["🌐 api_gateway.py"]
        ROOT[root]
        HEALTH[health_check]
        RATE_LIMITER[RateLimiter.__call__]
    end

    subgraph Auth["🔐 auth_routes.py"]
        GET_CURRENT_USER[get_current_user]
        SIGNUP[signup]
        TOKEN[token]
        LOGIN[login]
        LOGOUT[logout]
        REFRESH_TOKEN[refresh_token]
        GET_ME[get_me]
        UPDATE_ME[update_me]
        DELETE_ME[delete_me]
        FORGOT_PASSWORD[forgot_password]
        VERIFY_RESET_TOKEN[verify_reset_token]
        RESET_PASSWORD[reset_password]
    end

    subgraph Utils["🛠️ utils.py"]
        VERIFY_PASSWORD[verify_password]
        GET_PASSWORD_HASH[get_password_hash]
        CREATE_ACCESS_TOKEN[create_access_token]
        CREATE_REFRESH_TOKEN[create_refresh_token]
        SEND_RESET_EMAIL[send_reset_email]
        DOC_TO_DICT_UTILS[doc_to_dict]
        GET_IST_NOW[get_ist_now]
        UTC_TO_IST[utc_to_ist]
        IST_TO_UTC[ist_to_utc]
    end

    subgraph DB["💾 db.py"]
        INIT_DB[init_db]
        DOC_TO_DICT_DB[doc_to_dict]
    end

    subgraph Device_Routes["📱 device_routes.py"]
        SAFE_OID[safe_oid]
        SECURITY_VERIFY_OWNERSHIP[SecurityRules.verify_ownership]
        SECURITY_VERIFY_TOKEN[SecurityRules.verify_device_token]
        CREATE_NOTIFICATION[create_notification]
        TRIGGER_WEBHOOKS[trigger_webhooks]
        SEND_WEBHOOK[send_webhook]
        COMPUTE_VIRTUAL_PIN[compute_next_virtual_pin]
        APPLY_LED_STATE[apply_led_state]
        ENSURE_LED_ACCESS[ensure_led_widget_access]
        PATCH_WIDGET[patch_widget]
        SET_LED_STATE[set_led_state]
        GET_DEVICES[get_devices]
        ADD_DEVICE[add_device]
        DELETE_DEVICE[delete_device]
        BULK_UPDATE_STATUS[bulk_update_device_status]
        PUSH_TELEMETRY[push_telemetry]
        GET_LATEST_TELEMETRY[get_latest_telemetry_by_token]
        GET_TELEMETRY_HISTORY[get_telemetry_history]
        CREATE_DASHBOARD[create_dashboard]
        LIST_DASHBOARDS[list_dashboards]
        DELETE_DASHBOARD[delete_dashboard]
        UPDATE_DASHBOARD_LAYOUT[update_dashboard_layout]
        CREATE_WIDGET[create_widget]
        GET_WIDGETS[get_widgets]
        DELETE_WIDGET[delete_widget]
        CREATE_LED_SCHEDULE[create_led_schedule]
        CREATE_LED_TIMER[create_led_timer]
        LIST_LED_SCHEDULES[list_led_schedules]
        CANCEL_LED_SCHEDULE[cancel_led_schedule]
        LED_SCHEDULE_WORKER[led_schedule_worker]
        NOTIFICATIONS_HEALTH[notifications_health]
        GET_NOTIFICATIONS[get_notifications]
        MARK_NOTIFICATION_READ[mark_notification_read]
        MARK_ALL_READ[mark_all_notifications_read]
        DELETE_NOTIFICATION[delete_notification]
        NOTIFICATION_STREAM[notification_stream]
        AUTO_OFFLINE_CHECKER[auto_offline_checker]
        CREATE_WEBHOOK[create_webhook]
        LIST_WEBHOOKS[list_webhooks]
        GET_WEBHOOK[get_webhook]
        DELETE_WEBHOOK[delete_webhook]
        UPDATE_WEBHOOK[update_webhook]
    end

    subgraph Rules_Engine["🛡️ rules_engine.py"]
        RULES_ENGINE[RulesEngine]
        LOAD_RULES[load_rules]
        GET_RULE_STRING[_get_rule_string]
        VALIDATE_RULE[validate_rule]
    end

    subgraph WebSocket["🔌 websocket_routes.py"]
        WEBSOCKET_ENDPOINT[websocket_endpoint]
    end

    subgraph WebSocket_Manager["📡 websocket_manager.py"]
        CONNECTION_MANAGER[ConnectionManager]
        CONNECT[connect]
        DISCONNECT[disconnect]
        BROADCAST[broadcast]
        BROADCAST_TO_ALL[broadcast_to_all]
        GET_CONNECTION_COUNT[get_connection_count]
        GET_CONNECTED_USERS[get_connected_users]
    end

    subgraph Events["📢 events.py"]
        EVENT_STREAM[event_stream]
    end

    subgraph Event_Manager["📬 event_manager.py"]
        EVENT_MANAGER[EventManager]
        SUBSCRIBE[subscribe]
        UNSUBSCRIBE[unsubscribe]
        BROADCAST_EVENT[broadcast]
    end

    subgraph Schemas["📋 schemas.py"]
        USER_CREATE[UserCreate]
        USER_LOGIN[UserLogin]
        TOKEN_RESP[TokenResp]
        TOKEN_DATA[TokenData]
        FORGOT_PASSWORD_REQ[ForgotPasswordRequest]
        RESET_PASSWORD_REQ[ResetPasswordRequest]
        USER_OUT[UserOut]
    end

    subgraph Models["📦 models.py"]
        DEVICE_CREATE[DeviceCreate]
        DEVICE_UPDATE[DeviceUpdate]
        DEVICE_BULK_STATUS[DeviceBulkStatusUpdate]
        TELEMETRY_DATA[TelemetryData]
        DASHBOARD_CREATE[DashboardCreate]
        WIDGET_LAYOUT[WidgetLayout]
        DASHBOARD_LAYOUT_UPDATE[DashboardLayoutUpdate]
        WIDGET_CREATE[WidgetCreate]
        LED_SCHEDULE_CREATE[LedScheduleCreate]
        LED_TIMER_CREATE[LedTimerCreate]
        WEBHOOK_CREATE[WebhookCreate]
    end

    %% Main Flow Connections
    START --> API_Gateway
    STARTUP --> INIT_DB
    STARTUP --> AUTO_OFFLINE
    STARTUP --> LED_WORKER

    %% API Gateway Connections
    ROOT --> HEALTH
    HEALTH --> GET_CONNECTION_COUNT

    %% Auth Flow
    SIGNUP --> GET_PASSWORD_HASH
    SIGNUP --> CREATE_ACCESS_TOKEN
    SIGNUP --> CREATE_REFRESH_TOKEN
    SIGNUP --> DOC_TO_DICT_UTILS
    TOKEN --> VERIFY_PASSWORD
    TOKEN --> CREATE_ACCESS_TOKEN
    TOKEN --> CREATE_REFRESH_TOKEN
    LOGIN --> VERIFY_PASSWORD
    LOGIN --> CREATE_ACCESS_TOKEN
    LOGIN --> CREATE_REFRESH_TOKEN
    REFRESH_TOKEN --> CREATE_ACCESS_TOKEN
    REFRESH_TOKEN --> CREATE_REFRESH_TOKEN
    FORGOT_PASSWORD --> SEND_RESET_EMAIL
    RESET_PASSWORD --> GET_PASSWORD_HASH
    GET_CURRENT_USER --> DOC_TO_DICT_UTILS

    %% Device Routes - Security
    SECURITY_VERIFY_OWNERSHIP --> VALIDATE_RULE
    SECURITY_VERIFY_TOKEN --> SAFE_OID

    %% Device Routes - Devices
    GET_DEVICES --> DOC_TO_DICT_UTILS
    ADD_DEVICE --> DOC_TO_DICT_UTILS
    ADD_DEVICE --> BROADCAST_EVENT
    DELETE_DEVICE --> SECURITY_VERIFY_OWNERSHIP
    DELETE_DEVICE --> BROADCAST
    DELETE_DEVICE --> BROADCAST_EVENT
    BULK_UPDATE_STATUS --> BROADCAST
    BULK_UPDATE_STATUS --> BROADCAST_EVENT

    %% Device Routes - Telemetry
    PUSH_TELEMETRY --> SECURITY_VERIFY_TOKEN
    PUSH_TELEMETRY --> CREATE_NOTIFICATION
    PUSH_TELEMETRY --> BROADCAST
    PUSH_TELEMETRY --> TRIGGER_WEBHOOKS
    GET_LATEST_TELEMETRY --> SECURITY_VERIFY_TOKEN
    GET_TELEMETRY_HISTORY --> SECURITY_VERIFY_OWNERSHIP

    %% Device Routes - Dashboards
    CREATE_DASHBOARD --> DOC_TO_DICT_UTILS
    LIST_DASHBOARDS --> DOC_TO_DICT_UTILS
    DELETE_DASHBOARD --> SECURITY_VERIFY_OWNERSHIP
    UPDATE_DASHBOARD_LAYOUT --> SECURITY_VERIFY_OWNERSHIP

    %% Device Routes - Widgets
    CREATE_WIDGET --> SECURITY_VERIFY_OWNERSHIP
    CREATE_WIDGET --> COMPUTE_VIRTUAL_PIN
    CREATE_WIDGET --> DOC_TO_DICT_UTILS
    GET_WIDGETS --> UTC_TO_IST
    DELETE_WIDGET --> SECURITY_VERIFY_OWNERSHIP
    DELETE_WIDGET --> BROADCAST
    PATCH_WIDGET --> SECURITY_VERIFY_OWNERSHIP
    PATCH_WIDGET --> DOC_TO_DICT_UTILS
    PATCH_WIDGET --> BROADCAST

    %% Device Routes - LED Control
    SET_LED_STATE --> ENSURE_LED_ACCESS
    SET_LED_STATE --> APPLY_LED_STATE
    SET_LED_STATE --> BROADCAST
    APPLY_LED_STATE --> BROADCAST
    ENSURE_LED_ACCESS --> SECURITY_VERIFY_OWNERSHIP

    %% Device Routes - LED Scheduling
    CREATE_LED_SCHEDULE --> ENSURE_LED_ACCESS
    CREATE_LED_SCHEDULE --> IST_TO_UTC
    CREATE_LED_SCHEDULE --> UTC_TO_IST
    CREATE_LED_TIMER --> ENSURE_LED_ACCESS
    CREATE_LED_TIMER --> GET_IST_NOW
    CREATE_LED_TIMER --> IST_TO_UTC
    CREATE_LED_TIMER --> UTC_TO_IST
    LIST_LED_SCHEDULES --> ENSURE_LED_ACCESS
    LIST_LED_SCHEDULES --> UTC_TO_IST
    LIST_LED_SCHEDULES --> DOC_TO_DICT_UTILS
    CANCEL_LED_SCHEDULE --> ENSURE_LED_ACCESS
    CANCEL_LED_SCHEDULE --> BROADCAST
    LED_SCHEDULE_WORKER --> APPLY_LED_STATE
    LED_SCHEDULE_WORKER --> CREATE_NOTIFICATION
    LED_SCHEDULE_WORKER --> BROADCAST
    LED_SCHEDULE_WORKER --> DOC_TO_DICT_UTILS

    %% Device Routes - Notifications
    CREATE_NOTIFICATION --> BROADCAST
    GET_NOTIFICATIONS --> DOC_TO_DICT_UTILS
    NOTIFICATION_STREAM --> DOC_TO_DICT_UTILS

    %% Device Routes - Webhooks
    CREATE_WEBHOOK --> SECURITY_VERIFY_OWNERSHIP
    CREATE_WEBHOOK --> DOC_TO_DICT_UTILS
    LIST_WEBHOOKS --> DOC_TO_DICT_UTILS
    GET_WEBHOOK --> SECURITY_VERIFY_OWNERSHIP
    GET_WEBHOOK --> DOC_TO_DICT_UTILS
    DELETE_WEBHOOK --> SECURITY_VERIFY_OWNERSHIP
    UPDATE_WEBHOOK --> SECURITY_VERIFY_OWNERSHIP
    UPDATE_WEBHOOK --> DOC_TO_DICT_UTILS
    TRIGGER_WEBHOOKS --> SEND_WEBHOOK

    %% Device Routes - Background Tasks
    AUTO_OFFLINE_CHECKER --> CREATE_NOTIFICATION
    AUTO_OFFLINE_CHECKER --> BROADCAST
    AUTO_OFFLINE_CHECKER --> BROADCAST_EVENT

    %% WebSocket Flow
    WEBSOCKET_ENDPOINT --> CONNECT
    WEBSOCKET_ENDPOINT --> DISCONNECT
    BROADCAST --> CONNECTION_MANAGER

    %% Event Manager Flow
    EVENT_STREAM --> SUBSCRIBE
    EVENT_STREAM --> UNSUBSCRIBE
    BROADCAST_EVENT --> EVENT_MANAGER

    %% Rules Engine Flow
    VALIDATE_RULE --> LOAD_RULES
    VALIDATE_RULE --> GET_RULE_STRING
    VALIDATE_RULE --> DOC_TO_DICT_DB

    %% Styling
    classDef authClass fill:#e1f5ff,stroke:#01579b,stroke-width:2px
    classDef deviceClass fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef utilsClass fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    classDef dbClass fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef wsClass fill:#fce4ec,stroke:#880e4f,stroke-width:2px
    classDef mainClass fill:#f1f8e9,stroke:#33691e,stroke-width:2px

    class GET_CURRENT_USER,SIGNUP,TOKEN,LOGIN,LOGOUT,REFRESH_TOKEN,GET_ME,UPDATE_ME,DELETE_ME,FORGOT_PASSWORD,VERIFY_RESET_TOKEN,RESET_PASSWORD authClass
    class GET_DEVICES,ADD_DEVICE,DELETE_DEVICE,BULK_UPDATE_STATUS,PUSH_TELEMETRY,GET_LATEST_TELEMETRY,GET_TELEMETRY_HISTORY,CREATE_DASHBOARD,LIST_DASHBOARDS,DELETE_DASHBOARD,UPDATE_DASHBOARD_LAYOUT,CREATE_WIDGET,GET_WIDGETS,DELETE_WIDGET,SET_LED_STATE,CREATE_LED_SCHEDULE,CREATE_LED_TIMER,LIST_LED_SCHEDULES,CANCEL_LED_SCHEDULE,CREATE_WEBHOOK,LIST_WEBHOOKS,GET_WEBHOOK,DELETE_WEBHOOK,UPDATE_WEBHOOK deviceClass
    class VERIFY_PASSWORD,GET_PASSWORD_HASH,CREATE_ACCESS_TOKEN,CREATE_REFRESH_TOKEN,SEND_RESET_EMAIL,DOC_TO_DICT_UTILS,GET_IST_NOW,UTC_TO_IST,IST_TO_UTC utilsClass
    class INIT_DB,DOC_TO_DICT_DB dbClass
    class WEBSOCKET_ENDPOINT,CONNECT,DISCONNECT,BROADCAST,CONNECTION_MANAGER wsClass
    class START,STARTUP,SHUTDOWN mainClass
```

## Function Summary by Module

### 🔐 auth_routes.py (12 functions)
- `get_current_user` - JWT token validation
- `signup` - User registration
- `token` - OAuth2 token endpoint
- `login` - User login
- `logout` - User logout
- `refresh_token` - Token refresh
- `get_me` - Get current user profile
- `update_me` - Update user profile
- `delete_me` - Delete user account
- `forgot_password` - Request password reset
- `verify_reset_token` - Verify reset token
- `reset_password` - Reset password

### 📱 device_routes.py (40 functions)
**Helper Functions:**
- `safe_oid` - Safe ObjectId conversion
- `SecurityRules.verify_ownership` - Ownership verification
- `SecurityRules.verify_device_token` - Device token verification
- `create_notification` - Create notification
- `trigger_webhooks` - Trigger webhooks
- `send_webhook` - Send webhook HTTP request
- `compute_next_virtual_pin` - Compute next virtual pin
- `apply_led_state` - Apply LED state
- `ensure_led_widget_access` - Ensure LED widget access

**Device Endpoints:**
- `get_devices` - List devices
- `add_device` - Create device
- `delete_device` - Delete device
- `bulk_update_device_status` - Bulk status update

**Telemetry Endpoints:**
- `push_telemetry` - Push telemetry data
- `get_latest_telemetry_by_token` - Get latest telemetry
- `get_telemetry_history` - Get telemetry history

**Dashboard Endpoints:**
- `create_dashboard` - Create dashboard
- `list_dashboards` - List dashboards
- `delete_dashboard` - Delete dashboard
- `update_dashboard_layout` - Update dashboard layout

**Widget Endpoints:**
- `create_widget` - Create widget
- `get_widgets` - Get widgets
- `delete_widget` - Delete widget
- `patch_widget` - Update widget

**LED Control:**
- `set_led_state` - Set LED state
- `create_led_schedule` - Create LED schedule
- `create_led_timer` - Create LED timer
- `list_led_schedules` - List LED schedules
- `cancel_led_schedule` - Cancel LED schedule
- `led_schedule_worker` - Background worker for schedules

**Notifications:**
- `notifications_health` - Health check
- `get_notifications` - Get notifications
- `mark_notification_read` - Mark as read
- `mark_all_notifications_read` - Mark all as read
- `delete_notification` - Delete notification
- `notification_stream` - SSE stream

**Webhooks:**
- `create_webhook` - Create webhook
- `list_webhooks` - List webhooks
- `get_webhook` - Get webhook
- `delete_webhook` - Delete webhook
- `update_webhook` - Update webhook

**Background Tasks:**
- `auto_offline_checker` - Auto-offline checker

### 🛠️ utils.py (9 functions)
- `verify_password` - Verify password
- `get_password_hash` - Hash password
- `create_access_token` - Create JWT access token
- `create_refresh_token` - Create JWT refresh token
- `send_reset_email` - Send password reset email
- `doc_to_dict` - Convert MongoDB doc to dict
- `get_ist_now` - Get current IST time
- `utc_to_ist` - Convert UTC to IST
- `ist_to_utc` - Convert IST to UTC

### 💾 db.py (2 functions)
- `init_db` - Initialize database indexes
- `doc_to_dict` - Convert MongoDB doc to dict

### 🛡️ rules_engine.py (4 functions)
- `RulesEngine.__init__` - Initialize rules engine
- `load_rules` - Load security rules
- `_get_rule_string` - Get rule string
- `validate_rule` - Validate security rule

### 🔌 websocket_routes.py (1 function)
- `websocket_endpoint` - WebSocket endpoint handler

### 📡 websocket_manager.py (6 functions)
- `ConnectionManager.__init__` - Initialize manager
- `connect` - Connect WebSocket
- `disconnect` - Disconnect WebSocket
- `broadcast` - Broadcast to user
- `broadcast_to_all` - Broadcast to all users
- `get_connection_count` - Get connection count
- `get_connected_users` - Get connected users

### 📢 events.py (1 function)
- `event_stream` - SSE event stream

### 📬 event_manager.py (3 functions)
- `EventManager.__init__` - Initialize event manager
- `subscribe` - Subscribe to events
- `unsubscribe` - Unsubscribe from events
- `broadcast` - Broadcast event

### 🌐 api_gateway.py (3 functions)
- `RateLimiter.__init__` - Initialize rate limiter
- `RateLimiter.__call__` - Rate limiter middleware
- `root` - Root endpoint
- `health_check` - Health check endpoint

### 🚀 main.py (2 functions)
- `startup_event` - Application startup
- `shutdown_event` - Application shutdown

## Total Function Count: **83 functions**

