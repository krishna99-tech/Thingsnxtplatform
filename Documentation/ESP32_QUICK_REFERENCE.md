# ESP32 Quick Reference Card

## 🚀 Quick Start (5 Minutes)

### 1. Backend Endpoints (Already Working!)

```
✅ POST /devices/{device_id}/telemetry    - ESP32 sends data
✅ GET  /telemetry/latest?device_token=xxx - ESP32 gets commands
✅ POST /widgets/{widget_id}/state         - App controls device
```

### 2. ESP32 Setup

```cpp
// 1. Configure WiFi
const char* WIFI_SSID = "YourWiFi";
const char* WIFI_PASSWORD = "YourPassword";

// 2. Set API URL
const char* API_BASE_URL = "http://192.168.1.103:8000";

// 3. Get from platform
const char* DEVICE_ID = "your_device_id";
const char* DEVICE_TOKEN = "your_device_token";

// 4. Upload esp32_production_ready.ino
```

### 3. Test

```
1. ESP32 sends data → Check app updates
2. App clicks LED → Check ESP32 LED changes
```

---

## 📡 Bidirectional Communication

### App Controls ESP32 (Write)

```javascript
// App sends command
fetch("/widgets/123/state", {
  method: "POST",
  body: JSON.stringify({ state: 1 }),
});
```

↓ Backend stores in DB ↓

```cpp
// ESP32 polls (every 3s)
GET /telemetry/latest?device_token=xxx
// Response: { "data": { "v0": 1 } }
digitalWrite(LED_PIN, HIGH);  // LED ON
```

### ESP32 Sends Data (Read)

```cpp
// ESP32 reads sensor
float temp = dht.readTemperature();

// ESP32 sends (every 10s)
POST /devices/{id}/telemetry
Body: {
  "device_token": "xxx",
  "data": { "temperature": 25.5, "v0": 1 }
}
```

↓ Backend stores and broadcasts ↓

```javascript
// App receives via WebSocket
websocket.onmessage = (event) => {
  const data = JSON.parse(event.data);
  setTemperature(data.data.temperature);
};
```

---

## 🔧 ESP32 Code Template

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID = "your_device_id";
const char* DEVICE_TOKEN = "your_token";
const char* API_URL = "http://192.168.1.103:8000";

void loop() {
  // 1. Poll for commands (every 3s)
  if (millis() - lastPoll >= 3000) {
    pollCommands();
    lastPoll = millis();
  }

  // 2. Send data (every 10s)
  if (millis() - lastSend >= 10000) {
    sendData();
    lastSend = millis();
  }
}

void pollCommands() {
  HTTPClient http;
  http.begin(String(API_URL) + "/telemetry/latest?device_token=" + DEVICE_TOKEN);
  int code = http.GET();
  if (code == 200) {
    DynamicJsonDocument doc(2048);
    deserializeJson(doc, http.getString());
    int ledState = doc["data"]["v0"];
    digitalWrite(2, ledState ? HIGH : LOW);
  }
  http.end();
}

void sendData() {
  HTTPClient http;
  http.begin(String(API_URL) + "/devices/" + DEVICE_ID + "/telemetry");
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(1024);
  doc["device_token"] = DEVICE_TOKEN;
  JsonObject data = doc.createNestedObject("data");
  data["temperature"] = 25.5;
  data["v0"] = digitalRead(2);

  String payload;
  serializeJson(doc, payload);
  http.POST(payload);
  http.end();
}
```

---

## ⚙️ Configuration

### Development

```cpp
const char* API_URL = "http://192.168.1.103:8000";
```

### Production

```cpp
const char* API_URL = "https://api.thingsnxt.com";
```

---

## ⏱️ Timing

| Operation     | Interval | Requests/min |
| ------------- | -------- | ------------ |
| Poll commands | 3s       | 20           |
| Send data     | 10s      | 6            |
| **Total**     | -        | **26** ✅    |
| Rate limit    | -        | 100          |

---

## 🔍 Troubleshooting

### ESP32 not receiving commands?

```cpp
// 1. Check Serial Monitor
Serial.println("Polling...");

// 2. Reduce interval
const unsigned long POLL_INTERVAL = 1000;  // 1s for testing

// 3. Verify token
Serial.println(DEVICE_TOKEN);
```

### App not showing data?

```javascript
// 1. Check WebSocket
websocket.onopen = () => console.log("✅ Connected");

// 2. Check endpoint
fetch(`${API_URL}/telemetry/latest?device_token=${token}`)
  .then((r) => r.json())
  .then((d) => console.log(d));
```

### 429 Too Many Requests?

```cpp
// Increase intervals
const unsigned long POLL_INTERVAL = 5000;      // 5s
const unsigned long TELEMETRY_INTERVAL = 15000; // 15s
```

---

## 📚 Documentation Files

| File                             | Use When                         |
| -------------------------------- | -------------------------------- |
| **ESP32_BIDIRECTIONAL_GUIDE.md** | Understanding bidirectional flow |
| **ESP32_TELEMETRY_API_GUIDE.md** | API endpoint details             |
| **esp32_production_ready.ino**   | Production firmware template     |
| **ESP32_COMPLETE_SUMMARY.md**    | Complete overview                |

---

## ✅ Testing Checklist

- [ ] ESP32 connects to WiFi
- [ ] ESP32 sends telemetry (check Serial: "✅ Telemetry sent")
- [ ] App shows sensor data
- [ ] App LED button works
- [ ] ESP32 LED changes (within 3s)
- [ ] Works continuously for 1 hour
- [ ] Handles WiFi reconnection
- [ ] Handles backend restart

---

## 🎯 Key Points

1. **Parallel Operation**: ESP32 polls AND sends simultaneously
2. **Bidirectional**: App ↔ ESP32 both directions work
3. **Real-time**: WebSocket for instant app updates
4. **Reliable**: HTTP polling ensures ESP32 gets commands
5. **Production Ready**: Error handling, retries, monitoring

---

## 🚨 Common Mistakes

❌ **Don't**: Hardcode production tokens in code
✅ **Do**: Use Preferences for secure storage

❌ **Don't**: Poll too fast (< 1s)
✅ **Do**: Use 3-5s intervals

❌ **Don't**: Ignore HTTP errors
✅ **Do**: Implement retry logic

❌ **Don't**: Use HTTP in production
✅ **Do**: Use HTTPS with certificates

---

## 📞 Quick Help

**Serial Commands** (esp32_production_ready.ino):

- `provision` - Set device credentials
- `status` - Show current status
- `reset` - Reset configuration
- `help` - Show commands

**Backend Health Check**:

```bash
curl http://192.168.1.103:8000/health
```

**Test Telemetry**:

```bash
curl -X POST http://192.168.1.103:8000/devices/{id}/telemetry \
  -H "Content-Type: application/json" \
  -d '{"device_token":"xxx","data":{"test":1}}'
```

---

## 🎉 You're Ready!

Your platform supports **full bidirectional communication**:

- ✅ App controls ESP32 devices
- ✅ ESP32 sends sensor data
- ✅ Both work in parallel
- ✅ Production ready

**Start building!** 🚀
