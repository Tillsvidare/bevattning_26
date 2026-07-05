---
name: lbr-hardware
description: Expert knowledge for ESP32-S3 based water leak detector/breaker (läckagebrytare/lbr) hardware. Use when working with GPIO pin assignments, motor control, sensors, LEDs, buzzer, relay, switches, or any hardware-related code for the LBR project. Covers pin configurations, signal timing, and peripheral interfacing.
---

# LBR Hardware Reference

ESP32-S3 based water leak detector with dual motor valve control.

## Pin Assignments

| Signal         | GPIO | Type    | Active | Notes                        |
|----------------|------|---------|--------|------------------------------|
| Button         | 5    | Input   | LOW    | External pull-up             |
| Water sensor   | 1    | ADC     | -      | ATTN_11DB, wet < 50% range   |
| Motor 1 open   | 38   | Output  | HIGH   | Pulse 6s                     |
| Motor 1 close  | 42   | Output  | HIGH   | Pulse 6s                     |
| Motor 2 open   | 40   | Output  | HIGH   | Pulse 6s                     |
| Motor 2 close  | 41   | Output  | HIGH   | Pulse 6s                     |
| Red LED        | 7    | Output  | HIGH   |                              |
| Green LED      | 6    | Output  | HIGH   |                              |
| Buzzer         | 39   | PWM     | -      | 50% duty cycle               |
| Alarm relay    | 21   | Output  | HIGH   | External pull-down           |
| External input | 2    | Input   | LOW    | External pull-up             |
| Switch 1       | 8    | Input   | LOW    | External pull-up             |
| Switch 2       | 9    | Input   | LOW    | External pull-up             |

### Exposed Pins (J-headers)

| Label   | GPIO | Notes        |
|---------|------|--------------|
| pin 11  | 48   | Exposed pin  |
| J9-2    | 47   | Exposed pin  |
| J9-3    | 11   | Exposed pin  |
| pin 10  | 10   | Exposed pin  |

## Signal Specifications

### Motor Control
- Pulse duration: 6 seconds HIGH to actuate
- Never activate open and close simultaneously on same motor
- Allow motor to complete before reversing direction

### Water Sensor (ADC)
- Configure with `ADC.ATTN_11DB` for full range
- Threshold: wet condition when reading < 50% of max range
- Sample periodically, consider averaging for noise rejection

### Inputs
- All inputs active LOW with external pull-ups
- No internal pull-up configuration needed
- Debounce recommended for button/switches (~50ms)

### Buzzer
- Use PWM at 50% duty cycle
- Frequency typically 2-4 kHz for audible alarm

## MicroPython Patterns

### GPIO Setup
```python
from machine import Pin, ADC, PWM

# Outputs (active high)
led_red = Pin(7, Pin.OUT, value=0)
led_green = Pin(6, Pin.OUT, value=0)
relay = Pin(21, Pin.OUT, value=0)

# Motor pins
m1_open = Pin(38, Pin.OUT, value=0)
m1_close = Pin(42, Pin.OUT, value=0)
m2_open = Pin(40, Pin.OUT, value=0)
m2_close = Pin(41, Pin.OUT, value=0)

# Inputs (active low, external pull-up)
button = Pin(5, Pin.IN)
ext_input = Pin(2, Pin.IN)
sw1 = Pin(8, Pin.IN)
sw2 = Pin(9, Pin.IN)

# ADC
water = ADC(Pin(1))
water.atten(ADC.ATTN_11DB)
```

### Motor Actuation
```python
import asyncio

async def actuate_motor(open_pin, close_pin, direction):
    """direction: 'open' or 'close'"""
    pin = open_pin if direction == 'open' else close_pin
    pin.on()
    await asyncio.sleep(6)
    pin.off()
```

### Water Detection
```python
def is_wet():
    reading = water.read_u16()
    return reading < 32768  # < 50% of 16-bit range
```

### Buzzer Alarm
```python
buzzer = PWM(Pin(39), freq=2500, duty_u16=0)

def alarm_on():
    buzzer.duty_u16(32768)  # 50%

def alarm_off():
    buzzer.duty_u16(0)
```