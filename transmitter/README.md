# Transmitter: the stand-in smart glasses

Our stand-in for smart glasses is an **Arduino UNO Q** (Linux side, hostname `zeus`) broadcasting
our test packet: manufacturer data company **0x058E** (Meta Platforms Technologies), payload
**CA01**, non-connectable, every **20 ms**. It replaced the old Android phone running nRF Connect.

| Transmitter | Pi readings in 20 s (scan check, ~1 m) | Per second |
|---|---|---|
| Android phone, nRF Connect, 100 ms set | 41 | ~2 (Android doesn't honour the setting) |
| UNO Q, 20 ms, on a power bank | 184 | ~9 |

More readings per second means steadier per-second features and no missing rows (the Pi only
writes a row with at least 5 readings in the last 5 s).

## How zeus is set up
Broadcasting starts by itself about 25 s after the board gets power (USB-C power bank is fine).
It's a cron `@reboot` entry for the `arduino` user, no sudo needed:
```
@reboot sleep 25; setsid sh -c "(printf \"menu advertise\nmanufacturer 0x058e 0xca 0x01\ninterval 20 20\nback\nadvertise broadcast\n\"; sleep infinity) | bluetoothctl" > /home/arduino/advertise.log 2>&1
```
To set it up on a fresh board: plug it into the Mac (data USB-C cable), `adb shell`, then
```
(crontab -l 2>/dev/null; echo '<the line above>') | crontab -
crontab -l
```
To check it after boot: `adb shell` then `cat /home/arduino/advertise.log`; it should end with
"Advertising object registered", "Manufacturer: 1422  ca 01" and "Interval: 20-20 msec".

To broadcast by hand instead (e.g. to change something), in `adb shell`:
```
bluetoothctl
menu advertise
manufacturer 0x058e 0xca 0x01
interval 20 20
back
advertise broadcast
```
(keep that window open; quitting stops the broadcast). TX power is the controller default; this
bluetoothctl version can't set a level, which is fine as long as it's the same for calibration
and all walks.

## Wearing it
At head height, where glasses sit: on a cap or headband, power bank in a pocket. Same position
for calibration and every walk (see `docs/DECISIONS.md`).

## Alternative: advertise.py
`advertise.py` does the same over a raw HCI socket on any Linux board with Bluetooth (needs sudo),
with an exact interval and legacy/extended fallback. Not used on zeus (no sudo password there);
kept for other boards, e.g. a spare Raspberry Pi.
