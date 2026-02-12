# Bidirectional ESP32 Communication Guide

## Overview

This guide explains how to achieve **parallel read/write** operations between your app and ESP32 devices:

- **App → ESP32**: Control devices (LEDs, relays, etc.) from the app
- **ESP32 → Database**: Send sensor data (temperature, humidity, etc.) to the database

## Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│  Mobile/Web │◄───────►│   Backend    │◄───────►│    ESP32    │
│     App     │         │   + Database │         │   Device    │
└─────────────┘         └──────────────┘         └─────────────┘
      │                        │                        │
      │ 1. User clicks LED ON  │                        │
      ├───────────────────────►│                        │
      │                        │ 2. Store in DB         │
      │                        │ 3. Broadcast via WS    │
      │                        │                        │
      │                        │◄───────────────────────┤
      │                        │ 4. ESP32 polls state   │
      │                        ├───────────────────────►│
      │                        │ 5. Returns LED=ON      │
      │                        │                        │
      │                        │◄───────────────────────┤
      │                        │ 6. ESP32 sends data    │
      │◄───────────────────────┤ 7. Broadcast to app    │
      │ 8. App shows temp data │                        │
```

## How It Works Currently ✅

### 1. **App Controls ESP32** (App → ESP32)

#### Step 1: User Clicks LED Button in App

```javascript
// Frontend (React Native / Web)
const toggleLED = async (widgetId, newState) => {
  const response = await fetch(`${API_URL}/widgets/${widgetId}/state`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ state: newState }),
  });
};
```

#### Step 2: Backend Updates Database

```python
# device_routes.py - Line 538
@router.post("/widgets/{widget_id}/state")
async def set_led_state(widget_id: str, body: Dict[str, Any], ...):
    # 1. Validate widget ownership
    # 2. Update telemetry database with new LED state
    await apply_led_state(device_id, bool_state, virtual_pin)
    # 3. Broadcast via WebSocket to all connected clients
    await manager.broadcast(user_id, {
        "type": "telemetry_update",
        "device_id": str(device_id),
        "data": {virtual_pin: state}
    })
```

#### Step 3: ESP32 Polls for State

```cpp
// ESP32 polls every 5 seconds
void pollServerState() {
  HTTPClient http;
  String url = apiBaseUrl + "/telemetry/latest?device_token=" + deviceToken;

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, payload);

    JsonObject data = doc["data"];
    int v0State = data["v0"];  // Get LED state from server

    // Update physical LED
    digitalWrite(2, v0State ? HIGH : LOW);
  }
  http.end();
}
```

### 2. **ESP32 Sends Data to Database** (ESP32 → Database)

#### Step 1: ESP32 Reads Sensors

```cpp
// ESP32 reads sensors every 10 seconds
void sendTelemetry() {
  // Read sensors
  float temperature = readTemperature();
  float humidity = readHumidity();
  int ledState = digitalRead(2);

  // Build JSON payload
  DynamicJsonDocument doc(1024);
  doc["device_token"] = deviceToken;
  JsonObject data = doc.createNestedObject("data");

  // Add sensor data
  data["temperature"] = temperature;
  data["humidity"] = humidity;
  data["v0"] = ledState;
  data["uptime"] = millis() / 1000;

  // Send to backend
  HTTPClient http;
  String url = apiBaseUrl + "/devices/" + deviceId + "/telemetry";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String payload;
  serializeJson(doc, payload);
  http.POST(payload);
  http.end();
}
```

#### Step 2: Backend Stores in Database

```python
# device_routes.py - Line 759
@router.post("/devices/{device_id}/telemetry")
async def push_telemetry_v2(device_id: str, data: TelemetryData):
    # 1. Validate device_token
    # 2. Store telemetry in database
    await db.telemetry.update_one(
        {"device_id": dev_oid, "key": "telemetry_json"},
        {"$set": {"value": payload, "timestamp": now}},
        upsert=True
    )
    # 3. Update device status to "online"
    # 4. Broadcast to WebSocket clients
    await manager.broadcast(user_id, {
        "type": "telemetry_update",
        "device_id": device_id,
        "data": payload  # Contains temperature, humidity, etc.
    })
```

#### Step 3: App Receives Data

```javascript
// Frontend WebSocket listener
websocket.onmessage = (event) => {
  const message = JSON.parse(event.data);

  if (message.type === "telemetry_update") {
    // Update UI with sensor data
    setTemperature(message.data.temperature);
    setHumidity(message.data.humidity);
    setLedState(message.data.v0);
  }
};
```

## Complete ESP32 Example (Bidirectional)

```cpp
/*
 * ESP32 Bidirectional Communication
 * - Receives control commands from app
 * - Sends sensor data to database
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>  // For temperature/humidity sensor

// Configuration
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";
const char* API_BASE_URL = "http://192.168.1.103:8000";
const char* DEVICE_ID = "6123456abcdef12345678901";
const char* DEVICE_TOKEN = "your_device_token";

// Hardware
#define LED_PIN 2
#define DHT_PIN 4
#define DHT_TYPE DHT22

DHT dht(DHT_PIN, DHT_TYPE);

// Timing
const unsigned long POLL_INTERVAL = 3000;      // Poll for commands every 3s
const unsigned long TELEMETRY_INTERVAL = 10000; // Send data every 10s

unsigned long lastPollTime = 0;
unsigned long lastTelemetryTime = 0;

// State
int currentLedState = 0;
float lastTemperature = 0;
float lastHumidity = 0;

void setup() {
  Serial.begin(115200);

  // Initialize hardware
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  dht.begin();

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ WiFi connected");
}

void loop() {
  unsigned long now = millis();

  // 1. RECEIVE: Poll for control commands from app
  if (now - lastPollTime >= POLL_INTERVAL) {
    pollControlCommands();
    lastPollTime = now;
  }

  // 2. SEND: Send sensor data to database
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    sendSensorData();
    lastTelemetryTime = now;
  }

  delay(100);
}

// ============================================
// RECEIVE: Get control commands from app
// ============================================
void pollControlCommands() {
  HTTPClient http;
  String url = String(API_BASE_URL) + "/telemetry/latest?device_token=" + String(DEVICE_TOKEN);

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, payload);

    JsonObject data = doc["data"];

    // Check for LED control command
    if (data.containsKey("v0")) {
      int newLedState = data["v0"];
      if (newLedState != currentLedState) {
        currentLedState = newLedState;
        digitalWrite(LED_PIN, currentLedState ? HIGH : LOW);
        Serial.printf("🔄 LED: %s (from app)\n", currentLedState ? "ON" : "OFF");
      }
    }

    // You can also receive other commands
    if (data.containsKey("relay1")) {
      int relayState = data["relay1"];
      // Control relay
    }
  }

  http.end();
}

// ============================================
// SEND: Send sensor data to database
// ============================================
void sendSensorData() {
  // Read sensors
  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();

  // Validate readings
  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("❌ Failed to read sensor");
    return;
  }

  // Build JSON payload
  HTTPClient http;
  String url = String(API_BASE_URL) + "/devices/" + String(DEVICE_ID) + "/telemetry";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(1024);
  doc["device_token"] = DEVICE_TOKEN;
  JsonObject data = doc.createNestedObject("data");

  // Add sensor readings
  data["temperature"] = temperature;
  data["humidity"] = humidity;
  data["v0"] = currentLedState;  // Current LED state
  data["uptime"] = millis() / 1000;
  data["rssi"] = WiFi.RSSI();

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);

  if (httpCode == 200) {
    Serial.printf("📤 Sent: Temp=%.1f°C, Humidity=%.1f%%\n", temperature, humidity);
    lastTemperature = temperature;
    lastHumidity = humidity;
  } else {
    Serial.printf("❌ Failed to send: %d\n", httpCode);
  }

  http.end();
}
```

## Timing Recommendations

### Optimal Intervals

```cpp
// Fast response to user commands
const unsigned long POLL_INTERVAL = 3000;  // 3 seconds

// Reasonable sensor data updates
const unsigned long TELEMETRY_INTERVAL = 10000;  // 10 seconds

// For critical controls (e.g., door locks)
const unsigned long CRITICAL_POLL_INTERVAL = 1000;  // 1 second
```

### Rate Limiting Considerations

- Backend allows **100 requests per 60 seconds**
- With 3s poll + 10s telemetry = ~26 requests/minute ✅
- Leaves headroom for retries and other operations

## Advanced: WebSocket for Real-time Control

For **instant** control without polling, use WebSocket on ESP32:

```cpp
#include <WebSocketsClient.h>

WebSocketsClient webSocket;

void setup() {
  // ... WiFi setup ...

  // Connect to WebSocket
  webSocket.begin("192.168.1.103", 8000, "/ws");
  webSocket.onEvent(webSocketEvent);
}

void loop() {
  webSocket.loop();

  // Still send telemetry via HTTP
  if (millis() - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    sendSensorData();
    lastTelemetryTime = millis();
  }
}

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  if (type == WStype_TEXT) {
    DynamicJsonDocument doc(1024);
    deserializeJson(doc, payload);

    if (doc["type"] == "telemetry_update") {
      JsonObject data = doc["data"];
      if (data.containsKey("v0")) {
        int ledState = data["v0"];
        digitalWrite(LED_PIN, ledState ? HIGH : LOW);
        Serial.printf("⚡ Instant LED update: %s\n", ledState ? "ON" : "OFF");
      }
    }
  }
}
```

## Data Flow Summary

### App Controls Device (Write)

```
User clicks button → App API call → Backend updates DB →
ESP32 polls → ESP32 reads new state → Physical LED changes
```

**Latency:** 0-3 seconds (based on poll interval)

### Device Sends Data (Read)

```
ESP32 reads sensor → HTTP POST to backend → Backend stores in DB →
WebSocket broadcast → App receives update → UI updates
```

**Latency:** <500ms (real-time via WebSocket)

## Complete Workflow Example

### Scenario: Smart Home Temperature Control

```cpp
// ESP32 Code
void loop() {
  // 1. Read temperature sensor
  float temp = dht.readTemperature();

  // 2. Send to database
  sendTelemetry(temp);

  // 3. Check for AC control command from app
  pollControlCommands();

  // 4. If app sent "turn on AC", execute
  if (acShouldBeOn) {
    digitalWrite(AC_RELAY_PIN, HIGH);
  }
}
```

```javascript
// App Code
const TemperatureControl = () => {
  const [temperature, setTemperature] = useState(0);
  const [acState, setAcState] = useState(false);

  // Receive temperature from ESP32
  useEffect(() => {
    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "telemetry_update") {
        setTemperature(data.data.temperature);
      }
    };
  }, []);

  // Send AC control to ESP32
  const toggleAC = async () => {
    await fetch(`${API_URL}/widgets/${acWidgetId}/state`, {
      method: "POST",
      body: JSON.stringify({ state: !acState }),
    });
    setAcState(!acState);
  };

  return (
    <View>
      <Text>Temperature: {temperature}°C</Text>
      <Button onPress={toggleAC}>
        {acState ? "Turn OFF AC" : "Turn ON AC"}
      </Button>
    </View>
  );
};
```

## Key Points

✅ **Parallel Operation**: ESP32 can simultaneously:

- Poll for commands (every 3s)
- Send sensor data (every 10s)
- Both operations are independent

✅ **Bidirectional**:

- **App → ESP32**: Control via widget state updates
- **ESP32 → App**: Data via telemetry updates

✅ **Real-time**: WebSocket ensures instant updates to app

✅ **Reliable**: HTTP polling ensures ESP32 gets commands even without WebSocket

## Testing Checklist

- [ ] App can control LED on ESP32
- [ ] ESP32 LED changes within 3 seconds
- [ ] ESP32 sends temperature data
- [ ] App displays temperature in real-time
- [ ] Multiple sensors work simultaneously
- [ ] Commands work while sending data
- [ ] Works with poor network conditions
- [ ] Handles backend restarts gracefully

## Troubleshooting

**Q: ESP32 not receiving commands from app?**

- Check device_token is correct
- Verify widget is linked to correct device
- Check ESP32 polling interval (reduce to 1s for testing)
- Check backend logs for errors

**Q: App not receiving sensor data?**

- Verify WebSocket connection
- Check telemetry endpoint returns 200 OK
- Ensure data keys match (temperature, humidity, etc.)
- Check browser console for WebSocket messages

**Q: Delayed responses?**

- Reduce POLL_INTERVAL for faster command response
- Use WebSocket for instant updates
- Check network latency

This architecture gives you full bidirectional control! 🚀
