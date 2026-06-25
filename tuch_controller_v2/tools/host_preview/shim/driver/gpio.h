// Host shim for <driver/gpio.h>.
#pragma once
#include <cstdint>
typedef int gpio_num_t;
typedef int gpio_int_type_t;
#define GPIO_INTR_LOW_LEVEL 4
#define GPIO_INTR_HIGH_LEVEL 5
inline int gpio_wakeup_enable(gpio_num_t, gpio_int_type_t) { return 0; }
