#pragma once
// The ST7789 screen (240x320, SPI) on the pins from settings.h
#include <LovyanGFX.hpp>

#include "settings.h"

class Screen : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 panel;
  lgfx::Bus_SPI bus;

 public:
  Screen() {
    auto b = bus.config();
    b.spi_host = SPI2_HOST;
    b.spi_mode = SCREEN_SPI_MODE;
    b.freq_write = SCREEN_SPI_MHZ * 1000000;
    b.freq_read = 16000000;
    b.spi_3wire = false;
    b.use_lock = true;
    b.dma_channel = SPI_DMA_CH_AUTO;
    b.pin_sclk = PIN_SCK;
    b.pin_mosi = PIN_MOSI;
    b.pin_miso = -1;  // the screen has no data-out pin
    b.pin_dc = PIN_DC;
    bus.config(b);
    panel.setBus(&bus);

    auto p = panel.config();
    p.pin_cs = PIN_CS;
    p.pin_rst = PIN_RST;
    p.pin_busy = -1;
    p.panel_width = 240;
    p.panel_height = 320;
    p.offset_x = 0;
    p.offset_y = 0;
    p.offset_rotation = 0;
    p.readable = false;
    p.invert = SCREEN_INVERT;
    p.rgb_order = SCREEN_BGR;
    p.dlen_16bit = false;
    p.bus_shared = false;
    panel.config(p);
    setPanel(&panel);
  }
};
