/*
 * ESP32 LED Control with Virtual Pins
 * Controls LEDs based on virtual pin IDs (v0, v1, v2, etc.) from ThingsNXT Platform
 * 
 * Features:
 * - WiFi connection
 * - HTTP polling for LED state updates
 * - Virtual pin to GPIO mapping
 * - Telemetry reporting
 * 
 * Configuration:
 * 1. Update WIFI_SSID and WIFI_PASSWORD
 * 2. Update API_BASE_URL to your backend URL
 * 3. Update DEVICE_TOKEN from your device in the platform
 * 4. Configure VIRTUAL_PIN_MAPPING to map virtual pins to GPIO pins
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ============================================
// 🔧 CONFIGURATION
// ============================================
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* API_BASE_URL = "http://your-backend-url:8000";  // Update with your backend URL
const char* DEVICE_TOKEN = "YOUR_DEVICE_TOKEN_HERE";  // Get this from your device in the platform

// Virtual Pin to GPIO Mapping
// Format: {virtual_pin_index, GPIO_pin_number}
// Example: v0 -> GPIO 2, v1 -> GPIO 4, v2 -> GPIO 5
struct PinMapping {
  int virtualIndex;  // 0 for v0, 1 for v1, etc.
  int gpioPin;
};

// Configure your virtual pin mappings here
// Add more entries as needed for v0, v1, v2, etc.
PinMapping VIRTUAL_PIN_MAPPING[] = {
  {0, 2},   // v0 -> GPIO 2
  {1, 4},   // v1 -> GPIO 4
  {2, 5},   // v2 -> GPIO 5
  {3, 18},  // v3 -> GPIO 18
  {4, 19},  // v4 -> GPIO 19
  // Add more mappings as needed
};

const int MAX_VIRTUAL_PINS = sizeof(VIRTUAL_PIN_MAPPING) / sizeof(VIRTUAL_PIN_MAPPING[0]);

// Polling interval (milliseconds)
const unsigned long POLL_INTERVAL = 3000;  // Poll every 3 seconds
const unsigned long TELEMETRY_INTERVAL = 10000;  // Send telemetry every 10 seconds

// ============================================
// 📊 STATE MANAGEMENT
// ============================================
struct LEDState {
  int virtualIndex;
  int currentState;  // 0 = OFF, 1 = ON
  unsigned long lastUpdate;
};

LEDState ledStates[MAX_VIRTUAL_PINS];
unsigned long lastPollTime = 0;
unsigned long lastTelemetryTime = 0;

// ============================================
// 🔌 SETUP
// ============================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n\n🚀 ESP32 LED Control Starting...");
  Serial.println("=====================================");
  
  // Initialize LED states
  for (int i = 0; i < MAX_VIRTUAL_PINS; i++) {
    ledStates[i].virtualIndex = VIRTUAL_PIN_MAPPING[i].virtualIndex;
    ledStates[i].currentState = 0;
    ledStates[i].lastUpdate = 0;
    
    // Configure GPIO pins as outputs
    int gpioPin = VIRTUAL_PIN_MAPPING[i].gpioPin;
    pinMode(gpioPin, OUTPUT);
    digitalWrite(gpioPin, LOW);  // Start with all LEDs OFF
    Serial.printf("✅ Configured v%d -> GPIO %d\n", ledStates[i].virtualIndex, gpioPin);
  }
  
  // Connect to WiFi
  connectWiFi();
  
  Serial.println("\n✅ Setup complete!");
  Serial.println("=====================================\n");
}

// ============================================
// 🔄 MAIN LOOP
// ============================================
void loop() {
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ WiFi disconnected. Reconnecting...");
    connectWiFi();
    return;
  }
  
  unsigned long currentTime = millis();
  
  // Poll for LED state updates
  if (currentTime - lastPollTime >= POLL_INTERVAL) {
    pollLEDStates();
    lastPollTime = currentTime;
  }
  
  // Send telemetry updates
  if (currentTime - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    sendTelemetry();
    lastTelemetryTime = currentTime;
  }
  
  delay(100);  // Small delay to prevent watchdog issues
}

// ============================================
// 📡 WIFI CONNECTION
// ============================================
void connectWiFi() {
  Serial.print("📶 Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi connected!");
    Serial.print("📡 IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n❌ WiFi connection failed!");
    Serial.println("Please check your credentials and try again.");
  }
}

// ============================================
// 🔍 POLL LED STATES
// ============================================
void pollLEDStates() {
  HTTPClient http;
  String url = String(API_BASE_URL) + "/telemetry/latest?device_token=" + String(DEVICE_TOKEN);
  
  http.begin(url);
  http.setTimeout(5000);
  
  int httpCode = http.GET();
  
  if (httpCode == HTTP_CODE_OK) {
    String payload = http.getString();
    Serial.println("📥 Received telemetry data:");
    Serial.println(payload);
    
    // Parse JSON response
    DynamicJsonDocument doc(2048);
    DeserializationError error = deserializeJson(doc, payload);
    
    if (!error) {
      JsonObject data = doc["data"];
      if (data.isNull()) {
        Serial.println("⚠️ No data field in response");
        http.end();
        return;
      }
      
      // Update LED states based on virtual pins
      // The backend stores LED states in telemetry_json with keys like "v0", "v1", "v2", etc.
      // OR as a single "led" value (if only one LED widget exists)
      
      // Check for individual virtual pin states (v0, v1, v2, etc.)
      bool updated = false;
      for (int i = 0; i < MAX_VIRTUAL_PINS; i++) {
        String virtualPinKey = "v" + String(ledStates[i].virtualIndex);
        
        if (data.containsKey(virtualPinKey)) {
          int newState = data[virtualPinKey].as<int>();
          if (newState != ledStates[i].currentState) {
            ledStates[i].currentState = newState;
            updateLED(i, newState);
            updated = true;
            Serial.printf("🔄 Updated v%d: %s\n", 
              ledStates[i].virtualIndex, 
              newState ? "ON" : "OFF");
          }
        }
      }
      
      // Fallback: Check for single "led" key (for backward compatibility)
      if (!updated && data.containsKey("led")) {
        int ledState = data["led"].as<int>();
        // Apply to first virtual pin (v0) if no individual pins found
        if (MAX_VIRTUAL_PINS > 0 && ledState != ledStates[0].currentState) {
          ledStates[0].currentState = ledState;
          updateLED(0, ledState);
          Serial.printf("🔄 Updated v0 (led): %s\n", ledState ? "ON" : "OFF");
        }
      }
      
    } else {
      Serial.print("❌ JSON parsing error: ");
      Serial.println(error.c_str());
    }
  } else {
    Serial.printf("❌ HTTP GET failed, code: %d\n", httpCode);
    if (httpCode < 0) {
      Serial.println("   Check your API_BASE_URL and network connection");
    }
  }
  
  http.end();
}

// ============================================
// 💡 UPDATE LED
// ============================================
void updateLED(int index, int state) {
  if (index >= 0 && index < MAX_VIRTUAL_PINS) {
    int gpioPin = VIRTUAL_PIN_MAPPING[index].gpioPin;
    digitalWrite(gpioPin, state ? HIGH : LOW);
    ledStates[index].currentState = state;
    ledStates[index].lastUpdate = millis();
    
    Serial.printf("💡 v%d (GPIO %d): %s\n", 
      ledStates[index].virtualIndex, 
      gpioPin, 
      state ? "ON" : "OFF");
  }
}

// ============================================
// 📤 SEND TELEMETRY
// ============================================
void sendTelemetry() {
  HTTPClient http;
  String url = String(API_BASE_URL) + "/telemetry";
  
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);
  
  // Build JSON payload with all virtual pin states
  DynamicJsonDocument doc(2048);
  doc["device_token"] = DEVICE_TOKEN;
  JsonObject data = doc.createNestedObject("data");
  
  // Add all virtual pin states
  for (int i = 0; i < MAX_VIRTUAL_PINS; i++) {
    String virtualPinKey = "v" + String(ledStates[i].virtualIndex);
    data[virtualPinKey] = ledStates[i].currentState;
  }
  
  // Also include a general "led" field for backward compatibility
  if (MAX_VIRTUAL_PINS > 0) {
    data["led"] = ledStates[0].currentState;
  }
  
  // Add timestamp
  data["timestamp"] = millis();
  
  String payload;
  serializeJson(doc, payload);
  
  Serial.println("📤 Sending telemetry:");
  Serial.println(payload);
  
  int httpCode = http.POST(payload);
  
  if (httpCode == HTTP_CODE_OK || httpCode == 200) {
    String response = http.getString();
    Serial.println("✅ Telemetry sent successfully");
    Serial.println("Response: " + response);
  } else {
    Serial.printf("❌ Telemetry send failed, code: %d\n", httpCode);
    String response = http.getString();
    Serial.println("Response: " + response);
  }
  
  http.end();
}

// ============================================
// 🛠️ HELPER FUNCTIONS
// ============================================
void printStatus() {
  Serial.println("\n📊 Current LED Status:");
  Serial.println("====================");
  for (int i = 0; i < MAX_VIRTUAL_PINS; i++) {
    Serial.printf("v%d (GPIO %d): %s\n", 
      ledStates[i].virtualIndex,
      VIRTUAL_PIN_MAPPING[i].gpioPin,
      ledStates[i].currentState ? "ON" : "OFF");
  }
  Serial.println("====================\n");
}

