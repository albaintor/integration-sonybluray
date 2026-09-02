from pathlib import Path

client = Path("src/client.py")
text = client.read_text()
old = '''                if (
                    self._capabilities.ircc
                    and self._device_config.pin_code is None
                    and "register" in sony_device.actions
                ):
'''
new = '''                if (
                    self._capabilities.ircc
                    and self._device_config.pin_code is None
                    and sony_device.api_version >= 3
                    and "register" in sony_device.actions
                ):
'''
if old not in text:
    raise SystemExit("client.py registration block not found")
client.write_text(text.replace(old, new, 1))

tests = Path("tests/test_protocols.py")
text = tests.read_text()
text = text.replace("from unittest.mock import AsyncMock\n", "from unittest.mock import AsyncMock, patch\n", 1)
text = text.replace(
    "from media_player import features_for  # noqa: E402\n",
    "from client import SonyBlurayDevice  # noqa: E402\n"
    "from config import DeviceInstance  # noqa: E402\n"
    "from media_player import features_for  # noqa: E402\n",
    1,
)
text = text.replace(
    "from sonyapilib.device import DeviceCapabilities, DeviceState, SonyDevice, XmlApiObject  # noqa: E402\n",
    "from sonyapilib.device import (  # noqa: E402\n"
    "    AuthenticationResult,\n"
    "    DeviceCapabilities,\n"
    "    DeviceState,\n"
    "    SonyDevice,\n"
    "    XmlApiObject,\n"
    ")\n",
    1,
)
marker = "\n\nclass RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):\n"
addition = '''

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
        sony_device.actions = {
            "register": XmlApiObject({"name": "register", "mode": "1", "url": "http://example/register"})
        }

        device = SonyBlurayDevice(self._config())
        await device.connect()

        sony_device.register.assert_not_awaited()

    @patch("client.SonyDevice")
    async def test_mode3_without_pin_can_register_on_reconnect(self, sony_device_class):
        sony_device = sony_device_class.return_value
        sony_device.init_device = AsyncMock(return_value=True)
        sony_device.register = AsyncMock(return_value=AuthenticationResult.SUCCESS)
        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)
        sony_device.api_version = 3
        sony_device.actions = {
            "register": XmlApiObject({"name": "register", "mode": "3", "url": "http://example/register"})
        }

        device = SonyBlurayDevice(self._config())
        await device.connect()

        sony_device.register.assert_awaited_once()
'''
if marker not in text:
    raise SystemExit("test insertion marker not found")
tests.write_text(text.replace(marker, addition + marker, 1))
