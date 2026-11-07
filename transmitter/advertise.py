"""Stand-in smart glasses: broadcast our test packet (company 0x058E, data CA01) from any Linux board
with Bluetooth, e.g. the Arduino UNO Q's Linux side or a spare Raspberry Pi. Python 3, no installs.

Usage (on the board):
  sudo python3 advertise.py                    # every 20 ms until Ctrl+C
  sudo python3 advertise.py --interval-ms 100  # same rate the phone was set to

Talks to the Bluetooth controller directly over a raw HCI socket (like pi/hci_scan.py) so we
control the rate exactly: non-connectable, legacy advertising, manufacturer data only, highest
default TX power. If the controller refuses legacy commands (it does once BlueZ has used
Bluetooth 5 extended advertising), uses the extended advertising commands instead.
Check the board has a controller first: `bluetoothctl show` (should list one, Powered: yes).
"""
import argparse
import socket
import struct
import sys
import time

COMPANY_ID, PAYLOAD = 0x058E, bytes.fromhex("CA01")
HCI_COMMAND_PKT, HCI_EVENT_PKT = 0x01, 0x04
EVT_CMD_COMPLETE, EVT_CMD_STATUS = 0x0E, 0x0F
OGF_LE = 0x08
SET_ADV_PARAMS, SET_ADV_DATA, SET_ADV_ENABLE = 0x0006, 0x0008, 0x000A
SET_EXT_ADV_PARAMS, SET_EXT_ADV_DATA, SET_EXT_ADV_ENABLE, REMOVE_ADV_SET = 0x0036, 0x0037, 0x0039, 0x003C
ADV_NONCONN_IND = 0x03
ALL_CHANNELS = 0x07
STATUS_DISALLOWED, STATUS_UNKNOWN_CMD = 0x0C, 0x01


def opcode(ocf):
    return (OGF_LE << 10) | ocf


def command(ocf, params=b""):
    return struct.pack("<BHB", HCI_COMMAND_PKT, opcode(ocf), len(params)) + params


def adv_data():
    """Flags (BR/EDR not supported) + manufacturer specific data: company ID (little-endian) + payload."""
    manufacturer = struct.pack("<BBH", 3 + len(PAYLOAD), 0xFF, COMPANY_ID) + PAYLOAD
    return bytes([0x02, 0x01, 0x04]) + manufacturer


def legacy_params(interval_units):
    # min/max interval, type, own addr type, peer addr type, peer addr, channel map, filter policy
    return struct.pack("<HHBBB6sBB", interval_units, interval_units, ADV_NONCONN_IND, 0, 0, bytes(6), ALL_CHANNELS, 0)


def legacy_data():
    data = adv_data()
    return bytes([len(data)]) + data.ljust(31, b"\x00")


def ext_params(interval_units):
    # handle 0; properties 0x0010 = legacy PDU, non-connectable non-scannable (ADV_NONCONN_IND);
    # 3-byte min/max interval; channels; own addr; peer addr type+addr; filter; tx power 0x7F = no preference;
    # primary PHY 1M; secondary max skip; secondary PHY 1M; SID; scan request notifications off
    iv = interval_units.to_bytes(3, "little")
    return (struct.pack("<BH", 0, 0x0010) + iv + iv
            + struct.pack("<BBB6sBbBBBBB", ALL_CHANNELS, 0, 0, bytes(6), 0, 0x7F, 0x01, 0, 0x01, 0, 0))


def ext_data():
    data = adv_data()
    return struct.pack("<BBBB", 0, 0x03, 0x01, len(data)) + data   # handle 0, complete data, no fragmentation


def ext_enable(on):
    return struct.pack("<BB", 1 if on else 0, 1) + struct.pack("<BHB", 0, 0, 0)   # 1 set: handle 0, no duration


def status_for(packet, ocf):
    if len(packet) >= 7 and packet[0] == HCI_EVENT_PKT and packet[1] == EVT_CMD_COMPLETE \
            and struct.unpack("<H", packet[4:6])[0] == opcode(ocf):
        return packet[6]
    if len(packet) >= 7 and packet[0] == HCI_EVENT_PKT and packet[1] == EVT_CMD_STATUS \
            and struct.unpack("<H", packet[5:7])[0] == opcode(ocf):
        return packet[3]
    return None


class Controller:
    def __init__(self, device=0):
        self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        self.sock.setsockopt(socket.SOL_HCI, socket.HCI_FILTER,
                             struct.pack("<IIIHxx", 1 << HCI_EVENT_PKT,
                                         (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS), 0, 0))
        self.sock.bind((device,))

    def send(self, ocf, params=b"", timeout=2.0):
        self.sock.sendall(command(ocf, params))
        self.sock.settimeout(timeout)
        try:
            while True:
                status = status_for(self.sock.recv(260), ocf)
                if status is not None:
                    return status
        except socket.timeout:
            return None


def start(ctl, interval_units):
    ctl.send(SET_ADV_ENABLE, b"\x00")
    status = ctl.send(SET_ADV_PARAMS, legacy_params(interval_units))
    if status == 0:
        for ocf, params in ((SET_ADV_DATA, legacy_data()), (SET_ADV_ENABLE, b"\x01")):
            if ctl.send(ocf, params) != 0:
                raise SystemExit(f"controller refused legacy command 0x{ocf:04x}")
        return "legacy"
    if status not in (STATUS_DISALLOWED, STATUS_UNKNOWN_CMD, None):
        raise SystemExit(f"controller refused advertising parameters (status 0x{status:02x})")
    ctl.send(SET_EXT_ADV_ENABLE, ext_enable(False))
    ctl.send(REMOVE_ADV_SET, b"\x00")
    for ocf, params in ((SET_EXT_ADV_PARAMS, ext_params(interval_units)), (SET_EXT_ADV_DATA, ext_data()),
                        (SET_EXT_ADV_ENABLE, ext_enable(True))):
        status = ctl.send(ocf, params)
        if status != 0:
            raise SystemExit(f"controller refused extended command 0x{ocf:04x} (status {status})")
    return "extended"


def stop(ctl, mode):
    if mode == "legacy":
        ctl.send(SET_ADV_ENABLE, b"\x00", timeout=0.5)
    else:
        ctl.send(SET_EXT_ADV_ENABLE, ext_enable(False), timeout=0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval-ms", type=float, default=20.0, help="advertising interval, 20-10240 ms (default 20)")
    ap.add_argument("--hci", type=int, default=0, help="controller number (hci0 = 0)")
    args = ap.parse_args()
    units = max(0x20, min(0x4000, round(args.interval_ms / 0.625)))   # spec range: 20 ms to 10.24 s
    try:
        ctl = Controller(args.hci)
    except PermissionError:
        sys.exit("Needs raw Bluetooth access: run with sudo.")
    except OSError as e:
        sys.exit(f"No Bluetooth controller hci{args.hci} ({e}). Check `bluetoothctl show`.")
    mode = start(ctl, units)
    print(f"Advertising 058E/CA01 every {units * 0.625:.1f} ms ({mode} commands) on hci{args.hci}. Ctrl+C to stop.",
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop(ctl, mode)
        print("\nStopped advertising.")


if __name__ == "__main__":
    main()
