#include "headlines.h"

#include <ArduinoJson.h>

bool parseHeadlines(const String& json, Headlines& out, String& error) {
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

  Headlines h;
  h.id = doc["id"] | "";
  h.made = doc["made"] | "";
  for (JsonObject item : doc["items"].as<JsonArray>()) {
    if (h.count >= MAX_HEADLINES) break;
    String title = item["title"] | "";
    if (!title.length()) continue;
    h.items[h.count].title = title;
    h.items[h.count].section = item["section"] | "";
    h.count++;
  }
  if (!h.count) {
    error = "no headlines";
    return false;
  }
  h.ok = true;
  out = h;
  return true;
}
