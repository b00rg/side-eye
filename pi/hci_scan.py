"""Raw HCI scanner: every BLE advert the Pi's radio hears, not BlueZ's thinned-out updates.

BlueZ (via bleak) reports adverts sparingly (~1-2 per second for our phone). The controller itself
hears every advert (~10/s per channel). This talks to the controller over a raw HCI socket, turns
scanning on with duplicate filtering OFF, and hands each advert to the same callback canary.py
uses with bleak: callback(device, advertisement) with .address, .manufacturer_data, .service_uuids
and .rssi. Use it with `python3 canary.py --hci <command>`.

Needs raw-socket rights: run with sudo for a quick test, or once:
  sudo setcap cap_net_raw,cap_net_admin+eip "$(readlink -f "$(which python3)")"
Linux only (the Pi). Tries legacy LE scan commands first; if the controller refuses them (it
does once BlueZ has used Bluetooth 5 extended scanning), switches to the extended commands.
"""
import asyncio
import socket
import struct
import threading
from types import SimpleNamespace

HCI_COMMAND_PKT, HCI_EVENT_PKT = 0x01, 0x04
EVT_CMD_COMPLETE, EVT_CMD_STATUS, EVT_LE_META = 0x0E, 0x0F, 0x3E
SUB_ADV_REPORT, SUB_EXT_ADV_REPORT = 0x02, 0x0D
OGF_LE = 0x08
LE_SET_SCAN_PARAMS, LE_SET_SCAN_ENABLE = 0x000B, 0x000C
LE_SET_EXT_SCAN_PARAMS, LE_SET_EXT_SCAN_ENABLE = 0x0041, 0x0042
STATUS_DISALLOWED, STATUS_UNKNOWN_CMD = 0x0C, 0x01
SCAN_INTERVAL = SCAN_WINDOW = 0x0010   # 10 ms window every 10 ms: listen continuously


def event_filter():
    """struct hci_ufilter: u32 type_mask, u32 event_mask[2], u16 opcode, padded to 16 bytes (newer
    kernels reject anything shorter than sizeof(struct hci_ufilter) with EINVAL)."""
    return struct.pack("<IIIHxx", 1 << HCI_EVENT_PKT, (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS),
                       1 << (EVT_LE_META - 32), 0)


def opcode(ocf, ogf=OGF_LE):
    return (ogf << 10) | ocf


def command(ocf, params=b""):
    return struct.pack("<BHB", HCI_COMMAND_PKT, opcode(ocf), len(params)) + params


def uuid16(value):
    return f"0000{value:04x}-0000-1000-8000-00805f9b34fb"


def parse_ad(data):
    """AD structures -> (manufacturer_data {company_id: bytes}, service UUIDs [str])."""
    manufacturer, uuids, i = {}, [], 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + 1 + length > len(data):
            break
        ad_type, payload = data[i + 1], data[i + 2:i + 1 + length]
        if ad_type == 0xFF and len(payload) >= 2:
            manufacturer[int.from_bytes(payload[:2], "little")] = bytes(payload[2:])
        elif ad_type in (0x02, 0x03):
            uuids += [uuid16(int.from_bytes(payload[j:j + 2], "little")) for j in range(0, len(payload) - 1, 2)]
        elif ad_type in (0x06, 0x07):
            for j in range(0, len(payload) - 15, 16):
                h = payload[j:j + 16][::-1].hex()
                uuids.append(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}")
        i += 1 + length
    return manufacturer, uuids


def address(raw):
    return ":".join(f"{b:02X}" for b in raw[::-1])


def advert(addr, data, rssi):
    manufacturer, uuids = parse_ad(data)
    device = SimpleNamespace(address=addr)
    adv = SimpleNamespace(manufacturer_data=manufacturer, service_uuids=uuids, rssi=rssi, local_name=None)
    return device, adv


def parse_event(packet):
    """One raw HCI event packet -> list of (device, advertisement). Non-advert events -> []."""
    if len(packet) < 4 or packet[0] != HCI_EVENT_PKT or packet[1] != EVT_LE_META:
        return []
    sub, body = packet[3], packet[4:]
    out = []
    if sub == SUB_ADV_REPORT:   # legacy: per report evt_type, addr_type, addr[6], len, data, rssi
        i = 1
        for _ in range(body[0]):
            if i + 9 > len(body):
                break
            addr, length = body[i + 2:i + 8], body[i + 8]
            data, rssi = body[i + 9:i + 9 + length], struct.unpack("b", body[i + 9 + length:i + 10 + length])[0]
            if rssi != 127:   # 127 = RSSI not available
                out.append(advert(address(addr), data, rssi))
            i += 10 + length
    elif sub == SUB_EXT_ADV_REPORT:   # extended: 24 fixed bytes, then data
        i = 1
        for _ in range(body[0]):
            if i + 24 > len(body):
                break
            addr, rssi, length = body[i + 3:i + 9], struct.unpack("b", body[i + 13:i + 14])[0], body[i + 23]
            data = body[i + 24:i + 24 + length]
            if rssi != 127:
                out.append(advert(address(addr), data, rssi))
            i += 24 + length
    return out


def command_status(packet, ocf):
    """Status byte if this event completes our command, else None."""
    if len(packet) >= 7 and packet[0] == HCI_EVENT_PKT and packet[1] == EVT_CMD_COMPLETE:
        if struct.unpack("<H", packet[4:6])[0] == opcode(ocf):
            return packet[6]
    if len(packet) >= 7 and packet[0] == HCI_EVENT_PKT and packet[1] == EVT_CMD_STATUS:
        if struct.unpack("<H", packet[5:7])[0] == opcode(ocf):
            return packet[3]
    return None


class HciScanner:
    """Async context manager with the same use as bleak's BleakScanner(callback)."""

    def __init__(self, callback, device=0):
        self.callback, self.device = callback, device
        self.sock, self.thread, self.running, self.extended = None, None, False, False
        self.total_adverts = 0   # every advert from every device (diagnostics)

    def _send(self, ocf, params=b"", timeout=2.0):
        self.sock.sendall(command(ocf, params))
        self.sock.settimeout(timeout)
        try:
            while True:
                status = command_status(self.sock.recv(260), ocf)
                if status is not None:
                    return status
        except socket.timeout:
            return None
        finally:
            self.sock.settimeout(None)

    def _start_scan(self):
        self._send(LE_SET_SCAN_ENABLE, bytes([0, 0]))            # stop any scan in progress
        self._send(LE_SET_EXT_SCAN_ENABLE, bytes([0, 0]) + bytes(4))
        status = self._send(LE_SET_SCAN_PARAMS, struct.pack("<BHHBB", 0, SCAN_INTERVAL, SCAN_WINDOW, 0, 0))
        if status in (STATUS_DISALLOWED, STATUS_UNKNOWN_CMD) or status is None:
            self.extended = True
            ext = struct.pack("<BBB", 0, 0, 0x01) + struct.pack("<BHH", 0, SCAN_INTERVAL, SCAN_WINDOW)
            status = self._send(LE_SET_EXT_SCAN_PARAMS, ext)
            if status != 0:
                raise RuntimeError(f"controller refused scan parameters (status 0x{status or 0:02x})")
            status = self._send(LE_SET_EXT_SCAN_ENABLE, struct.pack("<BBHH", 1, 0, 0, 0))   # duplicates ON
        else:
            status = self._send(LE_SET_SCAN_ENABLE, bytes([1, 0]))                        # duplicates ON
        if status != 0:
            raise RuntimeError(f"controller refused to start scanning (status 0x{status or 0:02x})")

    def _reader(self, loop):
        while self.running:
            try:
                packet = self.sock.recv(260)
            except OSError:
                break
            for device, adv in parse_event(packet):
                self.total_adverts += 1
                loop.call_soon_threadsafe(self.callback, device, adv)

    async def __aenter__(self):
        try:
            self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
            self.sock.setsockopt(socket.SOL_HCI, socket.HCI_FILTER, event_filter())
            self.sock.bind((self.device,))
        except PermissionError:
            raise SystemExit("--hci needs raw Bluetooth access: run with sudo, or once: "
                             "sudo setcap cap_net_raw,cap_net_admin+eip \"$(readlink -f \"$(which python3)\")\"")
        self._start_scan()
        self.running = True
        self.thread = threading.Thread(target=self._reader, args=(asyncio.get_running_loop(),), daemon=True)
        self.thread.start()
        print(f"Raw HCI scanning on hci{self.device} ({'extended' if self.extended else 'legacy'} commands, "
              "duplicates not filtered).", flush=True)
        return self

    async def __aexit__(self, *exc):
        self.running = False
        try:
            if self.extended:
                self._send(LE_SET_EXT_SCAN_ENABLE, bytes([0, 0]) + bytes(4), timeout=0.5)
            else:
                self._send(LE_SET_SCAN_ENABLE, bytes([0, 0]), timeout=0.5)
        finally:
            self.sock.close()
        return False
