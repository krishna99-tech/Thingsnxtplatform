# ESP32 Telemetry API Endpoints - Production Guide

## Overview

This document outlines the telemetry API endpoints for ESP32 device communication with the ThingsNXT IoT Platform.

## Available Telemetry Endpoints

### 1. **V2 Endpoint (Recommended for ESP32)** ✅

**Path:** `POST /devices/{device_id}/telemetry`

**Advantages:**

- RESTful design with device_id in path
- Cleaner URL structure
- Better for production deployments
- Easier to debug and monitor

**Request:**

```http
POST /devices/{device_id}/telemetry
Content-Type: application/json

{
  "device_token": "your_device_token_here",
  "data": {
    "v0": 1,
    "v1": 0,
    "temperature": 25.5,
    "humidity": 60
  }
}
```

**Response:**

```json
{
  "message": "ok",
  "device_id": "6123456abcdef12345678901",
  "data": {
    "v0": 1,
    "v1": 0,
    "temperature": 25.5,
    "humidity": 60
  },
  "timestamp": "2026-02-12T16:00:00.000Z"
}
```

**ESP32 Implementation:**

```cpp
// Configuration
const char* DEVICE_ID = "6123456abcdef12345678901";  // Get from platform
const char* DEVICE_TOKEN = "your_device_token_here";
const char* API_BASE_URL = "http://192.168.1.103:8000";

// Send telemetry
void sendTelemetry() {
  HTTPClient http;
  String url = String(API_BASE_URL) + "/devices/" + String(DEVICE_ID) + "/telemetry";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(1024);
  doc["device_token"] = DEVICE_TOKEN;
  JsonObject data = doc.createNestedObject("data");
  data["v0"] = ledState0;
  data["v1"] = ledState1;
  data["temperature"] = readTemperature();

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);
  if (httpCode == 200) {
    Serial.println("✅ Telemetry sent");
  }
  http.end();
}
```

---

### 2. **Legacy Endpoint (Backward Compatible)**

**Path:** `POST /telemetry`

**Request:**

```http
POST /telemetry
Content-Type: application/json

{
  "device_token": "your_device_token_here",
  "data": {
    "v0": 1,
    "temperature": 25.5
  }
}
```

**Response:**

```json
{
  "message": "ok",
  "device_id": "6123456abcdef12345678901",
  "led": 1,
  "updated_data": {
    "v0": 1,
    "temperature": 25.5
  },
  "timestamp": "2026-02-12T16:00:00.000Z"
}
```

---

### 3. **Get Latest Telemetry**

**Path:** `GET /telemetry/latest?device_token={token}`

**Use Case:** ESP32 polling for latest state from server

**Request:**

```http
GET /telemetry/latest?device_token=your_device_token_here
```

**Response:**

```json
{
  "device_id": "6123456abcdef12345678901",
  "data": {
    "v0": 1,
    "v1": 0,
    "temperature": 25.5
  },
  "timestamp": "2026-02-12T16:00:00.000Z"
}
```

**ESP32 Implementation:**

```cpp
void pollLEDStates() {
  HTTPClient http;
  String url = String(API_BASE_URL) + "/telemetry/latest?device_token=" + String(DEVICE_TOKEN);

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, payload);

    JsonObject data = doc["data"];
    int v0State = data["v0"];
    int v1State = data["v1"];

    // Update LEDs based on server state
    updateLED(0, v0State);
    updateLED(1, v1State);
  }
  http.end();
}
```

---

## Production Deployment Configuration

### Environment Variables

Set these in your production environment:

```bash
# Backend Configuration
API_BASE_URL=https://api.thingsnxt.com
PORT=8000

# For ESP32 devices
PRODUCTION_API_URL=https://api.thingsnxt.com
```

### ESP32 Configuration Modes

#### Development Mode

```cpp
// Development - Local Network
const char* API_BASE_URL = "http://192.168.1.103:8000";
const char* DEVICE_ID = "6123456abcdef12345678901";
const char* DEVICE_TOKEN = "dev_token_12345";
```

#### Production Mode

```cpp
// Production - Cloud Deployment
const char* API_BASE_URL = "https://api.thingsnxt.com";
const char* DEVICE_ID = "6123456abcdef12345678901";
const char* DEVICE_TOKEN = "prod_token_secure_67890";
```

---

## Security Best Practices

### 1. **Device Token Security**

- Never hardcode production tokens in source code
- Use EEPROM or SPIFFS to store tokens
- Rotate tokens periodically

```cpp
#include <Preferences.h>

Preferences preferences;

void setup() {
  preferences.begin("iot-config", false);
  String deviceToken = preferences.getString("device_token", "");
  String deviceId = preferences.getString("device_id", "");

  if (deviceToken.length() == 0) {
    Serial.println("❌ No device token found. Please provision device.");
  }
}
```

### 2. **HTTPS for Production**

```cpp
#include <WiFiClientSecure.h>

WiFiClientSecure client;
client.setInsecure(); // For testing only

// Production: Use certificate validation
const char* root_ca = \
"-----BEGIN CERTIFICATE-----\n" \
"MIIDrzCCApegAwIBAgIQCDvgVpBCRrGhdWrJWZHHSjANBgkqhkiG9w0BAQUFADBh\n" \
// ... certificate content
"-----END CERTIFICATE-----\n";

client.setCACert(root_ca);
```

### 3. **Rate Limiting Awareness**

The backend has rate limiting (100 requests/60 seconds). For ESP32:

- Use reasonable polling intervals (3-10 seconds)
- Implement exponential backoff on errors
- Cache data locally when possible

```cpp
const unsigned long POLL_INTERVAL = 5000;  // 5 seconds
const unsigned long TELEMETRY_INTERVAL = 10000;  // 10 seconds
const int MAX_RETRIES = 3;
const unsigned long RETRY_DELAY = 2000;  // 2 seconds

void sendTelemetryWithRetry() {
  int retries = 0;
  while (retries < MAX_RETRIES) {
    if (sendTelemetry()) {
      return;  // Success
    }
    retries++;
    delay(RETRY_DELAY * retries);  // Exponential backoff
  }
  Serial.println("❌ Failed to send telemetry after retries");
}
```

---

## API Response Codes

| Code | Meaning           | Action                    |
| ---- | ----------------- | ------------------------- |
| 200  | Success           | Continue normal operation |
| 400  | Bad Request       | Check payload format      |
| 403  | Forbidden         | Invalid device_token      |
| 404  | Not Found         | Device doesn't exist      |
| 429  | Too Many Requests | Reduce polling frequency  |
| 500  | Server Error      | Retry with backoff        |

---

## Complete ESP32 Example (Production Ready)

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>

// Configuration
Preferences preferences;
String DEVICE_ID;
String DEVICE_TOKEN;
String API_BASE_URL;

// WiFi credentials (can also be stored in Preferences)
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Timing
const unsigned long POLL_INTERVAL = 5000;
const unsigned long TELEMETRY_INTERVAL = 10000;
unsigned long lastPollTime = 0;
unsigned long lastTelemetryTime = 0;

void setup() {
  Serial.begin(115200);

  // Load configuration from EEPROM
  preferences.begin("iot-config", false);
  DEVICE_ID = preferences.getString("device_id", "");
  DEVICE_TOKEN = preferences.getString("device_token", "");
  API_BASE_URL = preferences.getString("api_url", "http://192.168.1.103:8000");

  if (DEVICE_ID.length() == 0 || DEVICE_TOKEN.length() == 0) {
    Serial.println("❌ Device not provisioned!");
    return;
  }

  connectWiFi();

  // Initialize GPIO pins
  pinMode(2, OUTPUT);
  pinMode(4, OUTPUT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }

  unsigned long now = millis();

  // Poll for updates
  if (now - lastPollTime >= POLL_INTERVAL) {
    pollServerState();
    lastPollTime = now;
  }

  // Send telemetry
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    sendTelemetry();
    lastTelemetryTime = now;
  }

  delay(100);
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ WiFi connected");
}

bool sendTelemetry() {
  HTTPClient http;
  String url = API_BASE_URL + "/devices/" + DEVICE_ID + "/telemetry";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);

  DynamicJsonDocument doc(1024);
  doc["device_token"] = DEVICE_TOKEN;
  JsonObject data = doc.createNestedObject("data");
  data["v0"] = digitalRead(2);
  data["v1"] = digitalRead(4);
  data["uptime"] = millis() / 1000;

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);
  bool success = (httpCode == 200);

  if (success) {
    Serial.println("✅ Telemetry sent");
  } else {
    Serial.printf("❌ Failed: %d\n", httpCode);
  }

  http.end();
  return success;
}

void pollServerState() {
  HTTPClient http;
  String url = API_BASE_URL + "/telemetry/latest?device_token=" + DEVICE_TOKEN;

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String response = http.getString();
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, response);

    JsonObject data = doc["data"];
    if (!data.isNull()) {
      if (data.containsKey("v0")) {
        digitalWrite(2, data["v0"].as<int>());
      }
      if (data.containsKey("v1")) {
        digitalWrite(4, data["v1"].as<int>());
      }
    }
  }

  http.end();
}
```

---

## Provisioning ESP32 Devices

### Option 1: Serial Provisioning

```cpp
void provisionDevice() {
  Serial.println("Enter Device ID:");
  while (!Serial.available()) delay(100);
  String deviceId = Serial.readStringUntil('\n');

  Serial.println("Enter Device Token:");
  while (!Serial.available()) delay(100);
  String deviceToken = Serial.readStringUntil('\n');

  preferences.putString("device_id", deviceId);
  preferences.putString("device_token", deviceToken);

  Serial.println("✅ Device provisioned!");
}
```

### Option 2: WiFi Provisioning Portal

Use libraries like WiFiManager for user-friendly setup.

---

## Monitoring and Debugging

### Enable Debug Logging

```cpp
#define DEBUG_MODE 1

#if DEBUG_MODE
  #define DEBUG_PRINT(x) Serial.print(x)
  #define DEBUG_PRINTLN(x) Serial.println(x)
#else
  #define DEBUG_PRINT(x)
  #define DEBUG_PRINTLN(x)
#endif
```

### Health Check

```cpp
void checkHealth() {
  HTTPClient http;
  String url = API_BASE_URL + "/health";

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    Serial.println("✅ Backend healthy");
  } else {
    Serial.println("⚠️ Backend unhealthy");
  }
  http.end();
}
```

---

## Troubleshooting

### Common Issues

1. **403 Forbidden**
   - Check device_token is correct
   - Verify device exists in platform
   - Ensure token hasn't been regenerated

2. **429 Too Many Requests**
   - Increase polling intervals
   - Implement exponential backoff
   - Check for infinite retry loops

3. **Connection Timeout**
   - Verify API_BASE_URL is correct
   - Check network connectivity
   - Increase http.setTimeout() value

4. **JSON Parsing Errors**
   - Increase DynamicJsonDocument size
   - Validate JSON format
   - Check for special characters

---

## Performance Optimization

### 1. **Reduce Memory Usage**

```cpp
// Use StaticJsonDocument for known sizes
StaticJsonDocument<512> doc;

// Reuse HTTP client
HTTPClient http;  // Declare once globally
```

### 2. **Batch Updates**

```cpp
// Send multiple sensor readings in one request
data["temperature"] = temp;
data["humidity"] = humidity;
data["pressure"] = pressure;
data["v0"] = led0;
data["v1"] = led1;
```

### 3. **Conditional Updates**

```cpp
// Only send telemetry if values changed
if (hasDataChanged()) {
  sendTelemetry();
}
```

---

## Next Steps

1. ✅ Use V2 endpoint for new deployments
2. ✅ Implement HTTPS for production
3. ✅ Store credentials securely
4. ✅ Add error handling and retries
5. ✅ Monitor device health
6. ✅ Implement OTA updates for remote firmware updates
