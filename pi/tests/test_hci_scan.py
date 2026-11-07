import struct
import unittest

from canary_core import signature
from hci_scan import (EVT_CMD_COMPLETE, HCI_EVENT_PKT, LE_SET_SCAN_ENABLE, command, command_status, event_filter, opcode,
                      parse_ad, parse_event)

# Our test phone's advert: flags + manufacturer data 058E / CA01
AD = bytes([0x02, 0x01, 0x06, 0x05, 0xFF, 0x8E, 0x05, 0xCA, 0x01])
ADDR = bytes([0x66, 0x55, 0x44, 0x33, 0x22, 0x11])   # little-endian on the wire -> 11:22:33:44:55:66


def legacy_event(rssi, data=AD):
    report = bytes([0x03, 0x01]) + ADDR + bytes([len(data)]) + data + struct.pack("b", rssi)
    body = bytes([0x02, 0x01]) + report                       # subevent, num_reports
    return bytes([HCI_EVENT_PKT, 0x3E, len(body)]) + body


def extended_event(rssi, data=AD):
    fixed = struct.pack("<HB", 0x0010, 0x01) + ADDR + bytes([1, 0, 0xFF]) + struct.pack("bb", 12, rssi) \
        + struct.pack("<H", 0) + bytes([0]) + bytes(6) + bytes([len(data)])
    body = bytes([0x0D, 0x01]) + fixed + data
    return bytes([HCI_EVENT_PKT, 0x3E, len(body)]) + body


class HciParseTests(unittest.TestCase):
    def test_ad_structures(self):
        manufacturer, uuids = parse_ad(AD + bytes([0x03, 0x03, 0x6F, 0xFD]))
        self.assertEqual(manufacturer, {0x058E: bytes.fromhex("CA01")})
        self.assertEqual(uuids, ["0000fd6f-0000-1000-8000-00805f9b34fb"])

    def test_legacy_report(self):
        [(device, adv)] = parse_event(legacy_event(-47))
        self.assertEqual(device.address, "11:22:33:44:55:66")
        self.assertEqual(adv.rssi, -47)
        self.assertEqual(signature(adv), "phone_simulator")    # same matching as with bleak

    def test_extended_report(self):
        [(device, adv)] = parse_event(extended_event(-62))
        self.assertEqual(adv.rssi, -62)
        self.assertEqual(signature(adv), "phone_simulator")

    def test_rssi_unavailable_and_other_events_ignored(self):
        self.assertEqual(parse_event(legacy_event(127)), [])
        self.assertEqual(parse_event(bytes([HCI_EVENT_PKT, 0x05, 0x04, 0, 0, 0, 0])), [])

    def test_event_filter_is_full_kernel_struct_size(self):
        f = event_filter()
        self.assertEqual(len(f), 16)                          # sizeof(struct hci_ufilter)
        self.assertEqual(struct.unpack("<IIIH", f[:14]), (1 << 4, (1 << 0x0E) | (1 << 0x0F), 1 << (0x3E - 32), 0))

    def test_commands_and_status(self):
        self.assertEqual(command(LE_SET_SCAN_ENABLE, bytes([1, 0])), bytes([0x01, 0x0C, 0x20, 0x02, 0x01, 0x00]))
        complete = bytes([HCI_EVENT_PKT, EVT_CMD_COMPLETE, 4, 1]) + struct.pack("<H", opcode(LE_SET_SCAN_ENABLE)) + bytes([0x0C])
        self.assertEqual(command_status(complete, LE_SET_SCAN_ENABLE), 0x0C)


if __name__ == "__main__":
    unittest.main()
