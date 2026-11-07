#include "draw.h"

namespace {

// Colors (0xRRGGBB)
const uint32_t BG = 0x0B1220u;
const uint32_t HEADER = 0x16213Au;
const uint32_t HEADER_STALE = 0x7F1D1Du;
const uint32_t PANEL = 0x16213Au;
const uint32_t TEXT = 0xF1F5F9u;
const uint32_t DIM = 0x94A3B8u;
const uint32_t FAINT = 0x64748Bu;
const uint32_t DARK_TEXT = 0x0B1220u;
const uint32_t NO_CARD = 0x1E293Bu;
const uint32_t SUN = 0xFBBF24u;
const uint32_t MOON = 0xE2E8F0u;
// Clouds: a body color plus a darker shade underneath, so they look rounded
const uint32_t CLOUD = 0xF0F7FFu;        // fair-weather cloud: near white...
const uint32_t CLOUD_SHADE = 0x7DB4F0u;  // ...with a sky-blue underside
const uint32_t CLOUD_BACK = 0xA9C4E8u;   // the cloud behind, on "cloudy"
const uint32_t CLOUD_BACK_SHADE = 0x5F86BDu;
const uint32_t RAIN_CLOUD = 0xB4C0D3u;   // rain/snow: grey-blue
const uint32_t RAIN_CLOUD_SHADE = 0x6B7C96u;
const uint32_t STORM_CLOUD = 0x9A8FC4u;  // thunderstorm: purple-grey
const uint32_t STORM_CLOUD_SHADE = 0x5E5390u;
const uint32_t RAIN = 0x60A5FAu;
const uint32_t SNOW = 0xFFFFFFu;
const uint32_t BOLT = 0xFACC15u;
const uint32_t GOOD = 0x22C55Eu;
const uint32_t SO_SO = 0xF59E0Bu;
const uint32_t BAD = 0xEF4444u;
const uint32_t ITEM_COLORS[3] = {0xF97316u, 0xFACC15u, 0x60A5FAu};  // jacket, cap, umbrella

const int W = 320;
const char DEGREE[] = "\xC2\xB0";  // "°" in UTF-8: drawn as a small ring (the fonts lack it)

// ---------- text ----------

int ringRadius(lgfx::LovyanGFX& g) { return max(2, (int)g.fontHeight() / 11); }

// Where capital letters start below the y given to drawString (top-left datum)
int capTop(lgfx::LovyanGFX& g) { return g.fontHeight() / 8; }

int textWidth(lgfx::LovyanGFX& g, const String& s) {
  int width = 0, start = 0;
  while (true) {
    int at = s.indexOf(DEGREE, start);
    String part = s.substring(start, at < 0 ? s.length() : at);
    width += g.textWidth(part.c_str());
    if (at < 0) return width;
    width += ringRadius(g) * 2 + 3;
    start = at + 2;
  }
}

// Draws text with its top-left corner at (x, y). Returns its width.
int drawText(lgfx::LovyanGFX& g, const String& s, int x, int y, uint32_t color) {
  g.setTextColor(color);
  g.setTextDatum(textdatum_t::top_left);
  int cx = x, start = 0, r = ringRadius(g);
  while (true) {
    int at = s.indexOf(DEGREE, start);
    String part = s.substring(start, at < 0 ? s.length() : at);
    if (part.length()) {
      g.drawString(part.c_str(), cx, y);
      cx += g.textWidth(part.c_str());
    }
    if (at < 0) return cx - x;
    int ringX = cx + r + 1, ringY = y + capTop(g) + r;
    g.drawCircle(ringX, ringY, r, color);
    if (r >= 3) g.drawCircle(ringX, ringY, r - 1, color);
    cx += r * 2 + 3;
    start = at + 2;
  }
}

int drawCentered(lgfx::LovyanGFX& g, const String& s, int centerX, int y, uint32_t color) {
  return drawText(g, s, centerX - textWidth(g, s) / 2, y, color);
}

int drawRight(lgfx::LovyanGFX& g, const String& s, int rightX, int y, uint32_t color) {
  return drawText(g, s, rightX - textWidth(g, s), y, color);
}

// Cuts the text to fit `maxWidth` pixels, ending in "..." if it was cut
String fit(lgfx::LovyanGFX& g, String s, int maxWidth) {
  if (textWidth(g, s) <= maxWidth) return s;
  while (s.length() > 1 && textWidth(g, s + "...") > maxWidth) s.remove(s.length() - 1);
  s.trim();
  return s + "...";
}

String tempText(int value) { return String(value) + DEGREE; }

// ---------- weather icons (drawn from shapes, centered on cx, cy, about `s` pixels) ----------

void sunShape(lgfx::LovyanGFX& g, float cx, float cy, float s) {
  g.fillCircle(cx, cy, s * 0.21f, SUN);
  float inner = s * 0.31f, outer = s * 0.45f, width = max(1.0f, s / 28.0f);
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4;
    g.drawWideLine(cx + cosf(a) * inner, cy + sinf(a) * inner, cx + cosf(a) * outer,
                   cy + sinf(a) * outer, width, SUN);
  }
}

void moonShape(lgfx::LovyanGFX& g, float cx, float cy, float s, uint32_t bg) {
  float r = s * 0.3f;
  g.fillCircle(cx, cy, r, MOON);
  g.fillCircle(cx + r * 0.6f, cy - r * 0.4f, r * 0.8f, bg);
}

// The cloud outline: `w` pixels wide, centered on (cx, cy)
void cloudBody(lgfx::LovyanGFX& g, float cx, float cy, float w, uint32_t color) {
  float bottom = cy + w * 0.27f, base = w * 0.22f;
  g.fillRoundRect(cx - w / 2, bottom - base, w, base, base / 2, color);
  g.fillCircle(cx - w * 0.2f, bottom - base * 0.9f, w * 0.2f, color);
  g.fillCircle(cx + w * 0.08f, bottom - base * 1.2f, w * 0.27f, color);
  g.fillCircle(cx + w * 0.3f, bottom - base * 0.8f, w * 0.16f, color);
}

// A cloud with a darker shade peeking out below and to the right
void cloudShape(lgfx::LovyanGFX& g, float cx, float cy, float w, uint32_t color, uint32_t shade) {
  float dx = max(1.0f, w * 0.04f), dy = max(1.0f, w * 0.07f);
  cloudBody(g, cx + dx, cy + dy, w, shade);
  cloudBody(g, cx, cy, w, color);
}

void drawIcon(lgfx::LovyanGFX& g, const String& icon, bool day, int cx, int cy, int s,
              uint32_t bg) {
  float line = max(1.0f, s / 22.0f);
  if (icon == "clear") {
    if (day) sunShape(g, cx, cy, s);
    else moonShape(g, cx, cy, s, bg);
  } else if (icon == "partly") {
    if (day) sunShape(g, cx - s * 0.16f, cy - s * 0.16f, s * 0.72f);
    else moonShape(g, cx - s * 0.12f, cy - s * 0.14f, s * 0.8f, bg);
    cloudShape(g, cx + s * 0.08f, cy + s * 0.1f, s * 0.7f, CLOUD, CLOUD_SHADE);
  } else if (icon == "cloudy") {
    cloudShape(g, cx - s * 0.12f, cy - s * 0.1f, s * 0.6f, CLOUD_BACK, CLOUD_BACK_SHADE);
    cloudShape(g, cx + s * 0.06f, cy + s * 0.06f, s * 0.78f, CLOUD, CLOUD_SHADE);
  } else if (icon == "fog") {
    for (int i = 0; i < 4; i++) {
      float y = cy - s * 0.27f + i * s * 0.18f;
      float x = cx - s * 0.4f + (i % 2) * s * 0.08f;
      g.fillRoundRect(x, y, s * 0.72f, max(2.0f, s * 0.08f), max(1.0f, s * 0.04f),
                      i % 2 ? CLOUD_SHADE : CLOUD_BACK);
    }
  } else if (icon == "drizzle" || icon == "rain" || icon == "snow" || icon == "storm") {
    if (icon == "storm") cloudShape(g, cx, cy - s * 0.15f, s * 0.82f, STORM_CLOUD, STORM_CLOUD_SHADE);
    else cloudShape(g, cx, cy - s * 0.15f, s * 0.82f, RAIN_CLOUD, RAIN_CLOUD_SHADE);
    float top = cy + s * 0.2f;
    for (int i = -1; i <= 1; i++) {
      float x = cx + i * s * 0.24f;
      if (icon == "drizzle") {
        g.fillCircle(x, top + s * 0.1f, max(1.0f, s * 0.045f), RAIN);
      } else if (icon == "rain") {
        g.drawWideLine(x + s * 0.04f, top, x - s * 0.06f, top + s * 0.24f, line, RAIN);
      } else if (icon == "snow") {
        g.fillCircle(x, top + s * 0.06f + (i == 0) * s * 0.12f, max(1.5f, s * 0.055f), SNOW);
      }
    }
    if (icon == "storm") {
      g.fillTriangle(cx + s * 0.06f, top - s * 0.06f, cx - s * 0.12f, top + s * 0.16f,
                     cx + s * 0.02f, top + s * 0.16f, BOLT);
      g.fillTriangle(cx - s * 0.04f, top + s * 0.12f, cx + s * 0.11f, top + s * 0.12f,
                     cx - s * 0.08f, top + s * 0.34f, BOLT);
    }
  }
}

uint32_t confidenceColor(const String& level) {
  if (level == "high") return GOOD;
  if (level == "medium") return SO_SO;
  return BAD;
}

// The header bar: title, a dim subtitle, and the clock on the right
void drawHeader(lgfx::LovyanGFX& g, const String& title, const String& subtitle,
                const String& clock, bool stale) {
  g.fillRect(0, 0, W, 24, stale ? HEADER_STALE : HEADER);
  g.setFont(&fonts::FreeSansBold9pt7b);
  int clockWidth = clock.length() ? textWidth(g, clock) : 0;
  if (clockWidth) drawText(g, clock, W - 8 - clockWidth, 4, TEXT);
  int x = 8 + drawText(g, title, 8, 4, TEXT) + 8;
  g.setFont(&fonts::FreeSans9pt7b);
  drawText(g, fit(g, stale ? String("bot offline") : subtitle, W - 16 - clockWidth - x), x, 4,
           stale ? 0xFCA5A5u : DIM);
}

// Splits text into at most `maxLines` lines of `maxWidth` pixels (the last one ends
// in "..." if the text doesn't fit). Returns the number of lines.
int wrapText(lgfx::LovyanGFX& g, const String& s, int maxWidth, int maxLines, String* out) {
  int count = 0, pos = 0, len = s.length();
  String line;
  while (pos < len && count < maxLines) {
    int space = s.indexOf(' ', pos);
    if (space < 0) space = len;
    int wordStart = pos;
    String word = s.substring(pos, space);
    pos = space + 1;
    if (!word.length()) continue;
    String trial = line.length() ? line + " " + word : word;
    if (!line.length() || textWidth(g, trial) <= maxWidth) {
      line = trial;
      continue;
    }
    if (count == maxLines - 1) {  // no more lines: the rest goes here, cut short
      out[count++] = fit(g, line + " " + s.substring(wordStart), maxWidth);
      return count;
    }
    out[count++] = line;
    line = word;
  }
  if (line.length() && count < maxLines) out[count++] = fit(g, line, maxWidth);
  return count;
}

// Headline layout
const int NEWS_TOP = 30, NEWS_BOTTOM = 238;
const int NEWS_SECTION_H = 16, NEWS_LINE_H = 20, NEWS_GAP = 9, NEWS_MAX_LINES = 3;
const int NEWS_TEXT_W = W - 16;
const uint32_t SECTION_COLOR = 0x60A5FAu;
const uint32_t DIVIDER = 0x1E293Bu;

int headlineLines(lgfx::LovyanGFX& g, const Headline& item, String* lines) {
  g.setFont(&fonts::FreeSans9pt7b);
  return wrapText(g, item.title, NEWS_TEXT_W, NEWS_MAX_LINES, lines);
}

// Fills `starts` with the first headline of each page; returns the page count
int layoutHeadlines(lgfx::LovyanGFX& g, const Headlines& h, int* starts, int maxPages) {
  int pages = 0, y = NEWS_BOTTOM + 1;  // "full", so the first headline starts a page
  String lines[NEWS_MAX_LINES];
  for (int i = 0; i < h.count; i++) {
    int height = NEWS_SECTION_H + headlineLines(g, h.items[i], lines) * NEWS_LINE_H;
    if (y + height > NEWS_BOTTOM) {
      if (pages == maxPages) break;
      starts[pages++] = i;
      y = NEWS_TOP;
    }
    y += height + NEWS_GAP;
  }
  return pages;
}

}  // namespace

int headlinePages(lgfx::LovyanGFX& g, const Headlines& h) {
  if (!h.ok) return 1;
  int starts[MAX_HEADLINES];
  return max(1, layoutHeadlines(g, h, starts, MAX_HEADLINES));
}

void drawHeadlines(lgfx::LovyanGFX& g, const Headlines& h, int page, const String& clock,
                   bool stale) {
  g.fillScreen(BG);
  if (!h.ok) {
    drawHeader(g, "Headlines", "", clock, stale);
    g.setFont(&fonts::FreeSansBold9pt7b);
    drawText(g, "No headlines yet", 10, 40, TEXT);
    g.setFont(&fonts::FreeSans9pt7b);
    drawText(g, "The bot sends them with the weather.", 10, 66, DIM);
    drawText(g, "If this stays, restart the bot.", 10, 88, DIM);
    return;
  }
  int starts[MAX_HEADLINES];
  int pages = max(1, layoutHeadlines(g, h, starts, MAX_HEADLINES));
  page = constrain(page, 0, pages - 1);
  int first = starts[page], last = page + 1 < pages ? starts[page + 1] : h.count;

  // "1/3  9:25 PM": the page, then when the bot collected them (cut first if short of room)
  String subtitle = pages > 1 ? String(page + 1) + "/" + String(pages) : "";
  if (h.made.length()) subtitle += (subtitle.length() ? "   " : "") + h.made;
  drawHeader(g, "Headlines", subtitle, clock, stale);

  int y = NEWS_TOP;
  String lines[NEWS_MAX_LINES];
  for (int i = first; i < last; i++) {
    if (i > first) g.drawFastHLine(8, y - NEWS_GAP / 2 - 1, W - 16, DIVIDER);
    String section = String(i + 1) + "  " + h.items[i].section;
    section.toUpperCase();
    g.setFont(&fonts::Font2);
    drawText(g, fit(g, section, NEWS_TEXT_W), 8, y, SECTION_COLOR);
    int count = headlineLines(g, h.items[i], lines);  // sets the title font
    for (int l = 0; l < count; l++) {
      drawText(g, lines[l], 8, y + NEWS_SECTION_H + l * NEWS_LINE_H, TEXT);
    }
    y += NEWS_SECTION_H + count * NEWS_LINE_H + NEWS_GAP;
  }
}

// ---------- screens ----------

void drawBrief(lgfx::LovyanGFX& g, const Brief& b, const String& clock, bool stale) {
  g.fillScreen(BG);

  // Header: "Morning brief  8:00 AM" ............ "9:41 AM"
  drawHeader(g, b.title, b.made, clock, stale);

  // Now: icon, temperature, feels like, conditions, high and low
  drawIcon(g, b.nowIcon, b.nowDay, 30, 52, 44, BG);
  bool wide = String(b.nowTemp).length() >= 3;  // 108 or -12: one size smaller, to leave room
  g.setFont(wide ? &fonts::FreeSansBold18pt7b : &fonts::FreeSansBold24pt7b);
  int tempWidth = drawText(g, tempText(b.nowTemp), 58, wide ? 35 : 30, TEXT);
  int textX = 58 + tempWidth + 10;
  g.setFont(&fonts::FreeSans9pt7b);
  String high = b.hasHigh ? "H " + tempText(b.high) : "";
  String low = b.hasLow ? "L " + tempText(b.low) : "";
  int highLowWidth = max(textWidth(g, high), textWidth(g, low));
  if (high.length()) drawRight(g, high, W - 8, 32, TEXT);
  if (low.length()) drawRight(g, low, W - 8, 54, DIM);
  drawText(g, "Feels " + tempText(b.nowFeels), textX, 32, TEXT);
  drawText(g, fit(g, b.nowText, W - 8 - highLowWidth - 10 - textX), textX, 54, DIM);

  // "What to bring today" ......... "Confidence: high"
  g.setFont(&fonts::Font2);
  drawText(g, b.forDay == "tomorrow" ? "What to bring tomorrow" : "What to bring today", 8, 79, DIM);
  if (b.confidence.length()) {
    int width = drawRight(g, "Confidence: " + b.confidence, W - 8, 79, DIM);
    g.fillCircle(W - 8 - width - 10, 87, 4, confidenceColor(b.confidence));
  }

  // Jacket / cap / umbrella cards: colored = bring it
  for (int i = 0; i < b.itemCount && i < 3; i++) {
    const Item& item = b.items[i];
    int cardX = 6 + i * 104, cardY = 97, cardW = 100, cardH = 68;
    uint32_t fill = item.need ? ITEM_COLORS[i] : NO_CARD;
    uint32_t ink = item.need ? DARK_TEXT : DIM;
    g.fillRoundRect(cardX, cardY, cardW, cardH, 8, fill);
    String name = item.name;
    name.toUpperCase();
    g.setFont(&fonts::Font2);
    drawText(g, fit(g, name, cardW - 12), cardX + 7, cardY + 3, item.need ? DARK_TEXT : FAINT);
    g.setFont(&fonts::FreeSansBold12pt7b);
    drawText(g, item.answer, cardX + 7, cardY + 18, item.need ? DARK_TEXT : DIM);
    g.setFont(&fonts::Font2);
    drawText(g, fit(g, item.note1, cardW - 12), cardX + 7, cardY + 37, ink);
    drawText(g, fit(g, item.note2, cardW - 12), cardX + 7, cardY + 51, ink);
  }

  // The next hours: time, icon, temperature, chance of rain
  if (b.hourCount > 0) {
    int panelX = 6, panelY = 169, panelW = W - 12, panelH = 69;
    g.fillRoundRect(panelX, panelY, panelW, panelH, 8, PANEL);
    float columnW = (float)panelW / b.hourCount;
    for (int i = 0; i < b.hourCount; i++) {
      const Hour& h = b.hours[i];
      int cx = panelX + columnW * (i + 0.5f);
      g.setFont(&fonts::Font2);
      drawCentered(g, h.time, cx, panelY + 2, DIM);
      drawIcon(g, h.icon, h.day, cx, panelY + 29, 24, PANEL);
      g.setFont(&fonts::FreeSansBold9pt7b);
      drawCentered(g, tempText(h.temp), cx, panelY + 40, TEXT);
      g.setFont(&fonts::Font2);
      drawCentered(g, String(h.rain) + "%", cx, panelY + 53, h.rain >= 20 ? RAIN : FAINT);
    }
  }
}

void drawMessage(lgfx::LovyanGFX& g, const String& title, const String* lines, int count,
                 bool colorBars) {
  g.fillScreen(BG);
  g.fillRect(0, 0, W, 24, HEADER);
  g.setFont(&fonts::FreeSansBold9pt7b);
  drawText(g, title, 8, 4, TEXT);
  int y = 34;
  for (int i = 0; i < count; i++) {
    bool bold = lines[i].startsWith("*");
    g.setFont(bold ? &fonts::FreeSansBold9pt7b : &fonts::FreeSans9pt7b);
    drawText(g, fit(g, bold ? lines[i].substring(1) : lines[i], W - 20), 10, y, bold ? TEXT : DIM);
    y += 22;
  }
  if (colorBars) {
    const uint32_t colors[4] = {0xFF0000u, 0x00FF00u, 0x0000FFu, 0xFFFFFFu};
    const char* names[4] = {"RED", "GREEN", "BLUE", "WHITE"};
    g.setFont(&fonts::Font2);
    for (int i = 0; i < 4; i++) {
      g.fillRect(i * 80, 204, 80, 36, colors[i]);
      drawCentered(g, names[i], i * 80 + 40, 214, i == 1 || i == 3 ? 0x000000u : 0xFFFFFFu);
    }
  }
}
