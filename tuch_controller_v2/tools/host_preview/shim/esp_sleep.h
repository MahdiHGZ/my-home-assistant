// Host shim for <esp_sleep.h>.
#pragma once
#include <cstdint>

typedef enum {
  ESP_SLEEP_WAKEUP_UNDEFINED = 0,
  ESP_SLEEP_WAKEUP_ALL,
  ESP_SLEEP_WAKEUP_EXT0,
  ESP_SLEEP_WAKEUP_EXT1,
  ESP_SLEEP_WAKEUP_TIMER,
  ESP_SLEEP_WAKEUP_TOUCHPAD,
  ESP_SLEEP_WAKEUP_ULP,
  ESP_SLEEP_WAKEUP_GPIO,
} esp_sleep_wakeup_cause_t;

#define ESP_EXT1_WAKEUP_ANY_LOW 0
#define ESP_EXT1_WAKEUP_ANY_HIGH 1

inline esp_sleep_wakeup_cause_t esp_sleep_get_wakeup_cause() { return ESP_SLEEP_WAKEUP_UNDEFINED; }
inline int esp_sleep_enable_timer_wakeup(uint64_t) { return 0; }
inline int esp_sleep_enable_ext1_wakeup(uint64_t, int) { return 0; }
inline int esp_sleep_enable_gpio_wakeup() { return 0; }
inline void esp_light_sleep_start() {}
inline void esp_deep_sleep_start() {}
