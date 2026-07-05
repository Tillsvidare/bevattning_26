# Hårdvara enligt hardware.md: pinnar och ventilpulser.
#
# Varje motorventil styrs med en 6 s HIGH-puls på öppna- respektive
# stäng-pinnen. Öppna och stäng får ALDRIG vara höga samtidigt på samma
# motor — allt pinnedrivande går genom pulse(), och anroparen
# (scheduler.ValveController) serialiserar med ett asyncio.Lock per ventil.

import asyncio
import time

from machine import ADC, Pin

VALVE_PULSE_S = 6

# Vattensensor (GPIO 1): analog, våt < 50 % av mätområdet (hardware.md).
WATER_THRESHOLD = 32768  # 50 % av read_u16-området 0-65535
ADC_SAMPLES = 5

m1_open = Pin(38, Pin.OUT, value=0)
m1_close = Pin(42, Pin.OUT, value=0)
m2_open = Pin(40, Pin.OUT, value=0)
m2_close = Pin(41, Pin.OUT, value=0)

red_led = Pin(7, Pin.OUT, value=0)
green_led = Pin(6, Pin.OUT, value=0)

water_adc = ADC(Pin(1), atten=ADC.ATTN_11DB)


def read_water():
    """Läs vattensensorn med majoritetsröstning (provpump-mönstret)."""
    readings = [water_adc.read_u16() < WATER_THRESHOLD for _ in range(ADC_SAMPLES)]
    return sum(readings) > ADC_SAMPLES // 2

# valve_id -> (öppna-pinne, stäng-pinne)
MOTORS = {1: (m1_open, m1_close), 2: (m2_open, m2_close)}


async def pulse(pin, heartbeat=None):
    """6 s HIGH-puls med garanterad avstängning (provpump-mönstret)."""
    pin.on()
    try:
        end = time.ticks_add(time.ticks_ms(), VALVE_PULSE_S * 1000)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            if heartbeat:
                heartbeat()
            await asyncio.sleep_ms(100)
    finally:
        pin.off()


def close_all_blocking():
    """Stäng båda ventilerna synkront vid uppstart (känt läge, före WDT)."""
    for open_pin, close_pin in MOTORS.values():
        open_pin.off()
        close_pin.on()
    time.sleep(VALVE_PULSE_S)
    for _, close_pin in MOTORS.values():
        close_pin.off()
