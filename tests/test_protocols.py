"""Protocol-level regression tests for Sony Blu-ray capability routing."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sonyapilib.device import DeviceCapabilities, DeviceState, SonyDevice, XmlApiObject  # noqa: E402


class DeviceCapabilityTests(unittest.TestCase):
    """Verify persisted protocol capabilities and playback parsers."""

    def test_capability_round_trip(self):
        capabilities = DeviceCapabilities(ircc=True, cers=True, dlna=True, wol=True)
        restored = DeviceCapabilities.from_protocols(capabilities.protocols)
        self.assertEqual(restored, capabilities)
        self.assertEqual(restored.backend, "ircc_cers")

    def test_parse_cers_playback_info(self):
        response = """<response><status name="viewing">
            <item field="source" value="BD" />
            <item field="state" value="playing" />
            <item field="position" value="65" />
            <item field="duration" value="7200" />
            <item field="speed" value="1" />
        </status></response>"""
        info = SonyDevice._parse_cers_playback_info(response)
        self.assertEqual(info.state, DeviceState.PLAYING)
        self.assertEqual(info.source, "BD")
        self.assertEqual(info.position, 65)
        self.assertEqual(info.duration, 7200)

    def test_parse_dlna_playback_info(self):
        transport = """<Envelope><CurrentTransportState>PAUSED_PLAYBACK</CurrentTransportState>
            <CurrentSpeed>1</CurrentSpeed></Envelope>"""
        position = """<Envelope><RelTime>00:01:05</RelTime>
            <TrackDuration>02:00:00</TrackDuration></Envelope>"""
        info = SonyDevice._parse_dlna_playback_info(transport, position)
        self.assertEqual(info.state, DeviceState.PAUSED)
        self.assertEqual(info.position, 65)
        self.assertEqual(info.duration, 7200)


class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):
    """Protect mode-3 players from pre-registration command-list requests."""

    async def test_mode3_command_list_is_deferred_until_authenticated(self):
        device = SonyDevice("192.0.2.10", "test-client")
        register = XmlApiObject({"name": "register", "mode": "3", "url": "http://example/register"})
        device.actions["register"] = register
        device.capabilities.ircc = True
        device._update_service_urls = AsyncMock(return_value=True)
        device._update_commands = AsyncMock()
        device._update_applist = AsyncMock()

        self.assertTrue(await device.init_device())
        device._update_commands.assert_not_awaited()

        device.pin = "1234"
        self.assertTrue(await device.init_device())
        device._update_commands.assert_awaited_once()

    async def test_scalar_auth_cookie_is_a_mapping(self):
        device = SonyDevice("192.0.2.11", "test-client")
        device.cookies = {"auth": "token"}
        self.assertEqual(device._auth_cookies(), {"auth": "token"})
        self.assertIsInstance(device._auth_cookies(), dict)


if __name__ == "__main__":
    unittest.main()
