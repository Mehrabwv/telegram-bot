#pragma once
// Everything drawn on the screen (320 x 240, landscape).
#include <LovyanGFX.hpp>

#include "brief.h"
#include "feeds.h"

// Every screen gets `clock` (the current time, "9:41 PM", or "" if unknown) and
// `stale` (no word from the bot for a while: the header turns red).

// The weather brief: now, jacket/cap/umbrella, and the next hours.
void drawBrief(lgfx::LovyanGFX& g, const Brief& b, const String& clock, bool stale);

// The "later" screen: the next 12 hours (or tomorrow), in two rows.
void drawLater(lgfx::LovyanGFX& g, const Brief& b, const String& clock, bool stale);

// The headlines, one page of them (as many as fit; titles wrap to 3 lines).
// `page` counts from 0. `selected` (index into all headlines, or -1) gets a box.
void drawHeadlines(lgfx::LovyanGFX& g, const Headlines& h, int page, int selected,
                   const String& clock, bool stale);
// How many pages the headlines take, and which headlines are on a page
int headlinePages(lgfx::LovyanGFX& g, const Headlines& h);
void headlinePage(lgfx::LovyanGFX& g, const Headlines& h, int page, int& first, int& last);
// One story in full: section, rating, title and summary
void drawHeadlineDetail(lgfx::LovyanGFX& g, const Headlines& h, int index, const String& clock,
                        bool stale);

// The watchlist, one row per stock; `selected` (or -1) gets a box
void drawStocks(lgfx::LovyanGFX& g, const Stocks& s, int selected, const String& clock, bool stale);
// One stock in full: price, change, previous close, today's range, news
void drawStockDetail(lgfx::LovyanGFX& g, const Stocks& s, int index, const String& clock,
                     bool stale);

// A short message in a box near the bottom, over whatever is on the screen
void drawToast(lgfx::LovyanGFX& g, const String& text);

// A title and some lines of text. Lines starting with '*' are highlighted.
// `colorBars` adds red/green/blue/white bars at the bottom, to check the colors.
void drawMessage(lgfx::LovyanGFX& g, const String& title, const String* lines, int count,
                 bool colorBars);
