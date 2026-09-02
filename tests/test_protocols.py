"""Protocol-level regression tests for Sony Blu-ray capability routing."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientResponseError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from client import SonyBlurayDevice  # noqa: E402
from config import DeviceInstance  # noqa: E402
from media_player import features_for  # noqa: E402
from sonyapilib.device import (  # noqa: E402
    AuthenticationResult,
    DeviceCapabilities,
    DeviceState,
    SonyDevice,
    XmlApiObject,
)
from ucapi.media_player import Features  # noqa: E402


class DeviceCapabilityTests(unittest.TestCase):
    """Verify persisted protocol capabilities and playback parsers."""

    def test_capability_round_trip(self):
        capabilities = DeviceCapabilities(ircc=True, cers=True, dlna=True, wol=True)
        restored = DeviceCapabilities.from_protocols(capabilities.protocols)
        self.assertEqual(restored, capabilities)
        self.assertEqual(restored.backend, "ircc_cers")

    def test_mixed_ircc_dlna_does_not_advertise_renderer_timing_or_seek(self):
        capabilities = DeviceCapabilities(ircc=True, cers=True, dlna=True, wol=True)
        features = features_for(capabilities)
        self.assertFalse(capabilities.primary_dlna_transport)
        self.assertFalse(capabilities.media_timing)
        self.assertNotIn(Features.SEEK, features)
        self.assertNotIn(Features.MEDIA_POSITION, features)
        self.assertNotIn(Features.MEDIA_DURATION, features)
        self.assertIn(Features.PLAY_PAUSE, features)
        self.assertIn(Features.MEDIA_TITLE, features)

    def test_dlna_only_does_not_advertise_physical_media_controls(self):
        capabilities = DeviceCapabilities(dlna=True)
        features = features_for(capabilities)
        self.assertFalse(capabilities.primary_dlna_transport)
        self.assertFalse(capabilities.media_state)
        self.assertFalse(capabilities.media_timing)
        self.assertIsNone(capabilities.transport_protocol)
        self.assertNotIn(Features.SEEK, features)
        self.assertNotIn(Features.MEDIA_POSITION, features)
        self.assertNotIn(Features.MEDIA_DURATION, features)
        self.assertNotIn(Features.PLAY_PAUSE, features)
        self.assertNotIn(Features.STOP, features)
        self.assertNotIn(Features.PREVIOUS, features)
        self.assertNotIn(Features.NEXT, features)

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

    def test_parse_cers_legacy_viewing_wrapper(self):
        response = """<response><activity name="viewing"><details /></activity></response>"""
        info = SonyDevice._parse_cers_playback_info(response)
        self.assertEqual(info.state, DeviceState.PLAYING)
        self.assertIsNone(info.position)
        self.assertIsNone(info.duration)

    def test_parse_cers_content_info(self):
        response = """<response>
            <infoItem field="class" value="video" />
            <infoItem field="source" value="BD" />
            <infoItem field="mediaType" value="BD-ROM" />
            <infoItem field="mediaFormat" value="UHD" />
        </response>"""
        fields = SonyDevice._parse_cers_content_info(response)
        self.assertEqual(fields["source"], "BD")
        self.assertEqual(fields["mediatype"], "BD-ROM")
        self.assertEqual(fields["mediaformat"], "UHD")

    def test_parse_dlna_playback_info(self):
        transport = """<Envelope><CurrentTransportState>PAUSED_PLAYBACK</CurrentTransportState>
            <CurrentSpeed>1</CurrentSpeed></Envelope>"""
        position = """<Envelope><RelTime>00:01:05</RelTime>
            <TrackDuration>02:00:00</TrackDuration></Envelope>"""
        info = SonyDevice._parse_dlna_playback_info(transport, position)
        self.assertEqual(info.state, DeviceState.PAUSED)
        self.assertEqual(info.position, 65)
        self.assertEqual(info.duration, 7200)


class PlaybackFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Verify that DLNA renderer state does not corrupt physical-disc CERS state."""

    async def test_stopped_dlna_zero_timing_does_not_fill_cers_disc_state(self):
        device = SonyDevice("192.0.2.20", "test-client")
        device.capabilities.cers = True
        device.capabilities.dlna = True
        device.actions["getStatus"] = XmlApiObject({"name": "getStatus", "url": "http://example/status"})
        device._send_http = AsyncMock(return_value='<response><activity name="viewing" /></response>')
        device._get_dlna_playback_info = AsyncMock(
            return_value=device._parse_dlna_playback_info(
                "<Envelope><CurrentTransportState>STOPPED</CurrentTransportState><CurrentSpeed>1</CurrentSpeed></Envelope>",
                "<Envelope><RelTime>00:00:00</RelTime><TrackDuration>00:00:00</TrackDuration></Envelope>",
            )
        )

        info = await device.get_playback_info()
        self.assertEqual(info.state, DeviceState.PLAYING)
        self.assertIsNone(info.position)
        self.assertIsNone(info.duration)


class ReconnectRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """Verify reconnect registration is limited to APIs that require it."""

    @staticmethod
    def _config() -> DeviceInstance:
        return DeviceInstance(
            id="test-device",
            name="Sony test",
            client_name="test-client",
            address="192.0.2.30",
        )

    @patch("client.SonyDevice")
    async def test_legacy_api_does_not_reregister(self, sony_device_class):
        sony_device = sony_device_class.return_value
        sony_device.init_device = AsyncMock(return_value=True)
        sony_device.register = AsyncMock(return_value=AuthenticationResult.SUCCESS)
        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)
        sony_device.api_version = 1
        sony_device.registration_required = False
        sony_device.actions = {
            "register": XmlApiObject({"name": "register", "mode": "1", "url": "http://example/register"})
        }

        device = SonyBlurayDevice(self._config())
        await device.connect()

        sony_device.register.assert_not_awaited()

    @patch("client.SonyDevice")
    async def test_legacy_api_reregisters_when_command_list_is_protected(self, sony_device_class):
        sony_device = sony_device_class.return_value
        sony_device.init_device = AsyncMock(return_value=True)
        sony_device.register = AsyncMock(return_value=AuthenticationResult.SUCCESS)
        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)
        sony_device.api_version = 1
        sony_device.registration_required = True
        sony_device.actions = {
            "register": XmlApiObject({"name": "register", "mode": "1", "url": "http://example/register"})
        }

        device = SonyBlurayDevice(self._config())
        await device.connect()

        sony_device.register.assert_awaited_once()

    @patch("client.SonyDevice")
    async def test_mode3_without_pin_can_register_on_reconnect(self, sony_device_class):
        sony_device = sony_device_class.return_value
        sony_device.init_device = AsyncMock(return_value=True)
        sony_device.register = AsyncMock(return_value=AuthenticationResult.SUCCESS)
        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)
        sony_device.api_version = 3
        sony_device.registration_required = True
        sony_device.actions = {
            "register": XmlApiObject({"name": "register", "mode": "3", "url": "http://example/register"})
        }

        device = SonyBlurayDevice(self._config())
        await device.connect()

        sony_device.register.assert_awaited_once()


class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):
    """Protect command-list reads until registration when required."""

    @staticmethod
    def _http_error(status: int) -> ClientResponseError:
        request_info = MagicMock()
        request_info.real_url = "http://example/commands"
        return ClientResponseError(request_info=request_info, history=(), status=status)

    async def test_mode1_403_marks_registration_required_without_failing_init(self):
        device = SonyDevice("192.0.2.12", "test-client")
        register = XmlApiObject({"name": "register", "mode": "1", "url": "http://example/register"})
        device.actions["register"] = register
        device.capabilities.ircc = True
        device._update_service_urls = AsyncMock(return_value=True)
        device._update_commands = AsyncMock(side_effect=self._http_error(403))

        self.assertTrue(await device.init_device())
        self.assertTrue(device.registration_required)
        device._update_commands.assert_awaited_once()

    async def test_mode1_registered_device_reads_commands_without_reregister_flag(self):
        device = SonyDevice("192.0.2.13", "test-client")
        register = XmlApiObject({"name": "register", "mode": "1", "url": "http://example/register"})
        device.actions["register"] = register
        device.capabilities.ircc = True
        device._update_service_urls = AsyncMock(return_value=True)
        device._update_commands = AsyncMock()

        self.assertTrue(await device.init_device())
        self.assertFalse(device.registration_required)
        device._update_commands.assert_awaited_once()

    async def test_mode3_command_list_is_deferred_until_authenticated(self):
        device = SonyDevice("192.0.2.10", "test-client")
        register = XmlApiObject({"name": "register", "mode": "3", "url": "http://example/register"})
        device.actions["register"] = register
        device.capabilities.ircc = True
        device._update_service_urls = AsyncMock(return_value=True)
        device._update_commands = AsyncMock()
        device._update_applist = AsyncMock()

        self.assertTrue(await device.init_device())
        self.assertTrue(device.registration_required)
        device._update_commands.assert_not_awaited()

        device.pin = "1234"
        self.assertTrue(await device.init_device())
        self.assertFalse(device.registration_required)
        device._update_commands.assert_awaited_once()

    async def test_scalar_auth_cookie_is_a_mapping(self):
        device = SonyDevice("192.0.2.11", "test-client")
        device.cookies = {"auth": "token"}
        self.assertEqual(device._auth_cookies(), {"auth": "token"})
        self.assertIsInstance(device._auth_cookies(), dict)


if __name__ == "__main__":
    unittest.main()
