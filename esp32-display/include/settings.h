#pragma once
// ===== Settings you may want to change =====

// The display's name on your Wi-Fi. The bot finds it at http://weather-display.local
#define DEVICE_NAME "weather-display"
// The Wi-Fi network the display makes for setup. Join it with your phone.
#define SETUP_WIFI_NAME "Weather-Display-Setup"

// Screen orientation: 1 = landscape, 3 = landscape upside down
#define SCREEN_ROTATION 1
// If the colors look wrong on the setup screen's color bars:
//   black and white swapped -> change SCREEN_INVERT
//   red and blue swapped    -> change SCREEN_BGR
#define SCREEN_INVERT true
#define SCREEN_BGR false
#define SCREEN_SPI_MHZ 27  // lower it (e.g. 10) if the picture is garbled
#define SCREEN_SPI_MODE 0  // try 3 if the screen stays black

// Wiring (XIAO ESP32-S3 GPIO numbers)
#define PIN_SCK 7   // D8  -> SCL
#define PIN_MOSI 9  // D10 -> SDA
#define PIN_CS 1    // D0  -> CS
#define PIN_DC 2    // D1  -> DC
#define PIN_RST 4   // D3  -> RES
// Optional microSD module sharing SCK/MOSI: its CS is held high (deselected) so it
// can't interfere with the screen. Harmless when nothing is connected to D5.
#define PIN_SD_CS 6  // D5
#define PIN_BOOT_BUTTON 0  // the "B" button. Hold it 3 seconds to set up Wi-Fi again.

// The two buttons on top of the case. Each switch goes between its pin and GND.
// Presses move around, holds do something:
//   A press: next page / screen: weather -> headlines (pages) -> stocks -> later -> weather.
//            With details open: back to the list
//   B press: highlight the next headline or stock (with details open: the next one's).
//            On the weather: the later forecast (and back)
//   A hold:  open the highlighted headline or stock (more details); hold again to close
//   B hold:  send the highlighted headline or stock to your phone (through the bot).
//            On the weather screens: the status screen (Wi-Fi signal etc.)
//   Hold A and B together for 3 seconds: Wi-Fi setup.
// The XIAO's own B button: short = A press, longer = A hold, 3 seconds = Wi-Fi setup.
#define PIN_BUTTON_A 3        // D2
#define PIN_BUTTON_B 5        // D4
#define HOLD_MS 600           // a press this long counts as a hold
#define STATUS_SECONDS 10     // how long the status screen stays up

// Headlines come up by themselves every NEWS_EVERY_MINUTES (0 = only with the
// buttons), NEWS_PAGE_SECONDS a page, then the weather returns. Any other screen
// goes back to the weather after IDLE_SECONDS without a press.
#define NEWS_EVERY_MINUTES 5
#define NEWS_PAGE_SECONDS 10
#define IDLE_SECONDS 60

// The header turns red ("bot offline") when the bot hasn't sent anything for this long
#define STALE_MINUTES 10
// Clock time zone until the bot sends its own (seconds from UTC; Arizona = -7 hours)
#define DEFAULT_UTC_OFFSET (-7 * 3600)
