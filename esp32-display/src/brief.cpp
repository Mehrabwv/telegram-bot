#include "brief.h"

#include <ArduinoJson.h>

static void readHour(JsonObject hour, Hour& h) {
  h.time = hour["time"] | "";
  h.temp = hour["temp"] | 0;
  h.rain = hour["rain"] | 0;
  h.icon = hour["icon"] | "";
  h.day = hour["day"] | true;
}

bool parseBrief(const String& json, Brief& out, String& error) {
  JsonDocument doc;
  DeserializationError problem = deserializeJson(doc, json);
  if (problem) {
    error = String("bad JSON: ") + problem.c_str();
    return false;
  }
  if ((doc["v"] | 0) != 1) {
    error = "unknown format version";
    return false;
  }

  Brief b;
  b.id = doc["id"] | "";
  b.title = doc["title"] | "Weather";
  b.made = doc["made"] | "";
  b.forDay = doc["for"] | "today";
  b.confidence = doc["confidence"] | "";
  b.utcOffset = doc["utc_offset"] | (long)DEFAULT_UTC_OFFSET;
  b.sent = doc["sent"] | 0u;

  JsonObject now = doc["now"];
  if (now.isNull()) {
    error = "no 'now' section";
    return false;
  }
  b.nowTemp = now["temp"] | 0;
  b.nowFeels = now["feels"] | b.nowTemp;
  b.nowText = now["text"] | "";
  b.nowIcon = now["icon"] | "";
  b.nowDay = now["day"] | true;

  b.hasHigh = !doc["high"].isNull();
  b.high = doc["high"] | 0;
  b.hasLow = !doc["low"].isNull();
  b.low = doc["low"] | 0;

  for (JsonObject item : doc["items"].as<JsonArray>()) {
    if (b.itemCount >= 3) break;
    Item& it = b.items[b.itemCount++];
    it.name = item["name"] | "";
    it.need = item["need"] | false;
    it.answer = item["answer"] | (it.need ? "YES" : "NO");
    JsonArray note = item["note"];
    it.note1 = note[0] | "";
    it.note2 = note[1] | "";
  }

  for (JsonObject hour : doc["hours"].as<JsonArray>()) {
    if (b.hourCount >= 6) break;
    readHour(hour, b.hours[b.hourCount++]);
  }

  JsonObject later = doc["later"];
  if (!later.isNull()) {
    b.laterTitle = later["title"] | "Later";
    b.laterNote = later["note"] | "";
    for (JsonObject hour : later["hours"].as<JsonArray>()) {
      if (b.laterCount >= 12) break;
      readHour(hour, b.later[b.laterCount++]);
    }
  }

  b.ok = true;
  out = b;
  return true;
}
