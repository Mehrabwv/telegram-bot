// Weather display for the Telegram bot (Seeed XIAO ESP32-S3 + 2" ST7789 screen).
//
// The bot, running on your computer, sends the weather brief here over Wi-Fi
// every few minutes:  POST http://weather-display.local/brief  (JSON)
//
// Other addresses (open them in a browser on the same Wi-Fi):
//   http://weather-display.local/                 status
//   http://weather-display.local/brief            the brief on the screen
//   http://weather-display.local/screenshot.bmp   a picture of the screen
//
//   http://weather-display.local/show-status      the status screen, for 10 seconds
//
// First start: the screen asks you to join the "Weather-Display-Setup" Wi-Fi
// with your phone and pick your home Wi-Fi. To do that again (e.g. new Wi-Fi
// password): hold both buttons on top for 3 seconds, or the XIAO's B button.
// A short press on either top button shows the status screen (Wi-Fi signal etc.).

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <sys/time.h>
#include <time.h>

#include "brief.h"
#include "draw.h"
#include "screen_setup.h"
#include "settings.h"

static Screen screen;
static LGFX_Sprite canvas(&screen);  // drawn in memory first, then shown at once (no flicker)
static bool useCanvas = false;
static WebServer server(80);
static WiFiManager wifiManager;
static Preferences storage;

static Brief brief;         // what is on the screen
static String briefJson;    // ...as the bot sent it
static uint32_t lastBotMs = 0;  // when the bot last sent something (0 = not since start)
static uint32_t connectedMs = 0;
static long utcOffset = DEFAULT_UTC_OFFSET;
static String shownClock;
static bool shownStale = false;
static uint32_t statusUntil = 0;  // the status screen is up until then (millis)

static lgfx::LovyanGFX& gfx() {
  return useCanvas ? static_cast<lgfx::LovyanGFX&>(canvas) : static_cast<lgfx::LovyanGFX&>(screen);
}

static void present() {
  if (useCanvas) canvas.pushSprite(0, 0);
}

// ---------- clock ----------

static bool clockIsSet() { return time(nullptr) > 1700000000; }

static String clockText() {
  if (!clockIsSet()) return "";
  time_t now = time(nullptr);
  struct tm t;
  localtime_r(&now, &t);
  int hour = t.tm_hour % 12 == 0 ? 12 : t.tm_hour % 12;
  char text[12];
  snprintf(text, sizeof text, "%d:%02d %s", hour, t.tm_min, t.tm_hour < 12 ? "AM" : "PM");
  return text;
}

static void startClock() { configTime(utcOffset, 0, "pool.ntp.org", "time.google.com"); }

// ---------- screens ----------

static bool isStale() {
  if (!brief.ok) return false;
  uint32_t since = lastBotMs ? lastBotMs : connectedMs;
  return millis() - since > STALE_MINUTES * 60000UL;
}

static void showWaiting() {
  String lines[] = {
      "*Connected to Wi-Fi",
      WiFi.SSID(),
      "Address: " DEVICE_NAME ".local",
      "IP: " + WiFi.localIP().toString(),
      "",
      "*Waiting for the bot...",
  };
  drawMessage(gfx(), "Weather display", lines, 6, false);
}

static bool statusShowing() { return statusUntil && (int32_t)(statusUntil - millis()) > 0; }

static String ago(uint32_t seconds) {
  if (seconds < 60) return "under a minute";
  uint32_t minutes = seconds / 60;
  if (minutes < 60) return String(minutes) + " min";
  return String(minutes / 60) + " h " + String(minutes % 60) + " min";
}

// A button press shows this: is the Wi-Fi OK in the case, and is the bot talking to us?
static void showStatus() {
  int rssi = WiFi.RSSI();
  const char* quality = rssi >= -60 ? "great" : rssi >= -70 ? "good" : rssi >= -80 ? "weak" : "poor";
  String lines[] = {
      "*Wi-Fi: " + WiFi.SSID(),
      "Signal: " + String(rssi) + " dBm (" + quality + ")",
      "IP: " + WiFi.localIP().toString(),
      "Bot: " + (lastBotMs ? "updated " + ago((millis() - lastBotMs) / 1000) + " ago" : String("nothing yet")),
      "Running for " + ago(millis() / 1000),
      "",
      "Hold both buttons 3 s: Wi-Fi setup",
  };
  drawMessage(gfx(), "Status", lines, 7, false);
}

static void render() {
  if (statusShowing()) {
    showStatus();
    present();
    return;
  }
  shownClock = clockText();
  shownStale = isStale();
  if (brief.ok) {
    drawBrief(gfx(), brief, shownClock, shownStale);
  } else {
    showWaiting();
  }
  present();
}

static void showSetupPortal(WiFiManager*) {
  String lines[] = {
      "*Set up Wi-Fi with your phone:",
      "1. Join this Wi-Fi network:",
      "*     " SETUP_WIFI_NAME,
      "2. A setup page opens (if not,",
      "    go to 192.168.4.1 in a browser)",
      "3. Pick your Wi-Fi, type its password",
  };
  drawMessage(gfx(), "Wi-Fi setup", lines, 6, true);
  present();
  Serial.println("Wi-Fi setup: join '" SETUP_WIFI_NAME "' with your phone");
}

// ---------- web server (the bot talks to these) ----------

static void sendJson(int code, JsonDocument& doc) {
  String body;
  serializeJson(doc, body);
  server.send(code, "application/json", body);
}

static void handleStatus() {
  JsonDocument doc;
  doc["device"] = DEVICE_NAME;
  doc["version"] = 1;
  doc["ip"] = WiFi.localIP().toString();
  doc["wifi"] = WiFi.SSID();
  doc["wifi_signal_dbm"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["brief"] = brief.ok ? brief.id : "";
  if (lastBotMs) doc["seconds_since_bot"] = (millis() - lastBotMs) / 1000;
  else doc["seconds_since_bot"] = nullptr;
  doc["clock"] = clockText();
  doc["drawing"] = useCanvas ? "in memory" : "direct";
  doc["free_memory"] = ESP.getFreeHeap();
  sendJson(200, doc);
}

static void handlePostBrief() {
  String body = server.arg("plain");
  Brief incoming;
  String error;
  if (!parseBrief(body, incoming, error)) {
    Serial.printf("Bad brief from %s: %s\n", server.client().remoteIP().toString().c_str(),
                  error.c_str());
    JsonDocument doc;
    doc["ok"] = false;
    doc["error"] = error;
    sendJson(400, doc);
    return;
  }

  bool changed = incoming.id != brief.id || !brief.ok;
  bool wasStale = isStale();
  brief = incoming;
  briefJson = body;
  lastBotMs = millis();
  if (changed) {
    Serial.printf("New brief: %s\n", brief.id.c_str());
    if (body.length() < 3900) storage.putString("brief", body);  // shown again after a restart
  }
  if (brief.utcOffset != utcOffset) {
    utcOffset = brief.utcOffset;
    startClock();
  }
  if (!clockIsSet() && brief.sent > 1700000000) {  // no internet time yet: use the bot's
    struct timeval now = {(time_t)brief.sent, 0};
    settimeofday(&now, nullptr);
  }

  JsonDocument doc;
  doc["ok"] = true;
  doc["changed"] = changed;
  sendJson(200, doc);
  if (changed || wasStale) render();
}

static void handleGetBrief() {
  if (briefJson.length()) {
    server.send(200, "application/json", briefJson);
  } else {
    server.send(404, "application/json", "{\"error\":\"no brief yet\"}");
  }
}

// A picture of the screen (24-bit BMP), for checking the layout from the computer
static void handleScreenshot() {
  if (!useCanvas) {
    server.send(503, "text/plain", "Screenshots need the in-memory drawing (PSRAM).");
    return;
  }
  const int w = canvas.width(), h = canvas.height();
  const uint32_t rowBytes = w * 3;  // 960: already a multiple of 4
  const uint32_t size = 54 + rowBytes * h;
  uint8_t header[54] = {'B', 'M'};
  auto put32 = [&](int at, uint32_t v) {
    for (int i = 0; i < 4; i++) header[at + i] = (v >> (8 * i)) & 0xFF;
  };
  put32(2, size);
  put32(10, 54);  // where the pixels start
  put32(14, 40);  // info header size
  put32(18, w);
  put32(22, h);
  header[26] = 1;   // planes
  header[28] = 24;  // bits per pixel
  put32(34, rowBytes * h);

  server.setContentLength(size);
  server.send(200, "image/bmp", "");
  WiFiClient client = server.client();
  client.write(header, sizeof header);
  uint8_t* row = (uint8_t*)malloc(rowBytes);
  if (!row) return;
  for (int y = h - 1; y >= 0; y--) {  // BMP rows go bottom to top
    for (int x = 0; x < w; x++) {
      auto c = canvas.readPixelRGB(x, y);
      row[x * 3] = c.B8();
      row[x * 3 + 1] = c.G8();
      row[x * 3 + 2] = c.R8();
    }
    client.write(row, rowBytes);
  }
  free(row);
}

// ---------- buttons ----------

static void restartIntoWifiSetup() {
  String lines[] = {"*Forgetting this Wi-Fi...", "Restarting into Wi-Fi setup."};
  drawMessage(gfx(), "Wi-Fi setup", lines, 2, false);
  present();
  wifiManager.resetSettings();
  delay(1500);
  ESP.restart();
}

// The XIAO's own B button (a pinhole in the case): hold 3 seconds for Wi-Fi setup
static void checkBootButton() {
  static uint32_t pressedSince = 0;
  if (digitalRead(PIN_BOOT_BUTTON) != LOW) {
    pressedSince = 0;
    return;
  }
  if (!pressedSince) pressedSince = millis();
  if (millis() - pressedSince >= 3000) restartIntoWifiSetup();
}

// The two buttons on top: a short press shows the status screen; both held 3 s = Wi-Fi setup
struct Button {
  uint8_t pin;
  bool down;
  uint32_t changedMs, downSince;
};
static Button buttons[2] = {{PIN_BUTTON_A, false, 0, 0}, {PIN_BUTTON_B, false, 0, 0}};

static void checkButtons() {
  uint32_t now = millis();
  for (Button& b : buttons) {
    bool pressed = digitalRead(b.pin) == LOW;
    if (pressed == b.down || now - b.changedMs < 30) continue;  // no change, or still bouncing
    b.changedMs = now;
    b.down = pressed;
    if (pressed) {
      b.downSince = now;
    } else if (now - b.downSince < 1500) {
      Serial.printf("Button on GPIO %d pressed\n", b.pin);
      statusUntil = now + STATUS_SECONDS * 1000UL;
      render();
    }
  }
  if (buttons[0].down && buttons[1].down && now - buttons[0].downSince >= 3000 &&
      now - buttons[1].downSince >= 3000) {
    restartIntoWifiSetup();
  }
}

// ---------- start ----------

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUTTON_A, INPUT_PULLUP);  // not wired yet = reads "not pressed"
  pinMode(PIN_BUTTON_B, INPUT_PULLUP);

  screen.init();
  screen.setRotation(SCREEN_ROTATION);
  canvas.setColorDepth(16);
  canvas.setPsram(true);
  useCanvas = canvas.createSprite(screen.width(), screen.height()) != nullptr;
  Serial.printf("Screen %dx%d, drawing %s\n", screen.width(), screen.height(),
                useCanvas ? "in memory (PSRAM)" : "directly");

  // The last brief, so the screen isn't empty after a restart
  storage.begin("display", false);
  String saved = storage.getString("brief", "");
  Brief stored;
  String error;
  if (saved.length() && parseBrief(saved, stored, error)) {
    brief = stored;
    briefJson = saved;
    utcOffset = stored.utcOffset;
  }
  startClock();

  String lines[] = {
      "*Connecting to Wi-Fi...",
      "",
      "Color check: the bars below should",
      "read red, green, blue and white.",
  };
  drawMessage(gfx(), "Weather display", lines, 4, true);
  present();

  WiFi.mode(WIFI_STA);
  wifiManager.setHostname(DEVICE_NAME);
  wifiManager.setTitle("Weather display");
  std::vector<const char*> menu = {"wifi", "exit"};
  wifiManager.setMenu(menu);
  wifiManager.setAPCallback(showSetupPortal);
  wifiManager.setConnectTimeout(20);
  wifiManager.setConfigPortalTimeout(300);  // then restart and try the saved Wi-Fi again
  if (!wifiManager.autoConnect(SETUP_WIFI_NAME)) {
    String retry[] = {"*No Wi-Fi yet.", "Restarting to try again..."};
    drawMessage(gfx(), "Weather display", retry, 2, false);
    present();
    delay(3000);
    ESP.restart();
  }
  WiFi.setSleep(false);  // answer the bot right away
  WiFi.setAutoReconnect(true);
  connectedMs = millis();
  Serial.printf("Wi-Fi: %s, IP %s\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());

  if (MDNS.begin(DEVICE_NAME)) MDNS.addService("http", "tcp", 80);
  server.on("/", HTTP_GET, handleStatus);
  server.on("/brief", HTTP_POST, handlePostBrief);
  server.on("/brief", HTTP_GET, handleGetBrief);
  server.on("/screenshot.bmp", HTTP_GET, handleScreenshot);
  server.on("/show-status", HTTP_GET, [] {
    statusUntil = millis() + STATUS_SECONDS * 1000UL;
    render();
    server.send(200, "text/plain", "Showing the status screen for " + String(STATUS_SECONDS) + " seconds.\n");
  });
  server.onNotFound([] { server.send(404, "text/plain", "Not found"); });
  server.begin();
  Serial.println("Ready: http://" DEVICE_NAME ".local/");
  render();
}

void loop() {
  server.handleClient();
  checkBootButton();
  checkButtons();

  static uint32_t lastCheck = 0;
  static bool statusWasShowing = false;
  if (millis() - lastCheck >= 1000) {
    lastCheck = millis();
    bool status = statusShowing();
    if (status || statusWasShowing) {
      render();  // the status screen updates every second; then back to the weather
    } else if (clockText() != shownClock || isStale() != shownStale) {
      render();  // new minute, or the bot went quiet
    }
    statusWasShowing = status;
  }

  // Wi-Fi dropped and isn't coming back by itself: nudge it every 30 seconds
  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 30000) {
    lastReconnect = millis();
    Serial.println("Wi-Fi lost, reconnecting...");
    WiFi.reconnect();
  }
  delay(2);
}
