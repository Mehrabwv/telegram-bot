#pragma once
// Everything drawn on the screen (320 x 240, landscape).
#include <LovyanGFX.hpp>

#include "brief.h"

// The weather brief: now, jacket/cap/umbrella, and the next hours.
// `clock` is the current time ("9:41 PM", or "" if unknown); `stale` = no word
// from the bot for a while.
void drawBrief(lgfx::LovyanGFX& g, const Brief& b, const String& clock, bool stale);

// A title and some lines of text. Lines starting with '*' are highlighted.
// `colorBars` adds red/green/blue/white bars at the bottom, to check the colors.
void drawMessage(lgfx::LovyanGFX& g, const String& title, const String* lines, int count,
                 bool colorBars);
