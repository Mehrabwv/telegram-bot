#pragma once
// The NYT headlines the bot sends (as JSON), ready to draw.
#include <Arduino.h>

#define MAX_HEADLINES 9

struct Headline {
  String title;    // plain ASCII (the bot replaces curly quotes, dashes etc.)
  String section;  // "Technology", "U.S.", ...
};

struct Headlines {
  bool ok = false;
  String id;    // changes when the bot collects new headlines
  String made;  // when the bot collected them, e.g. "9:25 AM"
  Headline items[MAX_HEADLINES];
  int count = 0;
};

// Reads the bot's JSON. On failure returns false and says why in `error`.
bool parseHeadlines(const String& json, Headlines& out, String& error);
