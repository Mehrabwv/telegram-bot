#pragma once
// The headlines and stock prices the bot sends (as JSON), ready to draw.
#include <Arduino.h>

#define MAX_HEADLINES 9
#define MAX_STOCKS 5

struct Headline {
  String id;       // the bot's id for the story ("send to my phone" uses it)
  String title;    // plain ASCII (the bot replaces curly quotes, dashes etc.)
  String section;  // "Technology", "U.S.", ...
  String summary;  // a few sentences, for the story's detail view
  String tag;      // "MAJOR", "IMPORTANT", "TECH/SCI" or "" (the AI's rating)
};

struct Headlines {
  bool ok = false;
  String id;    // changes when the bot collects new headlines
  String made;  // when the bot collected them, e.g. "9:25 AM"
  Headline items[MAX_HEADLINES];
  int count = 0;
};

struct Stock {
  String ticker;      // "NVDA"
  String name;        // "NVIDIA Corporation"
  String price;       // "$182.55"
  String changeText;  // "+1.25%"
  float change = 0;   // percent since the previous close (for the color)
  String prev;        // previous close, "$180.30"
  String range;       // today's low - high, "$179.10 - $183.40"
  String news[2];     // recent NYT headlines about it
  int newsCount = 0;
};

struct Stocks {
  bool ok = false;
  String id;
  String made;    // when the prices are from, e.g. "1:05 PM"
  String market;  // "Market open" / "Market closed"
  Stock items[MAX_STOCKS];
  int count = 0;
};

// Read the bot's JSON. On failure they return false and say why in `error`.
bool parseHeadlines(const String& json, Headlines& out, String& error);
bool parseStocks(const String& json, Stocks& out, String& error);
