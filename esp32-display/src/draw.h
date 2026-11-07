#pragma once
// Everything drawn on the screen (320 x 240, landscape).
#include <LovyanGFX.hpp>

#include "brief.h"
#include "headlines.h"

// The weather brief: now, jacket/cap/umbrella, and the next hours.
// `clock` is the current time ("9:41 PM", or "" if unknown); `stale` = no word
// from the bot for a while.
void drawBrief(lgfx::LovyanGFX& g, const Brief& b, const String& clock, bool stale);

// The NYT headlines, one page of them (as many as fit; long titles wrap to 3 lines).
// `page` counts from 0. Pages beyond the last show the last one.
void drawHeadlines(lgfx::LovyanGFX& g, const Headlines& h, int page, const String& clock,
                   bool stale);
// How many pages the headlines take
int headlinePages(lgfx::LovyanGFX& g, const Headlines& h);

// A title and some lines of text. Lines starting with '*' are highlighted.
// `colorBars` adds red/green/blue/white bars at the bottom, to check the colors.
void drawMessage(lgfx::LovyanGFX& g, const String& title, const String* lines, int count,
                 bool colorBars);
