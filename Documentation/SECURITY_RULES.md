# Security Rules Documentation

## Overview
The ThingsNXT Platform uses a Firebase-like security rules engine to enforce access control across all collections. Rules are defined in `security_rules.json` and evaluated by `rules_engine.py`.

## Rule Structure

Rules follow this pattern:
```json
{
  "collection_name": {
    "$resource_id": {
      ".read": "rule_expression",
      ".write": "rule_expression"
    }
  }
}
```

## Available Context Variables

### `auth.uid`
- The current authenticated user's ID (as string)
- Available in all rule expressions
- Example: `auth.uid == data.user_id`

### `data`
- The resource document being accessed
- Contains all fields from the MongoDB document
- IDs are converted to strings for comparison
- Example: `data.user_id`, `data.device_id`

### `root`
- Allows access to other collections for cross-referencing
- Syntax: `root.collection_name[resource_id]`
- Example: `root.dashboards[data.dashboard_id].user_id`

## Collections and Rules

### 1. Users (`users`)
- **Read**: Users can only read their own profile
- **Write**: Users can only update their own profile
- **Rule**: `auth.uid == data.id or auth.uid == data._id`

### 2. Devices (`devices`)
- **Read**: Users can only read their own devices
- **Write**: Users can only create/update/delete their own devices
- **Rule**: `auth.uid == data.user_id`

### 3. Dashboards (`dashboards`)
- **Read**: Users can only read their own dashboards
- **Write**: Users can only create/update/delete their own dashboards
- **Rule**: `auth.uid == data.user_id`

### 4. Widgets (`widgets`)
- **Read**: Users can read widgets if they own the parent dashboard
- **Write**: Users can write widgets if they own the parent dashboard
- **Rule**: `auth.uid == root.dashboards[data.dashboard_id].user_id`
- **Note**: Uses root lookup to check dashboard ownership

### 5. Telemetry (`telemetry`)
- **Read**: Users can read telemetry for their own devices
- **Write**: Users can write telemetry if they own the device OR have the device token
- **Rule**: 
  - Read: `auth.uid == root.devices[data.device_id].user_id`
  - Write: `auth.uid == root.devices[data.device_id].user_id or data.device_token == root.devices[data.device_id].device_token`
- **Note**: Allows device token authentication for IoT devices

### 6. Notifications (`notifications`)
- **Read**: Users can only read their own notifications
- **Write**: Users can only create/update/delete their own notifications
- **Rule**: `auth.uid == data.user_id`

### 7. Webhooks (`webhooks`)
- **Read**: Users can only read their own webhooks
- **Write**: Users can only create/update/delete their own webhooks
- **Rule**: `auth.uid == data.user_id`

### 8. LED Schedules (`led_schedules`)
- **Read**: Users can read schedules if they own the parent widget's dashboard
- **Write**: Users can write schedules if they own the parent widget's dashboard
- **Rule**: `auth.uid == root.widgets[data.widget_id].dashboard_id and auth.uid == root.dashboards[root.widgets[data.widget_id].dashboard_id].user_id`
- **Note**: Uses nested root lookups to verify widget and dashboard ownership

### 9. Refresh Tokens (`refresh_tokens`)
- **Read**: Users can only read their own refresh tokens
- **Write**: Users can only create/update/delete their own refresh tokens
- **Rule**: `auth.uid == data.user_id`

### 10. Reset Tokens (`reset_tokens`)
- **Read**: Public (no authentication required for password reset)
- **Write**: Public (no authentication required for password reset)
- **Rule**: `True`
- **Note**: Special case - allows password reset without authentication

## Rule Evaluation

### Default Behavior
- **If no rule is defined**: Access is **DENIED** (secure by default)
- **If rule evaluation fails**: Access is **DENIED**
- **If rule returns `True`**: Access is **GRANTED**
- **If rule returns `False`**: Access is **DENIED**

### Performance
- Rules are cached and compiled for performance
- Rules file is checked every 60 seconds (configurable)
- Root lookups are fetched asynchronously only when needed

## Integration Points

### SecurityRules Class (`device_routes.py`)
```python
# Verify ownership using rules engine
await SecurityRules.verify_ownership(db.devices, device_id, user_id, "Device")
```

### Direct Rules Engine Usage
```python
from rules_engine import rules_engine

# Validate a rule directly
is_allowed = await rules_engine.validate_rule(
    collection_name="devices",
    operation=".write",
    user_id=user_id,
    resource_data=device_doc
)
```

## Adding New Rules

1. **Add rule to `security_rules.json`**:
```json
{
  "new_collection": {
    "$resource_id": {
      ".read": "auth.uid == data.user_id",
      ".write": "auth.uid == data.user_id"
    }
  }
}
```

2. **Use in code**:
```python
# In SecurityRules.verify_ownership or directly
is_allowed = await rules_engine.validate_rule(
    "new_collection", ".write", user_id, resource_data
)
```

## Testing Rules

### Manual Testing
```python
# Test a rule
from rules_engine import rules_engine

test_resource = {"user_id": "user123", "name": "Test"}
result = await rules_engine.validate_rule(
    "devices", ".write", "user123", test_resource
)
assert result == True
```

### Common Patterns

#### Owner-Only Access
```json
".read": "auth.uid == data.user_id",
".write": "auth.uid == data.user_id"
```

#### Parent Resource Ownership
```json
".read": "auth.uid == root.parent_collection[data.parent_id].user_id",
".write": "auth.uid == root.parent_collection[data.parent_id].user_id"
```

#### Public Read, Owner Write
```json
".read": "True",
".write": "auth.uid == data.user_id"
```

#### Token-Based Access
```json
".write": "auth.uid == root.devices[data.device_id].user_id or data.device_token == root.devices[data.device_id].device_token"
```

## Security Best Practices

1. **Default Deny**: Always deny access if no rule matches
2. **Least Privilege**: Grant minimum necessary permissions
3. **Validate Input**: Rules should validate all required fields
4. **Test Rules**: Test rules with various scenarios
5. **Monitor Logs**: Check rule evaluation errors in logs
6. **Regular Review**: Review and update rules as needed

## Troubleshooting

### Rule Not Working
1. Check rule syntax in `security_rules.json`
2. Verify collection name matches exactly
3. Check logs for rule evaluation errors
4. Ensure `auth.uid` and `data` fields are correct

### Performance Issues
1. Reduce root lookups if possible
2. Cache frequently accessed documents
3. Optimize rule expressions

### Access Denied Unexpectedly
1. Verify user_id format (should be string)
2. Check if resource exists
3. Validate rule expression logic
4. Check logs for specific error messages

