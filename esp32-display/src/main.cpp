// Weather display for the Telegram bot (Seeed XIAO ESP32-S3 + 2" ST7789 screen).
//
// The bot, running on your computer, sends what to show here over Wi-Fi every two
// minutes. The display never connects to the computer; the bot does all the asking:
//   POST /brief      the weather brief, plus the next 12 hours (the "later" screen)
//   POST /news       the headlines with summaries, in the order the AI ranked them
//   POST /stocks     the watchlist's prices
//   GET  /requests   "send to my phone" presses waiting for the bot (read = cleared)
//
// Other addresses (open them in a browser on the same Wi-Fi):
//   http://weather-display.local/                 status
//   http://weather-display.local/screenshot.bmp   a picture of the screen
//   http://weather-display.local/press?button=a   acts like a press of A (or b);
//                                                 add &hold=1 for a hold
//
// Screens, in the order button A steps through them:
//   weather -> headlines (a few pages) -> stocks -> later (next 12 hours / tomorrow)
// Buttons (see settings.h): presses move around, holds do something.
// A press = next page, B press = highlight the next headline or stock,
// A hold = open the highlighted one, B hold = send it to your phone.
// The headlines also come up by themselves every few minutes.
//
// First start: the screen asks you to join the "Weather-Display-Setup" Wi-Fi
// with your phone and pick your home Wi-Fi. To do that again (e.g. new Wi-Fi
// password): hold both buttons on top for 3 seconds, or the XIAO's B button.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <sys/time.h>
#include <time.h>

#include <memory>

#include "brief.h"
#include "draw.h"
#include "feeds.h"
#include "screen_setup.h"
#include "settings.h"

static Screen screen;
static LGFX_Sprite canvas(&screen);  // drawn in memory first, then shown at once (no flicker)
static bool useCanvas = false;
static WebServer server(80);
static WiFiManager wifiManager;
static Preferences storage;

// What the bot sent (also kept in flash, so it's back after a restart)
static Brief brief;
static String briefJson;
static Headlines headlines;
static String headlinesJson;
static Stocks stocks;
static String stocksJson;

static uint32_t lastBotMs = 0;  // when the bot last sent something (0 = not since start)
static uint32_t connectedMs = 0;
static long utcOffset = DEFAULT_UTC_OFFSET;
static String shownClock;
static bool shownStale = false;
static uint32_t statusUntil = 0;  // the status screen is up until then (millis)

enum class View { Weather, News, Stocks, Later };
static View view = View::Weather;
static int newsPage = 0;
static int selected = -1;          // highlighted headline (index into all of them) or stock
static bool detailOpen = false;    // showing the highlighted one in full
static bool newsByItself = false;  // the headlines came up on their own (they turn pages)
static uint32_t viewSinceMs = 0;   // when the screen last changed or a button was pressed
static uint32_t lastPressMs = 0;

static String toastText;  // a short message over the screen ("Sent to your phone")
static uint32_t toastUntil = 0;

#define MAX_REQUESTS 5
static String requests[MAX_REQUESTS];  // "send to my phone", waiting for the bot
static int requestCount = 0;

static lgfx::LovyanGFX& gfx() {
  return useCanvas ? static_cast<lgfx::LovyanGFX&>(canvas) : static_cast<lgfx::LovyanGFX&>(screen);
}

static void present() {
  if (useCanvas) canvas.pushSprite(0, 0);
}

// ---------- saved in flash ----------

static void saveJson(const char* key, const String& json) {
  storage.putBytes(key, json.c_str(), json.length());
}

static String loadJson(const char* key) {
  if (!storage.isKey(key)) return "";
  size_t length = storage.getBytesLength(key);
  if (!length || length > 16000) return "";
  std::unique_ptr<char[]> text(new char[length + 1]);
  storage.getBytes(key, text.get(), length);
  text[length] = 0;
  return String(text.get());
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

static bool statusShowing() { return statusUntil && (int32_t)(statusUntil - millis()) > 0; }
static bool toastShowing() { return toastUntil && (int32_t)(toastUntil - millis()) > 0; }
static bool hasLater() { return brief.ok && brief.laterCount > 0; }
static bool showsList() {
  return (view == View::News && headlines.ok) || (view == View::Stocks && stocks.ok);
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

static String ago(uint32_t seconds) {
  if (seconds < 60) return "under a minute";
  uint32_t minutes = seconds / 60;
  if (minutes < 60) return String(minutes) + " min";
  return String(minutes / 60) + " h " + String(minutes % 60) + " min";
}

// Hold B on the weather: is the Wi-Fi OK in the case, and is the bot talking to us?
static void showStatus() {
  int rssi = WiFi.RSSI();
  const char* quality = rssi >= -60 ? "great" : rssi >= -70 ? "good" : rssi >= -80 ? "weak" : "poor";
  String lines[] = {
      "*Wi-Fi: " + WiFi.SSID(),
      "Signal: " + String(rssi) + " dBm (" + quality + ")",
      "IP: " + WiFi.localIP().toString(),
      "Bot: " + (lastBotMs ? "updated " + ago((millis() - lastBotMs) / 1000) + " ago" : String("nothing yet")),
      "Running for " + ago(millis() / 1000),
      "A: next page   (hold: open)",
      "B: next item   (hold: send to phone)",
      "Hold A + B 3 s: Wi-Fi setup",
  };
  drawMessage(gfx(), "Status", lines, 8, false);
}

static int pageCount() { return headlinePages(gfx(), headlines); }

// The highlighted headline or stock, or the first one on the screen if none is
static int currentItem() {
  if (view == View::Stocks) return selected >= 0 && selected < stocks.count ? selected : 0;
  int first, last;
  headlinePage(gfx(), headlines, newsPage, first, last);
  return selected >= first && selected < last ? selected : first;
}

static void render() {
  lgfx::LovyanGFX& g = gfx();
  if (statusShowing()) {
    showStatus();
  } else {
    shownClock = clockText();
    shownStale = isStale();
    if (view == View::News) {
      if (detailOpen && headlines.ok) drawHeadlineDetail(g, headlines, currentItem(), shownClock, shownStale);
      else drawHeadlines(g, headlines, newsPage, selected, shownClock, shownStale);
    } else if (view == View::Stocks) {
      if (detailOpen && stocks.ok) drawStockDetail(g, stocks, currentItem(), shownClock, shownStale);
      else drawStocks(g, stocks, selected, shownClock, shownStale);
    } else if (view == View::Later && hasLater()) {
      drawLater(g, brief, shownClock, shownStale);
    } else if (brief.ok) {
      drawBrief(g, brief, shownClock, shownStale);
    } else {
      showWaiting();
    }
  }
  if (toastShowing()) drawToast(g, toastText);
  present();
}

static void toast(const String& text, uint32_t ms) {
  toastText = text;
  toastUntil = millis() + ms;
  render();
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

// ---------- switching screens ----------

static void showView(View next, int page = 0, bool byItself = false) {
  view = next;
  newsPage = page;
  newsByItself = byItself;
  selected = -1;
  detailOpen = false;
  viewSinceMs = millis();
  render();
}

// Weather -> headlines page 1, 2 ... -> stocks -> later -> weather
static void nextScreen() {
  if (view == View::News && newsPage + 1 < pageCount()) {
    showView(View::News, newsPage + 1);
    return;
  }
  const View order[] = {View::Weather, View::News, View::Stocks, View::Later};
  int at = 0;
  for (int i = 0; i < 4; i++) {
    if (order[i] == view) at = i;
  }
  for (int step = 1; step <= 4; step++) {
    View next = order[(at + step) % 4];
    if (next == View::Stocks && !stocks.ok) continue;
    if (next == View::Later && !hasLater()) continue;
    showView(next);
    return;
  }
}

// Move the highlight to the next headline on this page (or the next stock)
static void selectNext() {
  if (view == View::Stocks) {
    selected = (selected + 1) % max(1, stocks.count);
    return;
  }
  int first, last;
  headlinePage(gfx(), headlines, newsPage, first, last);
  if (last <= first) return;
  selected = (selected < first || selected >= last - 1) ? first : selected + 1;
}

static void queueRequest(const char* type, const String& id, const String& what) {
  if (!id.length()) {
    toast("Can't send this one", 3000);
  } else if (!lastBotMs || isStale()) {
    toast("The bot isn't connected", 3000);
  } else if (requestCount >= MAX_REQUESTS) {
    toast("Still sending the others...", 3000);
  } else {
    JsonDocument doc;
    doc["type"] = type;
    doc["id"] = id;
    String request;
    serializeJson(doc, request);
    requests[requestCount++] = request;
    toast("Sending " + what + " to your phone...", 15000);  // until the bot picks it up
  }
}

static void touch() {
  lastPressMs = millis();
  viewSinceMs = millis();
  newsByItself = false;
}

// A press: next page / screen (or close the details)
static void pressA() {
  touch();
  statusUntil = 0;
  if (detailOpen) {
    detailOpen = false;
    render();
    return;
  }
  nextScreen();
}

// A hold: open the highlighted headline or stock (more details); hold again to close
static void holdA() {
  touch();
  if (statusShowing()) {
    statusUntil = 0;
    render();
  } else if (detailOpen) {
    detailOpen = false;
    render();
  } else if (showsList()) {
    selected = currentItem();
    detailOpen = true;
    render();
  }
}

// B press: highlight the next headline or stock (with the details open: the next
// one's details). On the weather: the later forecast, and back.
static void pressB() {
  touch();
  if (statusShowing()) {
    statusUntil = 0;
    render();
  } else if (showsList()) {
    selectNext();
    render();
  } else if (view == View::Weather && hasLater()) {
    showView(View::Later);
  } else if (view == View::Later) {
    showView(View::Weather);
  }
}

// B hold: send the highlighted headline or stock to your phone; on the weather, the status
static void holdB() {
  touch();
  if (view == View::News && headlines.ok) {
    selected = currentItem();
    queueRequest("headline", headlines.items[selected].id, "the story");
  } else if (view == View::Stocks && stocks.ok) {
    selected = currentItem();
    queueRequest("stock", stocks.items[selected].ticker, stocks.items[selected].ticker);
  } else {
    statusUntil = millis() + STATUS_SECONDS * 1000UL;
    render();
  }
}

// Once a second: turn the headline pages, go back to the weather, or bring the
// headlines up by themselves
static void timeScreens() {
  if (statusShowing()) return;
  uint32_t now = millis();
  if (view == View::News && newsByItself) {
    if (now - viewSinceMs < NEWS_PAGE_SECONDS * 1000UL) return;
    if (newsPage + 1 < pageCount()) showView(View::News, newsPage + 1, true);
    else showView(View::Weather);
  } else if (view != View::Weather) {
    if (now - viewSinceMs >= IDLE_SECONDS * 1000UL) showView(View::Weather);
  } else if (NEWS_EVERY_MINUTES > 0 && headlines.ok && brief.ok &&
             now - viewSinceMs >= NEWS_EVERY_MINUTES * 60000UL) {
    showView(View::News, 0, true);
  }
}

static String describeView() {
  if (statusShowing()) return "status";
  if (view == View::Later) return "later forecast";
  if (view == View::Weather) return "weather";
  String name = view == View::News ? "headlines page " + String(newsPage + 1) : String("stocks");
  if (detailOpen) return name + ", details of item " + String(currentItem() + 1);
  if (selected >= 0) return name + ", item " + String(selected + 1) + " highlighted";
  return name;
}

// ---------- web server (the bot talks to these) ----------

static void sendJson(int code, JsonDocument& doc) {
  String body;
  serializeJson(doc, body);
  server.send(code, "application/json", body);
}

static void sendError(const String& error) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = error;
  sendJson(400, doc);
}

static void sendOk(bool changed) {
  JsonDocument doc;
  doc["ok"] = true;
  doc["changed"] = changed;
  sendJson(200, doc);
}

static void handleStatus() {
  JsonDocument doc;
  doc["device"] = DEVICE_NAME;
  doc["version"] = 2;
  doc["ip"] = WiFi.localIP().toString();
  doc["wifi"] = WiFi.SSID();
  doc["wifi_signal_dbm"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["brief"] = brief.ok ? brief.id : "";
  doc["headlines"] = headlines.ok ? headlines.id : "";
  doc["stocks"] = stocks.ok ? stocks.id : "";
  doc["screen"] = describeView();
  doc["requests_waiting"] = requestCount;
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
    sendError(error);
    return;
  }

  bool changed = incoming.id != brief.id || !brief.ok;
  bool wasStale = isStale();
  brief = incoming;
  briefJson = body;
  lastBotMs = millis();
  if (changed) {
    Serial.printf("New brief: %s\n", brief.id.c_str());
    saveJson("brief2", body);  // shown again after a restart
  }
  if (brief.utcOffset != utcOffset) {
    utcOffset = brief.utcOffset;
    startClock();
  }
  if (!clockIsSet() && brief.sent > 1700000000) {  // no internet time yet: use the bot's
    struct timeval now = {(time_t)brief.sent, 0};
    settimeofday(&now, nullptr);
  }
  sendOk(changed);
  if (changed || wasStale) render();
}

static void handlePostNews() {
  String body = server.arg("plain");
  Headlines incoming;
  String error;
  if (!parseHeadlines(body, incoming, error)) {
    sendError(error);
    return;
  }
  bool changed = incoming.id != headlines.id || !headlines.ok;
  headlines = incoming;
  headlinesJson = body;
  if (changed) {
    Serial.printf("New headlines: %s (%d)\n", headlines.id.c_str(), headlines.count);
    saveJson("news2", body);
  }
  sendOk(changed);
  if (changed && view == View::News) {
    newsPage = min(newsPage, pageCount() - 1);
    if (selected >= headlines.count) {
      selected = -1;
      detailOpen = false;
    }
    render();
  }
}

static void handlePostStocks() {
  String body = server.arg("plain");
  Stocks incoming;
  String error;
  if (!parseStocks(body, incoming, error)) {
    sendError(error);
    return;
  }
  bool changed = incoming.id != stocks.id || !stocks.ok;
  stocks = incoming;
  stocksJson = body;
  if (changed) saveJson("stocks2", body);
  sendOk(changed);
  if (changed && view == View::Stocks) {
    if (selected >= stocks.count) {
      selected = -1;
      detailOpen = false;
    }
    render();
  }
}

// The bot collects "send to my phone" requests here (every few seconds)
static void handleGetRequests() {
  String body = "[";
  for (int i = 0; i < requestCount; i++) {
    if (i) body += ",";
    body += requests[i];
  }
  body += "]";
  bool hadAny = requestCount > 0;
  requestCount = 0;
  server.send(200, "application/json", body);
  if (hadAny) toast("Sent to your phone", 3000);
}

static void sendSaved(const String& json, const char* what) {
  if (json.length()) server.send(200, "application/json", json);
  else server.send(404, "application/json", String("{\"error\":\"no ") + what + " yet\"}");
}

// Acts like a button press, for trying the screens from a browser
static void handlePress() {
  String which = server.arg("button");
  which.toLowerCase();
  bool hold = server.arg("hold") == "1";
  if (which == "a") {
    hold ? holdA() : pressA();
  } else if (which == "b") {
    hold ? holdB() : pressB();
  } else {
    server.send(400, "text/plain", "Use /press?button=a or /press?button=b (add &hold=1 to hold)\n");
    return;
  }
  server.send(200, "text/plain", "Now showing: " + describeView() + "\n");
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

struct Button {
  uint8_t pin;
  bool down = false;
  uint32_t changedMs = 0, downSince = 0;
  bool used = false;  // this press already did something (a hold, or both buttons)
  explicit Button(uint8_t p) : pin(p) {}
};
static Button buttonA(PIN_BUTTON_A), buttonB(PIN_BUTTON_B), bootButton(PIN_BOOT_BUTTON);

// Returns true when the button went down or up (after 30 ms of bounce)
static bool readButton(Button& b, uint32_t now) {
  bool pressed = digitalRead(b.pin) == LOW;
  if (pressed == b.down || now - b.changedMs < 30) return false;
  b.changedMs = now;
  b.down = pressed;
  if (pressed) {
    b.downSince = now;
    b.used = false;
  }
  return true;
}

static void checkButtons() {
  uint32_t now = millis();
  bool aChanged = readButton(buttonA, now);
  bool bChanged = readButton(buttonB, now);
  bool bootChanged = readButton(bootButton, now);

  // Both top buttons: no single-button actions; 3 seconds = Wi-Fi setup
  if (buttonA.down && buttonB.down) {
    buttonA.used = buttonB.used = true;
    if (now - buttonA.downSince >= 3000 && now - buttonB.downSince >= 3000) restartIntoWifiSetup();
  }
  // Holds act while still held; presses act when let go
  if (buttonA.down && !buttonA.used && now - buttonA.downSince >= HOLD_MS) {
    buttonA.used = true;
    holdA();
  }
  if (buttonB.down && !buttonB.used && now - buttonB.downSince >= HOLD_MS) {
    buttonB.used = true;
    holdB();
  }
  if (aChanged && !buttonA.down && !buttonA.used) pressA();
  if (bChanged && !buttonB.down && !buttonB.used) pressB();

  // The XIAO's B button: short = A press, longer = A hold, 3 seconds = Wi-Fi setup
  if (bootButton.down && now - bootButton.downSince >= 3000) restartIntoWifiSetup();
  if (bootChanged && !bootButton.down) {
    if (now - bootButton.downSince < HOLD_MS) pressA();
    else holdA();
  }
}

// ---------- start ----------

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUTTON_A, INPUT_PULLUP);  // not wired yet = reads "not pressed"
  pinMode(PIN_BUTTON_B, INPUT_PULLUP);
  pinMode(PIN_SD_CS, OUTPUT);  // keep the SD module off the shared SPI lines
  digitalWrite(PIN_SD_CS, HIGH);

  screen.init();
  screen.setRotation(SCREEN_ROTATION);
  canvas.setColorDepth(16);
  canvas.setPsram(true);
  useCanvas = canvas.createSprite(screen.width(), screen.height()) != nullptr;
  Serial.printf("Screen %dx%d, drawing %s\n", screen.width(), screen.height(),
                useCanvas ? "in memory (PSRAM)" : "directly");

  // What the bot sent last time, so the screens aren't empty after a restart
  storage.begin("display", false);
  for (const char* old : {"brief", "news"}) {  // saved as text by older firmware
    if (storage.isKey(old)) storage.remove(old);
  }
  String error;
  String saved = loadJson("brief2");
  Brief storedBrief;
  if (saved.length() && parseBrief(saved, storedBrief, error)) {
    brief = storedBrief;
    briefJson = saved;
    utcOffset = storedBrief.utcOffset;
  }
  saved = loadJson("news2");
  Headlines storedNews;
  if (saved.length() && parseHeadlines(saved, storedNews, error)) {
    headlines = storedNews;
    headlinesJson = saved;
  }
  saved = loadJson("stocks2");
  Stocks storedStocks;
  if (saved.length() && parseStocks(saved, storedStocks, error)) {
    stocks = storedStocks;
    stocksJson = saved;
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
  server.on("/brief", HTTP_GET, [] { sendSaved(briefJson, "brief"); });
  server.on("/news", HTTP_POST, handlePostNews);
  server.on("/news", HTTP_GET, [] { sendSaved(headlinesJson, "headlines"); });
  server.on("/stocks", HTTP_POST, handlePostStocks);
  server.on("/stocks", HTTP_GET, [] { sendSaved(stocksJson, "stocks"); });
  server.on("/requests", HTTP_GET, handleGetRequests);
  server.on("/press", HTTP_GET, handlePress);
  server.on("/screenshot.bmp", HTTP_GET, handleScreenshot);
  server.on("/show-status", HTTP_GET, [] {
    statusUntil = millis() + STATUS_SECONDS * 1000UL;
    render();
    server.send(200, "text/plain", "Showing the status screen for " + String(STATUS_SECONDS) + " seconds.\n");
  });
  server.onNotFound([] { server.send(404, "text/plain", "Not found"); });
  server.begin();
  Serial.println("Ready: http://" DEVICE_NAME ".local/");
  viewSinceMs = millis();
  render();
}

void loop() {
  server.handleClient();
  checkButtons();

  static uint32_t lastCheck = 0;
  static bool statusWasShowing = false, toastWasShowing = false;
  if (millis() - lastCheck >= 1000) {
    lastCheck = millis();
    timeScreens();
    bool status = statusShowing(), toasting = toastShowing();
    if (status || statusWasShowing || toasting != toastWasShowing) {
      render();  // the status screen updates every second; a message went away
    } else if (clockText() != shownClock || isStale() != shownStale) {
      render();  // new minute, or the bot went quiet
    }
    statusWasShowing = status;
    toastWasShowing = toasting;
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
