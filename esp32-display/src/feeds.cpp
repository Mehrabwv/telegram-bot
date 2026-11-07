#include "feeds.h"

#include <ArduinoJson.h>

static bool readJson(const String& json, JsonDocument& doc, String& error) {
  DeserializationError problem = deserializeJson(doc, json);
  if (problem) {
    error = String("bad JSON: ") + problem.c_str();
    return false;
  }
  if ((doc["v"] | 0) != 1) {
    error = "unknown format version";
    return false;
  }
  return true;
}

bool parseHeadlines(const String& json, Headlines& out, String& error) {
  JsonDocument doc;
  if (!readJson(json, doc, error)) return false;

  Headlines h;
  h.id = doc["id"] | "";
  h.made = doc["made"] | "";
  for (JsonObject item : doc["items"].as<JsonArray>()) {
    if (h.count >= MAX_HEADLINES) break;
    String title = item["title"] | "";
    if (!title.length()) continue;
    Headline& it = h.items[h.count++];
    it.id = item["id"] | "";
    it.title = title;
    it.section = item["section"] | "";
    it.summary = item["summary"] | "";
    it.tag = item["tag"] | "";
  }
  if (!h.count) {
    error = "no headlines";
    return false;
  }
  h.ok = true;
  out = h;
  return true;
}

bool parseStocks(const String& json, Stocks& out, String& error) {
  JsonDocument doc;
  if (!readJson(json, doc, error)) return false;

  Stocks s;
  s.id = doc["id"] | "";
  s.made = doc["made"] | "";
  s.market = doc["market"] | "";
  for (JsonObject item : doc["items"].as<JsonArray>()) {
    if (s.count >= MAX_STOCKS) break;
    String ticker = item["ticker"] | "";
    if (!ticker.length()) continue;
    Stock& st = s.items[s.count++];
    st.ticker = ticker;
    st.name = item["name"] | "";
    st.price = item["price"] | "";
    st.changeText = item["change_text"] | "";
    st.change = item["change"] | 0.0f;
    st.prev = item["prev"] | "";
    st.range = item["range"] | "";
    for (JsonVariant title : item["news"].as<JsonArray>()) {
      if (st.newsCount >= 2) break;
      const char* text = title.as<const char*>();
      if (text && *text) st.news[st.newsCount++] = text;
    }
  }
  if (!s.count) {
    error = "no stocks";
    return false;
  }
  s.ok = true;
  out = s;
  return true;
}
