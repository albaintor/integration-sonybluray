from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    if old not in text:
        raise SystemExit(f"Pattern not found in {path}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1))


replace_once(
    "src/sonyapilib/device.py",
    """        self._initialized = False\n        self._registered = False\n\n        self.dmr_url = f\"http://{self.host}:{self.dmr_port}/dmr.xml\"\n""",
    """        self._initialized = False\n        self._registered = False\n        self._registration_required = False\n\n        self.dmr_url = f\"http://{self.host}:{self.dmr_port}/dmr.xml\"\n""",
)

replace_once(
    "src/sonyapilib/device.py",
    """        self._add_headers()\n        registration_action = self.actions.get(\"register\")\n        registration_mode = registration_action.mode if registration_action is not None else None\n        requires_registration = registration_mode is not None and registration_mode >= 3\n\n        if self.pin and registration_action is not None:\n            self._recreate_authentication()\n\n        # Mode 3/4 command lists are protected. During initial setup we only\n        # discover capabilities and the registration action; commands are read\n        # after registration/PIN authentication has succeeded.\n        if self.capabilities.ircc and (not requires_registration or self.pin or self._registered):\n            await self._update_commands()\n\n        if self.pin and registration_action is not None:\n""",
    """        self._add_headers()\n        registration_action = self.actions.get(\"register\")\n        registration_mode = registration_action.mode if registration_action is not None else None\n        known_registration_required = registration_mode is not None and registration_mode >= 3\n        self._registration_required = known_registration_required and not (self.pin or self._registered)\n\n        if self.pin and registration_action is not None:\n            self._recreate_authentication()\n\n        # Mode 3/4 command lists are known to be protected. Older players can\n        # also protect getRemoteCommandList until first registration (for\n        # example the BDP-S780 in mode 1), so detect 401/403 dynamically.\n        if self.capabilities.ircc and not self._registration_required:\n            try:\n                await self._update_commands()\n                self._registration_required = False\n            except ClientResponseError as ex:\n                if ex.status in (401, 403) and registration_action is not None and not self.pin and not self._registered:\n                    self._registration_required = True\n                    _LOGGER.debug(\n                        \"Command list requires registration on %s (mode %s, HTTP %s)\",\n                        self.host,\n                        registration_mode,\n                        ex.status,\n                    )\n                else:\n                    raise\n\n        if self.pin and registration_action is not None:\n""",
)

replace_once(
    "src/sonyapilib/device.py",
    """    @property\n    def initialized(self) -> bool:\n        \"\"\"Return true if initialized.\"\"\"\n        return self._initialized\n\n    # @staticmethod\n""",
    """    @property\n    def initialized(self) -> bool:\n        \"\"\"Return true if initialized.\"\"\"\n        return self._initialized\n\n    @property\n    def registration_required(self) -> bool:\n        \"\"\"Return whether the device requires registration before commands are available.\"\"\"\n        return self._registration_required\n\n    # @staticmethod\n""",
)

replace_once(
    "src/client.py",
    """                    self._capabilities.ircc\n                    and self._device_config.pin_code is None\n                    and sony_device.api_version >= 3\n                    and \"register\" in sony_device.actions\n""",
    """                    self._capabilities.ircc\n                    and self._device_config.pin_code is None\n                    and (sony_device.api_version >= 3 or sony_device.registration_required)\n                    and \"register\" in sony_device.actions\n""",
)

replace_once(
    "tests/test_protocols.py",
    "from unittest.mock import AsyncMock, patch\n",
    "from unittest.mock import AsyncMock, MagicMock, patch\n\nfrom aiohttp import ClientResponseError\n",
)

replace_once(
    "tests/test_protocols.py",
    """        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)\n        sony_device.api_version = 1\n        sony_device.actions = {\n""",
    """        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)\n        sony_device.api_version = 1\n        sony_device.registration_required = False\n        sony_device.actions = {\n""",
)

replace_once(
    "tests/test_protocols.py",
    """        sony_device.register.assert_not_awaited()\n\n    @patch(\"client.SonyDevice\")\n    async def test_mode3_without_pin_can_register_on_reconnect(self, sony_device_class):\n""",
    """        sony_device.register.assert_not_awaited()\n\n    @patch(\"client.SonyDevice\")\n    async def test_legacy_api_reregisters_when_command_list_is_protected(self, sony_device_class):\n        sony_device = sony_device_class.return_value\n        sony_device.init_device = AsyncMock(return_value=True)\n        sony_device.register = AsyncMock(return_value=AuthenticationResult.SUCCESS)\n        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)\n        sony_device.api_version = 1\n        sony_device.registration_required = True\n        sony_device.actions = {\n            \"register\": XmlApiObject({\"name\": \"register\", \"mode\": \"1\", \"url\": \"http://example/register\"})\n        }\n\n        device = SonyBlurayDevice(self._config())\n        await device.connect()\n\n        sony_device.register.assert_awaited_once()\n\n    @patch(\"client.SonyDevice\")\n    async def test_mode3_without_pin_can_register_on_reconnect(self, sony_device_class):\n""",
)

replace_once(
    "tests/test_protocols.py",
    """        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)\n        sony_device.api_version = 3\n        sony_device.actions = {\n""",
    """        sony_device.capabilities = DeviceCapabilities(ircc=True, cers=True)\n        sony_device.api_version = 3\n        sony_device.registration_required = True\n        sony_device.actions = {\n""",
)

replace_once(
    "tests/test_protocols.py",
    """class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):\n    \"\"\"Protect mode-3 players from pre-registration command-list requests.\"\"\"\n\n    async def test_mode3_command_list_is_deferred_until_authenticated(self):\n""",
    """class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):\n    \"\"\"Protect command-list reads until registration when required.\"\"\"\n\n    @staticmethod\n    def _http_error(status: int) -> ClientResponseError:\n        request_info = MagicMock()\n        request_info.real_url = \"http://example/commands\"\n        return ClientResponseError(request_info=request_info, history=(), status=status)\n\n    async def test_mode1_403_marks_registration_required_without_failing_init(self):\n        device = SonyDevice(\"192.0.2.12\", \"test-client\")\n        register = XmlApiObject({\"name\": \"register\", \"mode\": \"1\", \"url\": \"http://example/register\"})\n        device.actions[\"register\"] = register\n        device.capabilities.ircc = True\n        device._update_service_urls = AsyncMock(return_value=True)\n        device._update_commands = AsyncMock(side_effect=self._http_error(403))\n\n        self.assertTrue(await device.init_device())\n        self.assertTrue(device.registration_required)\n        device._update_commands.assert_awaited_once()\n\n    async def test_mode1_registered_device_reads_commands_without_reregister_flag(self):\n        device = SonyDevice(\"192.0.2.13\", \"test-client\")\n        register = XmlApiObject({\"name\": \"register\", \"mode\": \"1\", \"url\": \"http://example/register\"})\n        device.actions[\"register\"] = register\n        device.capabilities.ircc = True\n        device._update_service_urls = AsyncMock(return_value=True)\n        device._update_commands = AsyncMock()\n\n        self.assertTrue(await device.init_device())\n        self.assertFalse(device.registration_required)\n        device._update_commands.assert_awaited_once()\n\n    async def test_mode3_command_list_is_deferred_until_authenticated(self):\n""",
)

replace_once(
    "tests/test_protocols.py",
    """        self.assertTrue(await device.init_device())\n        device._update_commands.assert_not_awaited()\n\n        device.pin = \"1234\"\n""",
    """        self.assertTrue(await device.init_device())\n        self.assertTrue(device.registration_required)\n        device._update_commands.assert_not_awaited()\n\n        device.pin = \"1234\"\n""",
)

replace_once(
    "tests/test_protocols.py",
    """        self.assertTrue(await device.init_device())\n        device._update_commands.assert_awaited_once()\n\n    async def test_scalar_auth_cookie_is_a_mapping(self):\n""",
    """        self.assertTrue(await device.init_device())\n        self.assertFalse(device.registration_required)\n        device._update_commands.assert_awaited_once()\n\n    async def test_scalar_auth_cookie_is_a_mapping(self):\n""",
)
