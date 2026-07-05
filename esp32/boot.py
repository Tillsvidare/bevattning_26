# boot.py - update-mode bootstrap.
#
# MicroPython runs this before main.py on every power-on. If the button is
# held steadily for ~BUTTON_HOLD_MS at power-on, the device updates itself
# from GitHub (ota_update.run(): download changed files, verify, reboot
# normally) instead of letting main.py run. If the OTA attempt fails for
# any reason (no config.json, no WiFi, GitHub unreachable, bad download),
# it falls back to the WiFi access-point file manager (wifi_update.serve())
# so the device can always be rescued without a USB cable.
#
# The WDT must not be initialized here (it cannot be disabled once started
# and would reset the device mid-transfer).

import machine
import time

BUTTON_PIN = 5        # active-low button (external pull-up); held = enter update mode
BUTTON_HOLD_MS = 500  # steady hold time at power-on to enter update mode
RED_LED_PIN = 7       # solid on in update mode (set to None to skip)
BUZZER_PIN = 39       # short confirmation beep (set to None to skip)


def _update_requested():
    """True if the button (active low) is held steadily for ~BUTTON_HOLD_MS."""
    pin = machine.Pin(BUTTON_PIN, machine.Pin.IN)
    for _ in range(BUTTON_HOLD_MS // 50):
        if pin.value() == 1:  # released -> not held, resume normal boot
            return False
        time.sleep_ms(50)
    return True


def _feedback():
    """Confirm update mode: solid red LED and a short beep."""
    if RED_LED_PIN is not None:
        machine.Pin(RED_LED_PIN, machine.Pin.OUT, value=1)
    if BUZZER_PIN is not None:
        buzzer = machine.PWM(machine.Pin(BUZZER_PIN), freq=1000, duty=0)
        buzzer.duty_u16(32768)
        time.sleep_ms(200)
        buzzer.duty_u16(0)
        buzzer.deinit()  # release the pin so main.py never reconfigures it


if _update_requested():
    _feedback()
    try:
        import ota_update
        ota_update.run()  # resets into normal mode on success
    except Exception as e:
        print("OTA misslyckades (%s), startar AP-uppdateringsläget" % e)
    import wifi_update
    wifi_update.serve()  # rescue mode; reset (button up) to resume normal operation
