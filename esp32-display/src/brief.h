#pragma once
// The weather brief the bot sends (as JSON), ready to draw.
#include <Arduino.h>

#include "settings.h"

struct Item {  // jacket, cap or umbrella
  String name;        // "Jacket"
  bool need = false;  // bring it?
  String answer;      // "YES" / "NO"
  String note1, note2;  // two short lines, e.g. "Feels 52°" / "at 7 AM"
};

struct Hour {
  String time;  // "9 AM"
  int temp = 0;
  int rain = 0;  // chance of rain, %
  String icon;   // clear, partly, cloudy, fog, drizzle, rain, snow, storm
  bool day = true;
};

struct Brief {
  bool ok = false;
  String id;       // changes whenever the content changes
  String title;    // "Morning brief", "Afternoon brief", "Right now"
  String made;     // "8:00 AM"
  String forDay;   // "today" or "tomorrow" (what the jacket/cap/umbrella advice covers)
  String confidence;  // "high", "medium", "low" or ""
  int nowTemp = 0, nowFeels = 0;
  String nowText, nowIcon;
  bool nowDay = true;
  bool hasHigh = false, hasLow = false;
  int high = 0, low = 0;
  Item items[3];
  int itemCount = 0;
  Hour hours[6];
  int hourCount = 0;
  // The "later" screen: the next 12 hours (tomorrow in the evening)
  String laterTitle;  // "Next 12 hours" or "Tomorrow"
  String laterNote;   // "High 78° at 2 PM, low 55° at 6 AM, rain up to 10%"
  Hour later[12];
  int laterCount = 0;
  long utcOffset = DEFAULT_UTC_OFFSET;
  uint32_t sent = 0;  // when the bot sent it (Unix time), to set the clock
};

// Reads the bot's JSON. On failure returns false and says why in `error`.
bool parseBrief(const String& json, Brief& out, String& error);
