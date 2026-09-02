"""
Client for Sony Media Players.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import base64
import json
import logging
import socket
import struct
import xml.etree.ElementTree
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, urljoin, urlparse

import aiohttp
import jsonpickle
from aiohttp import ClientResponseError, ClientTimeout
from aiohttp.web_exceptions import HTTPError

# pylint: disable=C0302

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 5
URN_UPNP_DEVICE = "{urn:schemas-upnp-org:device-1-0}"
URN_SONY_AV = "{urn:schemas-sony-com:av}"
URN_SONY_IRCC = "urn:schemas-sony-com:serviceId:IRCC"
URN_SCALAR_WEB_API_DEVICE_INFO = "{urn:schemas-sony-com:av}"
WEBAPI_SERVICETYPE = "av:X_ScalarWebAPI_ServiceType"
AVTRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"


class DeviceState(Enum):
    """Device state."""

    OFF = 0
    STOPPED = 1
    PLAYING = 2
    PAUSED = 3


class ControlProtocol(Enum):
    """Network protocols exposed by a Sony device."""

    IRCC = "ircc"
    CERS = "cers"
    SCALAR = "scalar"
    DLNA = "dlna"
    WOL = "wol"


@dataclass
class DeviceCapabilities:
    """Network capabilities detected from device descriptors and actions."""

    ircc: bool = False
    cers: bool = False
    scalar: bool = False
    dlna: bool = False
    wol: bool = False

    @classmethod
    def from_protocols(cls, protocols: list[str] | None) -> "DeviceCapabilities":
        """Create capabilities from values persisted in the integration config."""
        values = set(protocols or [])
        return cls(
            ircc=ControlProtocol.IRCC.value in values,
            cers=ControlProtocol.CERS.value in values,
            scalar=ControlProtocol.SCALAR.value in values,
            dlna=ControlProtocol.DLNA.value in values,
            wol=ControlProtocol.WOL.value in values,
        )

    @classmethod
    def legacy_defaults(cls) -> "DeviceCapabilities":
        """Preserve the feature set of configurations created before detection existed."""
        return cls(ircc=True, cers=True, dlna=True, wol=True)

    @property
    def protocols(self) -> list[str]:
        """Return enabled protocols in a stable, serializable order."""
        enabled = {
            ControlProtocol.IRCC: self.ircc,
            ControlProtocol.CERS: self.cers,
            ControlProtocol.SCALAR: self.scalar,
            ControlProtocol.DLNA: self.dlna,
            ControlProtocol.WOL: self.wol,
        }
        return [protocol.value for protocol, available in enabled.items() if available]

    @property
    def media_state(self) -> bool:
        """Return whether physical-media playback state can be queried."""
        # Sony DMR AVTransport represents the network renderer, not the
        # physical Blu-ray/DVD/USB transport (e.g. UBP-X800M2).
        return self.cers

    @property
    def primary_dlna_transport(self) -> bool:
        """Return whether DLNA can be used as the physical-media transport."""
        return False

    @property
    def media_timing(self) -> bool:
        """Return whether physical-media position/duration are reliable."""
        return False

    @property
    def backend(self) -> str:
        """Return the primary control backend selected for this device."""
        if self.ircc:
            return "scalar_ircc" if self.scalar else "ircc_cers"
        if self.dlna:
            return "dlna_only"
        if self.wol:
            return "wol_only"
        return "unsupported"

    @property
    def transport_protocol(self) -> str | None:
        """Return the protocol used for physical-media transport commands."""
        if self.ircc:
            return ControlProtocol.IRCC.value
        return None


@dataclass
class PlaybackInfo:
    """Normalized playback information returned by CERS or AVTransport."""

    state: DeviceState = DeviceState.STOPPED
    position: int | None = None
    duration: int | None = None
    source: str | None = None
    title: str | None = None
    speed: float | None = None


class AuthenticationResult(Enum):
    """Store the result of the authentication process."""

    SUCCESS = 0
    ERROR = 1
    PIN_NEEDED = 2


class HttpMethod(Enum):
    """Define which http method is used."""

    GET = "get"
    POST = "post"


class IrccCategory(Enum):
    """Device categories used by IRCC."""

    TV1 = 1
    AUSYS3 = 80
    TV1EEE = 119
    TV1E = 164
    AUSYS3E = 208
    AUSYS3SE = 528
    AUSYS3EE = 1552
    DVD4 = 3578
    DVD4E = 3834
    BD1 = 7258


IR_KEY_CODES = {
    IrccCategory.BD1: (
        ("Num1", 0),
        ("Num2", 1),
        ("Num3", 2),
        ("Num4", 3),
        ("Num5", 4),
        ("Num6", 5),
        ("Num7", 6),
        ("Num8", 7),
        ("Num9", 8),
        ("Num0", 9),
        ("Power", 21),
        ("Eject", 22),
        ("Stop", 24),
        ("Pause", 25),
        ("Play", 26),
        ("Rewind", 27),
        ("Forward", 28),
        ("PopUpMenu", 41),
        ("TopMenu", 44),
        ("Up", 57),
        ("Down", 58),
        ("Left", 59),
        ("Right", 60),
        ("Confirm", 61),
        ("Options", 63),
        ("Display", 65),
        ("Home", 66),
        ("Return", 67),
        ("Karaoke", 74),
        ("Netflix", 75),
        ("Mode3D", 77),
        ("Next", 86),
        ("Prev", 87),
        ("Favorites", 94),
        ("SubTitle", 99),
        ("Audio", 100),
        ("Angle", 101),
        ("Blue", 102),
        ("Red", 103),
        ("Green", 104),
        ("Yellow", 105),
        ("Advance", 117),
        ("Replay", 118),
    )
}


class XmlApiObject:
    # pylint: disable=too-few-public-methods
    """Holds data for a device action or a command."""

    def __init__(self, xml_data):
        """Init xml object with given data."""
        self.name = None
        self.mode = None
        self.url = None
        self.type = None
        self.value = None
        self.mac = None
        # must be named that way to match xml
        # pylint: disable=invalid-name
        self.id = None
        if not xml_data:
            return

        for attr in self.__dict__:
            if attr == "mode" and xml_data.get(attr):
                xml_data[attr] = int(xml_data[attr])
            setattr(self, attr, xml_data.get(attr))


class SonyDevice:
    # pylint: disable=too-many-public-methods
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=fixme
    """Contains all data for the device."""

    def __init__(self, host, nickname, psk=None, app_port=50202, dmr_port=52323, ircc_port=50001):
        # pylint: disable=too-many-arguments,R0917
        """Init the device with the entry point."""
        self.host = host
        self.nickname = nickname
        self.client_id = nickname
        self.actionlist_url = None
        self.control_url = None
        self.av_transport_url = None
        self.app_url = None
        self.psk = psk

        self.app_port = app_port
        self.dmr_port = dmr_port
        self.ircc_port = ircc_port

        # actions are thing like getting status
        self.actions: dict[str, XmlApiObject] = {}
        self.headers = {}
        # commands are alike to buttons on the remote
        self.commands = {}
        self.apps = {}

        self.pin = None
        self.cookies = None
        self.mac: str | None = None
        self.api_version = 0
        self.capabilities = DeviceCapabilities()
        self._initialized = False
        self._registered = False
        self._registration_required = False

        self.dmr_url = f"http://{self.host}:{self.dmr_port}/dmr.xml"
        self.app_url = f"http://{self.host}:{self.app_port}"
        self.base_url = f"http://{self.host}/sony/"
        ircc_base = f"http://{self.host}:{self.ircc_port}"
        if self.ircc_port == self.dmr_port:
            self.ircc_url = self.dmr_url
        else:
            self.ircc_url = urljoin(ircc_base, "/Ircc.xml")

        self.irccscpd_url = urljoin(ircc_base, "/IRCCSCPD.xml")
        self._ircc_categories = set()
        self._add_headers()
        self._event_loop = asyncio.get_event_loop() or asyncio.get_running_loop()

    async def init_device(self) -> bool:
        """Update this object with data from the device."""
        if not await self._update_service_urls():
            return False

        self._add_headers()
        registration_action = self.actions.get("register")
        registration_mode = registration_action.mode if registration_action is not None else None
        known_registration_required = registration_mode is not None and registration_mode >= 3
        self._registration_required = known_registration_required and not (self.pin or self._registered)

        if self.pin and registration_action is not None:
            self._recreate_authentication()

        # Mode 3/4 command lists are known to be protected. Older players can
        # also protect getRemoteCommandList until first registration (for
        # example the BDP-S780 in mode 1), so detect 401/403 dynamically.
        if self.capabilities.ircc and not self._registration_required:
            try:
                await self._update_commands()
                self._registration_required = False
            except ClientResponseError as ex:
                if (
                    ex.status in (401, 403)
                    and registration_action is not None
                    and not self.pin
                    and not self._registered
                ):
                    self._registration_required = True
                    _LOGGER.debug(
                        "Command list requires registration on %s (mode %s, HTTP %s)",
                        self.host,
                        registration_mode,
                        ex.status,
                    )
                else:
                    raise

        if self.pin and registration_action is not None:
            try:
                await self._update_applist()
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.info(
                    "Cannot retrieve apps list, the device probably don't support it %s",
                    ex,
                )
        self._initialized = True
        return True

    @property
    def initialized(self) -> bool:
        """Return true if initialized."""
        return self._initialized

    @property
    def registration_required(self) -> bool:
        """Return whether the device requires registration before commands are available."""
        return self._registration_required

    # @staticmethod
    # def discover():
    #     """Discover all available devices."""
    #     discovery = ssdp.SSDPDiscovery()
    #     devices = []
    #     for device in discovery.discover(
    #             "urn:schemas-sony-com:service:IRCC:1"
    #     ):
    #         host = device.location.split(":")[1].split("//")[1]
    #         devices.append(SonyDevice(host, device.location))
    #
    #     return devices

    @staticmethod
    async def load_from_json(data):
        """Load a device configuration from a stored json."""
        device = jsonpickle.decode(data)
        await device.init_device()
        return device

    async def save_to_json(self):
        """Save this device configuration into a json."""
        # make sure object is up to date
        await self.init_device()
        return jsonpickle.dumps(self)

    async def _update_service_urls(self) -> bool:
        """Initialize the device by reading the necessary resources from it."""
        try:
            content = await self._send_http(self.dmr_url, method=HttpMethod.GET, raise_errors=True)
        except (aiohttp.ClientError, asyncio.TimeoutError, HTTPError) as exc:
            _LOGGER.error("Failed to get DMR: %s %s", type(exc), exc)
            return False

        if not content:
            return False

        ircc_parsed = await self._parse_dmr(content)
        if self.capabilities.scalar:
            await self._parse_system_information_v4()
        elif not ircc_parsed:
            try:
                await self._parse_ircc()
                await self._parse_action_list()
            # IRCC is optional on DLNA-only devices.
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("IRCC is not available on %s: %s", self.host, ex)

        self._refresh_capabilities()
        if self.capabilities.cers:
            try:
                await self._parse_system_information()
            # System information is optional and must not invalidate detection.
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("Cannot read CERS system information from %s: %s", self.host, ex)

        self._refresh_capabilities()
        return self.capabilities.ircc or self.capabilities.dlna

    def _refresh_capabilities(self) -> None:
        """Update derived capabilities after parsing descriptors or action lists."""
        self.capabilities.ircc = bool(self.control_url or self._ircc_categories)
        self.capabilities.scalar = self.api_version >= 4
        self.capabilities.cers = bool(self.actions) and not self.capabilities.scalar
        self.capabilities.dlna = bool(self.av_transport_url)
        self.capabilities.wol = self.capabilities.wol or bool(self.mac)

    async def _parse_optional_ircc(self) -> bool:
        """Parse the optional legacy IRCC descriptor and action list."""
        try:
            await self._parse_ircc()
            await self._parse_action_list()
            return True
        # pylint: disable=W0718
        except Exception as ex:
            _LOGGER.debug("IRCC is not available on %s: %s", self.host, ex)
            return False

    async def _parse_action_list(self):
        try:
            response = await self._send_http(self.actionlist_url, method=HttpMethod.GET)
            if not response:
                return
        # pylint: disable=W0718
        except (Exception, HTTPError) as ex:
            _LOGGER.debug("Error on %s : %s", self.actionlist_url, ex)
            return

        for element in find_in_xml(response, [("action", True)]):
            action = XmlApiObject(element.attrib)
            _LOGGER.debug("Available action %s : %s", action.name, action.url)
            self.actions[action.name] = action

            if action.mode is None:
                action.mode = self.api_version
            if action.url is None and action.name:
                action.url = urljoin(self.actionlist_url, f"?action={action.name}")
                separator = "&"
            else:
                separator = "?"

            if action.name == "register":
                # the authentication is based on the device id and the mac
                action.url = (
                    f"{action.url}{separator}name={quote(self.nickname)}&registrationType=initial&deviceId="
                    f"{quote(self.client_id)}"
                )
                self.api_version = action.mode
                if action.mode == 3:
                    action.url = action.url + "&wolSupport=true"
                _LOGGER.debug("Registration mode %s : %s", action.mode, action.url)
        self._refresh_capabilities()

    async def _parse_ircc(self):
        content = await self._send_http(self.ircc_url, method=HttpMethod.GET, raise_errors=True)

        upnp_device = f"{URN_UPNP_DEVICE}device"
        # the action list contains everything the device supports
        self.actionlist_url = find_in_xml(
            content,
            [
                upnp_device,
                f"{URN_SONY_AV}X_UNR_DeviceInfo",
                f"{URN_SONY_AV}X_CERS_ActionList_URL",
            ],
        ).text
        services = find_in_xml(
            content,
            [
                upnp_device,
                f"{URN_UPNP_DEVICE}serviceList",
                (f"{URN_UPNP_DEVICE}service", True),
            ],
        )

        lirc_url = urlparse(self.ircc_url)
        for service in services:
            service_id = service.find(f"{URN_UPNP_DEVICE}serviceId")

            if service_id is None or URN_SONY_IRCC not in service_id.text:
                continue

            service_location = service.find(f"{URN_UPNP_DEVICE}controlURL").text

            if service_location.startswith("http://"):
                service_url = ""
            else:
                service_url = lirc_url.scheme + "://" + lirc_url.netloc
            self.control_url = service_url + service_location

        categories = find_in_xml(
            content,
            [
                upnp_device,
                f"{URN_SONY_AV}X_IRCC_DeviceInfo",
                f"{URN_SONY_AV}X_IRCC_CategoryList",
                (f"{URN_SONY_AV}X_IRCC_Category", True),
            ],
        )

        for category in categories:
            category_info = category.find(f"{URN_SONY_AV}X_CategoryInfo")
            if category_info is None:
                continue

            self._ircc_categories.add(category_info.text)
        self._refresh_capabilities()

    async def _parse_system_information_v4(self):
        url = urljoin(self.base_url, "system")
        json_data = self._create_api_json("getSystemSupportedFunction")
        response = await self._send_http(url, HttpMethod.POST, json=json_data)
        if not response:
            _LOGGER.debug("no response received, device might be off")
            return

        json_resp = json.loads(response)
        if json_resp and not json_resp.get("error"):
            for option in json_resp.get("result")[0]:
                if option["option"] == "WOL":
                    self.mac = option["value"]
                    self.capabilities.wol = True

    async def _parse_system_information(self):
        try:
            content = await self._send_http(self._get_action("getSystemInformation").url, method=HttpMethod.GET)
            if not content:
                return
        # pylint: disable=W0718
        except (Exception, HTTPError):
            return
        for element in find_in_xml(content, [("supportFunction", "all"), ("function", True)]):
            for function in element:
                if function.attrib["name"] == "WOL":
                    self.mac = function.find("functionItem").attrib["value"]
                    self.capabilities.wol = True

    async def _parse_dmr(self, data) -> bool:
        # pylint: disable=too-many-locals
        """Parse DMR xml data.

        :return: True if IRCC data is read and actions list is filled in
        """
        lirc_url = urlparse(self.ircc_url)
        xml_data = xml.etree.ElementTree.fromstring(data)

        for element in xml_data.iter():
            name = element.tag.rsplit("}", 1)[-1].casefold()
            if name in {"magicpacketwakesupported", "x_magicpacketwakesupported"}:
                value = (element.text or "").strip().casefold()
                self.capabilities.wol = value in {"1", "true", "yes", "supported"}

        for device in find_in_xml(
            xml_data,
            [
                (f"{URN_UPNP_DEVICE}device", True),
                f"{URN_UPNP_DEVICE}serviceList",
            ],
        ):
            for service in device:
                service_id = service.find(f"{URN_UPNP_DEVICE}serviceId")
                if service_id is None or not service_id.text:
                    continue
                if "urn:upnp-org:serviceId:AVTransport" not in service_id.text:
                    continue
                transport_location = service.find(f"{URN_UPNP_DEVICE}controlURL").text
                # pylint: disable=W1405
                self.av_transport_url = (
                    f"{lirc_url.scheme}://{lirc_url.netloc.split(':')[0]}:{self.dmr_port}{transport_location}"
                )

        self._refresh_capabilities()

        # this is only true for v4 devices except some v3 checks after.
        scalar_api = WEBAPI_SERVICETYPE in data or any(
            element.tag.rsplit("}", 1)[-1] == "X_ScalarWebAPI_DeviceInfo" for element in xml_data.iter()
        )
        if not scalar_api:
            return False

        _LOGGER.debug("Device registration mode 3 or 4, extracting further information...")
        if await self._parse_optional_ircc():
            _LOGGER.debug("Device registration mode is : %s", self.actions["register"].mode)
            return True

        _LOGGER.debug("Device registration mode is 4")
        self.api_version = 4
        device_info_name = f"{URN_SCALAR_WEB_API_DEVICE_INFO}X_ScalarWebAPI_DeviceInfo"

        search_params = [
            (f"{URN_UPNP_DEVICE}device", True),
            (device_info_name, True),
            f"{URN_SCALAR_WEB_API_DEVICE_INFO}X_ScalarWebAPI_BaseURL",
        ]
        for device in find_in_xml(xml_data, search_params):
            for xml_url in device:
                self.base_url = xml_url.text
                if not self.base_url.endswith("/"):
                    self.base_url = f"{self.base_url}/"

                action = XmlApiObject({})
                action.url = urljoin(self.base_url, "accessControl")
                action.mode = 4
                self.actions["register"] = action
                _LOGGER.debug("Registration mode %s : %s", action.mode, action.url)
                action = XmlApiObject({})
                action.url = urljoin(self.base_url, "system")
                action.value = "getRemoteControllerInfo"
                self.actions["getRemoteCommandList"] = action
                self.control_url = urljoin(self.base_url, "IRCC")

        self._refresh_capabilities()
        return True

    async def _update_commands(self):
        """Update the list of commands."""
        if self.api_version == 0:
            self._use_builtin_command_list()
        elif self.api_version <= 3:
            await self._parse_command_list()
        elif self.api_version > 3 and self.pin:
            _LOGGER.debug("Registration necessary to read command list.")
            await self._parse_command_list_v4()

    async def _parse_command_list_v4(self):
        action_name = "getRemoteCommandList"
        action = self.actions[action_name]
        json_data = self._create_api_json(action.value)

        response = await self._send_http(action.url, HttpMethod.POST, json=json_data, headers={})

        if not response:
            _LOGGER.debug("no response received, device might be off")
            return

        json_resp = json.loads(response)
        if json_resp and not json_resp.get("error"):
            for command in json_resp.get("result")[1]:
                api_object = XmlApiObject(command)
                if api_object.name == "PowerOff":
                    api_object.name = "Power"
                self.commands[api_object.name] = api_object
        else:
            _LOGGER.error("JSON request error: %s", json.dumps(json_resp, indent=4))

    async def _parse_command_list(self):
        """Parse the list of available command in devices with the legacy api."""
        action_name = "getRemoteCommandList"
        if action_name not in self.actions:
            _LOGGER.debug("Action list not set in device, try calling init_device")
            return

        action = self.actions[action_name]
        url = action.url
        response = await self._send_http(url, method=HttpMethod.GET)
        if not response:
            _LOGGER.debug("Failed to get response for command list, device might be off")
            return

        for command in find_in_xml(response, [("command", True)]):
            name = command.get("name")
            self.commands[name] = XmlApiObject(command.attrib)

    def _use_builtin_command_list(self):
        for encoded_str in self._ircc_categories:
            fmt, category_id = struct.unpack(">HI", base64.b64decode(encoded_str))
            try:
                category = IrccCategory(category_id)
            except ValueError:
                _LOGGER.warning("Unknown IRCC category identifier: %d", category_id)
                continue

            code_list = IR_KEY_CODES.get(category)
            if code_list is None:
                _LOGGER.warning("No command list available for %s", category)
                continue

            for name, code in code_list:
                value = base64.b64encode(struct.pack(">IIIB", fmt, category_id, code, 3))
                data = XmlApiObject(
                    {
                        "name": name,
                        "type": "ircc",
                        "value": value.decode("ascii"),
                    }
                )
                self.commands[name] = data

    async def _update_applist(self):
        """Update the list of apps which are supported by the device."""
        if self.api_version < 4:
            url = self.app_url + "/appslist"
            response = await self._send_http(url, method=HttpMethod.GET)
        else:
            url = f"http://{self.host}/DIAL/sony/applist"
            response = await self._send_http(
                url,
                method=HttpMethod.GET,
                cookies=self._auth_cookies(),
            )

        if response:
            for app in find_in_xml(response, [(".//app", True)]):
                data = XmlApiObject(
                    {
                        "name": app.find("name").text,
                        "id": app.find("id").text,
                    }
                )
                self.apps[data.name] = data

    def _recreate_authentication(self):
        """Recreate auth authentication."""
        registration_action = self._get_action("register")
        if any([not registration_action, registration_action.mode < 3]):
            return

        self._add_headers()
        username = ""
        base64string = base64.encodebytes(f"{username}:{self.pin}".encode()).decode().replace("\n", "")

        self.headers["Authorization"] = f"Basic {base64string}"
        if registration_action.mode == 4:
            self.headers["Connection"] = "keep-alive"

        if self.psk:
            self.headers["X-Auth-PSK"] = self.psk

    def _create_api_json(self, method, params=None):
        # pylint: disable=invalid-name
        """Create json data which will be send via post for the V4 api."""
        if not params:
            params = [
                {"clientid": self.client_id, "nickname": self.nickname},
                [
                    {
                        "clientid": self.client_id,
                        "nickname": self.nickname,
                        "value": "yes",
                        "function": "WOL",
                    }
                ],
            ]

        return {"method": method, "params": params, "id": 1, "version": "1.0"}

    def _auth_cookies(self) -> dict:
        """Return authentication cookies in the mapping format expected by aiohttp."""
        if self.cookies is None:
            return {}
        auth_cookie = self.cookies.get("auth")
        return {"auth": auth_cookie} if auth_cookie is not None else {}

    async def _send_http(self, url, method, **kwargs) -> str | None:
        # pylint: disable=too-many-arguments
        """Send request command via HTTP json to Sony Bravia."""
        log_errors = kwargs.pop("log_errors", True)
        raise_errors = kwargs.pop("raise_errors", False)
        method = kwargs.pop("method", method.value)
        timeout = kwargs.pop("timeout", TIMEOUT)

        params = {
            "timeout": timeout,
            "headers": self.headers,
        }
        params.update(kwargs)

        _LOGGER.debug("Calling http url %s method %s", url, method)
        if url is None:
            return None

        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(sock_read=60, sock_connect=timeout, connect=timeout, total=60),
                cookies=self._auth_cookies(),
            ) as session:
                response = await getattr(session, method)(url, **params)
                response.raise_for_status()
                return await response.text(encoding="utf-8")
        except aiohttp.ClientConnectorError as ex:
            if log_errors:
                _LOGGER.error("HTTPError: %s", str(ex))
            if raise_errors:
                raise

    async def _post_soap_request(self, url, params, action) -> str | None:
        headers = {"SOAPACTION": f'"{action}"', "Content-Type": "text/xml"}

        data = f"""<?xml version='1.0' encoding='utf-8'?>
                    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
                        SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
                        <SOAP-ENV:Body>
                            {params}
                        </SOAP-ENV:Body>
                    </SOAP-ENV:Envelope>"""
        response = await self._send_http(url, method=HttpMethod.POST, headers=headers, data=data)
        if response:
            return response
        return None

    async def _send_avtransport_action(self, action_name: str, arguments: dict[str, str] | None = None) -> None:
        """Send a transport command through UPnP AVTransport."""
        parameters = ["<InstanceID>0</InstanceID>"]
        for name, value in (arguments or {}).items():
            parameters.append(f"<{name}>{value}</{name}>")
        data = f'<m:{action_name} xmlns:m="{AVTRANSPORT_SERVICE}">{"".join(parameters)}</m:{action_name}>'
        action = f"{AVTRANSPORT_SERVICE}#{action_name}"
        await self._post_soap_request(url=self.av_transport_url, params=data, action=action)

    async def _send_transport_command(
        self,
        ircc_command: str,
        dlna_action: str,
        dlna_arguments: dict[str, str] | None = None,
    ) -> None:
        """Route transport controls to IRCC, or to DLNA when IRCC is absent."""
        if self.capabilities.ircc and ircc_command in self.commands:
            await self.send_command(ircc_command)
            return
        if self.capabilities.dlna:
            await self._send_avtransport_action(dlna_action, dlna_arguments)
            return
        raise ValueError(f"No protocol can send transport command {ircc_command}")

    async def _send_req_ircc(self, params):
        """Send an IRCC command via HTTP to Sony Bravia."""
        data = f"""<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">
                    <IRCCCode>{params}</IRCCCode>
                  </u:X_SendIRCC>"""
        action = "urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"

        content = await self._post_soap_request(url=self.control_url, params=data, action=action)
        return content

    async def send_command(self, name):
        """Send a command."""
        if not self.commands:
            raise ValueError(f"Unknown command: {name}")
            # self.init_device()

        if self.commands:
            if name in self.commands:
                await self._send_req_ircc(self.commands[name].value)
            else:
                raise ValueError(f"Unknown command: {name}")
        else:
            raise ValueError("Failed to read command list from device.")

    def _get_action(self, name):
        """Get the action object for the action with the given name."""
        if name not in self.actions and not self.actions:
            # self.init_device()
            # if name not in self.actions and not self.actions:
            raise ValueError(f"Failed to read action list from device ({name})")

        return self.actions[name]

    async def _register_without_auth(self, registration_action):
        try:
            await self._send_http(registration_action.url, method=HttpMethod.GET, raise_errors=True)
            # set the pin to something to make sure init_device is called
            self.pin = 9999
        # pylint: disable=W0718
        except (Exception, HTTPError) as ex:
            _LOGGER.error("Registration error %s", ex)
            return AuthenticationResult.ERROR
        return AuthenticationResult.SUCCESS

    async def _register_v3(self, registration_action):
        try:
            await self._send_http(registration_action.url, method=HttpMethod.GET, raise_errors=True)
        except ClientResponseError as ex:
            _LOGGER.error("Registration v3 error %s", ex)
            if ex.status == 401:
                return AuthenticationResult.PIN_NEEDED
            return AuthenticationResult.ERROR
        return AuthenticationResult.SUCCESS

    async def _register_v4(self, registration_action):
        authorization = self._create_api_json("actRegister")

        try:
            headers = {"Content-Type": "application/json"}

            if self.pin is None:
                auth_pin = ""
            else:
                auth_pin = str(self.pin)

            async with aiohttp.ClientSession(
                timeout=ClientTimeout(sock_read=60, sock_connect=TIMEOUT, connect=TIMEOUT, total=60),
                raise_for_status=True,
            ) as session:
                response = await session.post(
                    registration_action.url,
                    data=json.dumps(authorization),
                    headers=headers,
                    params={"auth": ("", auth_pin)},
                )

                # response = await self._send_http(registration_action.url,
                #                                  method=HttpMethod.POST,
                #                                  headers=headers,
                #                                  auth=('', auth_pin),
                #                                  data=json.dumps(authorization),
                #                                  raise_errors=True)
                resp = await response.json()
                _LOGGER.debug("Registration v4 %s", resp)
                if not resp or resp.get("error"):
                    return AuthenticationResult.ERROR
                self.cookies = response.cookies
                return AuthenticationResult.SUCCESS
        except ClientResponseError as ex:
            _LOGGER.error("Registration v3 error %s", ex)
            if ex.status == 401:
                return AuthenticationResult.PIN_NEEDED
            return AuthenticationResult.ERROR

    def _add_headers(self):
        """Add headers which all devices need."""
        self.headers["X-CERS-DEVICE-ID"] = self.client_id
        self.headers["X-CERS-DEVICE-INFO"] = self.client_id

    async def register(self):
        """Register at the api.

        The name which will be displayed in the UI of the device.
        Make sure this name does not exist yet.
        For this the device must be put in registration mode.
        """
        registration_action = self._get_action("register")

        if registration_action.mode < 3:
            registration_result = await self._register_without_auth(registration_action)
        elif registration_action.mode == 3:
            registration_result = await self._register_v3(registration_action)
        elif registration_action.mode == 4:
            registration_result = await self._register_v4(registration_action)
        else:
            raise ValueError(f"Registration mode {registration_action.mode} is not supported")

        if registration_result is AuthenticationResult.SUCCESS:
            self._registered = True
            await self.init_device()

        return registration_result

    async def send_authentication(self, pin):
        """Authenticate against the device."""
        registration_action = self._get_action("register")

        # they do not need a pin
        if registration_action.mode < 2:
            return True

        if not pin:
            return False

        self.pin = pin
        self._recreate_authentication()
        result = await self.register()

        return AuthenticationResult.SUCCESS == result

    def _create_magic_packet(self, mac_address: str) -> bytes:
        """Create a magic packet to wake on LAN."""
        addr_byte = mac_address.replace("-", ":").split(":")
        hw_addr = struct.pack(
            "BBBBBB",
            int(addr_byte[0], 16),
            int(addr_byte[1], 16),
            int(addr_byte[2], 16),
            int(addr_byte[3], 16),
            int(addr_byte[4], 16),
            int(addr_byte[5], 16),
        )
        return b"\xff" * 6 + hw_addr * 16

    def wakeonlan(self, broadcast="255.255.255.255") -> None:
        """Send WOL command. to known mac addresses."""
        if not self.mac:
            raise ValueError("A MAC address is required for Wake-on-LAN")
        messages = [self._create_magic_packet(self.mac)]
        broadcast = "<broadcast>" if broadcast is None else broadcast
        socket_instance = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        socket_instance.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for msg in messages:
            socket_instance.sendto(msg, (broadcast, 9))

    @staticmethod
    def _parse_time(value: str | None) -> int | None:
        """Convert CERS seconds or a UPnP time value to integer seconds."""
        if not value or value in {"NOT_IMPLEMENTED", "-", "--:--:--"}:
            return None
        try:
            return max(0, int(float(value)))
        except ValueError:
            pass

        parts = value.split(":")
        if len(parts) != 3:
            return None
        try:
            hours, minutes, seconds = parts
            return max(0, int(hours) * 3600 + int(minutes) * 60 + int(float(seconds)))
        except ValueError:
            return None

    @staticmethod
    def _find_xml_text(root, name: str) -> str | None:
        """Find text by local XML name in responses with varying namespace prefixes."""
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == name and element.text:
                return element.text.strip()
        return None

    @classmethod
    def _parse_cers_playback_info(cls, response: str) -> PlaybackInfo:
        """Normalize the optional fields returned by the CERS getStatus action."""
        root = xml.etree.ElementTree.fromstring(response)
        viewing_items: dict[str, str] | None = None
        for status in root.iter():
            # Legacy Sony players such as the UBP-X700 use the presence of an
            # element named "viewing" as their playback signal. Do not require
            # a particular XML tag name: firmware variants wrap this element
            # differently, while the historical integration only relied on
            # the name attribute.
            if status.attrib.get("name", "").casefold() != "viewing":
                continue
            items: dict[str, str] = {}
            for item in status.iter():
                field = item.attrib.get("field")
                value = item.attrib.get("value")
                if field and value is not None:
                    items[field.casefold()] = value
            viewing_items = items
            break

        if viewing_items is None:
            return PlaybackInfo(state=DeviceState.STOPPED)

        speed_value = viewing_items.get("speed")
        try:
            speed = float(speed_value) if speed_value is not None else None
        except ValueError:
            speed = None

        state_value = viewing_items.get("state", "").casefold()
        if state_value in {"paused", "pause", "paused_playback"} or speed == 0:
            state = DeviceState.PAUSED
        elif state_value in {"stopped", "stop", "no_media_present"}:
            state = DeviceState.STOPPED
        else:
            state = DeviceState.PLAYING

        duration = next(
            (
                cls._parse_time(viewing_items.get(field))
                for field in ("duration", "totaltime", "reproductiontime")
                if viewing_items.get(field) is not None
            ),
            None,
        )
        position = next(
            (
                cls._parse_time(viewing_items.get(field))
                for field in ("position", "currentposition", "elapsedtime", "reproductionpoint")
                if viewing_items.get(field) is not None
            ),
            None,
        )
        return PlaybackInfo(
            state=state,
            position=position,
            duration=duration,
            source=viewing_items.get("source"),
            title=viewing_items.get("title"),
            speed=speed,
        )

    @staticmethod
    def _parse_cers_content_info(response: str) -> dict[str, str]:
        """Extract optional content metadata fields from CERS getContentInformation."""
        root = xml.etree.ElementTree.fromstring(response)
        fields: dict[str, str] = {}
        for element in root.iter():
            field = element.attrib.get("field")
            value = element.attrib.get("value")
            if field and value is not None:
                fields[field.casefold()] = value
        return fields

    @classmethod
    def _parse_dlna_playback_info(cls, transport_response: str | None, position_response: str | None) -> PlaybackInfo:
        """Normalize AVTransport GetTransportInfo and GetPositionInfo responses."""
        transport_state = None
        speed = None
        if transport_response:
            transport_root = xml.etree.ElementTree.fromstring(transport_response)
            transport_state = cls._find_xml_text(transport_root, "CurrentTransportState")
            speed_value = cls._find_xml_text(transport_root, "CurrentSpeed")
            try:
                speed = float(speed_value) if speed_value is not None else None
            except ValueError:
                speed = None

        states = {
            "PLAYING": DeviceState.PLAYING,
            "PAUSED_PLAYBACK": DeviceState.PAUSED,
            "PAUSED_RECORDING": DeviceState.PAUSED,
            "STOPPED": DeviceState.STOPPED,
            "NO_MEDIA_PRESENT": DeviceState.STOPPED,
        }
        state = states.get(transport_state or "", DeviceState.STOPPED)

        position = None
        duration = None
        if position_response:
            position_root = xml.etree.ElementTree.fromstring(position_response)
            position = cls._parse_time(cls._find_xml_text(position_root, "RelTime"))
            duration = cls._parse_time(cls._find_xml_text(position_root, "TrackDuration"))

        return PlaybackInfo(state=state, position=position, duration=duration, speed=speed)

    async def _get_avtransport_action(self, action_name: str) -> str | None:
        """Execute a read-only AVTransport action for instance zero."""
        data = f'<m:{action_name} xmlns:m="{AVTRANSPORT_SERVICE}"><InstanceID>0</InstanceID></m:{action_name}>'
        action = f"{AVTRANSPORT_SERVICE}#{action_name}"
        return await self._post_soap_request(url=self.av_transport_url, params=data, action=action)

    async def _get_dlna_playback_info(self) -> PlaybackInfo:
        """Read playback state, duration and position through AVTransport."""
        transport_response = await self._get_avtransport_action("GetTransportInfo")
        position_response = await self._get_avtransport_action("GetPositionInfo")
        return self._parse_dlna_playback_info(transport_response, position_response)

    async def get_playback_info(self) -> PlaybackInfo:
        """Return normalized playback information using CERS first, then DLNA."""
        cers_info = None
        if self.capabilities.cers and "getStatus" in self.actions:
            response = await self._send_http(self._get_action("getStatus").url, method=HttpMethod.GET)
            if not response:
                return PlaybackInfo(state=DeviceState.OFF)
            cers_info = self._parse_cers_playback_info(response)

        if (
            cers_info
            and "getContentInformation" in self.actions
            and (cers_info.source is None or cers_info.title is None)
        ):
            try:
                content_response = await self._send_http(
                    self._get_action("getContentInformation").url,
                    method=HttpMethod.GET,
                )
                if content_response:
                    content_fields = self._parse_cers_content_info(content_response)
                    cers_info.source = cers_info.source or content_fields.get("source")
                    cers_info.title = cers_info.title or content_fields.get("title")
            # Content metadata is optional and must never break state polling.
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("Cannot read CERS content information from %s: %s", self.host, ex)

        dlna_info = None
        if self.capabilities.dlna:
            try:
                dlna_info = await self._get_dlna_playback_info()
            # Some players only expose AVTransport for network media.
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("Cannot read AVTransport state from %s: %s", self.host, ex)

        if cers_info:
            # AVTransport on Sony players can describe only the DLNA renderer,
            # not the physical Blu-ray transport. A STOPPED 0/0 DLNA response
            # must therefore not overwrite missing CERS timing for a disc.
            if dlna_info and dlna_info.state in {DeviceState.PLAYING, DeviceState.PAUSED}:
                cers_info.position = cers_info.position if cers_info.position is not None else dlna_info.position
                cers_info.duration = cers_info.duration if cers_info.duration is not None else dlna_info.duration
            return cers_info
        if dlna_info:
            return dlna_info
        return PlaybackInfo(state=DeviceState.STOPPED)

    async def get_status(self) -> DeviceState:
        """Return the normalized playback state of the device."""
        return (await self.get_playback_info()).state

    async def get_playing_status(self):
        """Get the legacy string representation of the playback state."""
        return (await self.get_playback_info()).state.name

    async def get_power_status(self, timeout=TIMEOUT):
        """Check if the device is online."""
        if self.api_version < 4:
            url = self.actionlist_url or self.dmr_url
            try:
                await self._send_http(
                    url,
                    HttpMethod.GET,
                    log_errors=False,
                    raise_errors=True,
                    timeout=timeout,
                )
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug(ex)
                return False
            return True
        try:
            resp = await self._send_http(
                urljoin(self.base_url, "system"),
                HttpMethod.POST,
                json=self._create_api_json("getPowerStatus"),
                timeout=timeout,
            )
            if not resp:
                return False
            json_data = json.loads(resp)
            if not json_data.get("error"):
                power_data = json_data.get("result")[0]
                return power_data.get("status") != "off"
        # pylint: disable=W0718
        except Exception:
            pass
        return False

    async def start_app(self, app_name):
        """Start an app by name."""
        # sometimes device does not start app if already running one
        await self.home()

        if self.api_version < 4:
            url = f"{self.app_url}/apps/{self.apps[app_name].id}"
            data = f"LOCATION: {url}/run"
            await self._send_http(url, HttpMethod.POST, data=data)
        else:
            url = f"http://{self.host}/DIAL/apps/{self.apps[app_name].id}"
            await self._send_http(url, HttpMethod.POST, cookies=self._auth_cookies())

    async def power(self, power_on, broadcast="255.255.255.255"):
        """Powers the device on or shuts it off."""
        if power_on:
            if self.mac:
                _LOGGER.debug("Wake on LAN")
                self.wakeonlan(broadcast)
            elif not self.capabilities.ircc:
                raise ValueError("This device has no available power-on protocol")

            if self.capabilities.ircc and self.initialized and not await self.get_power_status(timeout=2):
                # Try using the power on command incase the WOL doesn't work
                _LOGGER.debug("Sends power command asynchronously")
                self._event_loop.create_task(self.send_command("Power"))
        else:
            if not self.capabilities.ircc:
                raise ValueError("Power off requires IRCC")
            await self.send_command("Power")

    def get_apps(self):
        """Get the apps from the stored dict."""
        return list(self.apps.keys())

    async def volume_up(self):
        # pylint: disable=invalid-name
        """Send the command 'VolumeUp' to the connected device."""
        await self.send_command("VolumeUp")

    async def volume_down(self):
        # pylint: disable=invalid-name
        """Send the command 'VolumeDown' to the connected device."""
        await self.send_command("VolumeDown")

    async def mute(self):
        # pylint: disable=invalid-name
        """Send the command 'Mute' to the connected device."""
        await self.send_command("Mute")

    async def up(self):
        # pylint: disable=invalid-name
        """Send the command 'up' to the connected device."""
        await self.send_command("Up")

    async def confirm(self):
        """Send the command 'confirm' to the connected device."""
        await self.send_command("Confirm")

    async def down(self):
        """Send the command 'down' to the connected device."""
        await self.send_command("Down")

    async def right(self):
        """Send the command 'right' to the connected device."""
        await self.send_command("Right")

    async def left(self):
        """Send the command 'left' to the connected device."""
        await self.send_command("Left")

    async def home(self):
        """Send the command 'home' to the connected device."""
        await self.send_command("Home")

    async def options(self):
        """Send the command 'options' to the connected device."""
        await self.send_command("Options")

    async def returns(self):
        """Send the command 'returns' to the connected device."""
        await self.send_command("Return")

    async def num1(self):
        """Send the command 'num1' to the connected device."""
        await self.send_command("Num1")

    async def num2(self):
        """Send the command 'num2' to the connected device."""
        await self.send_command("Num2")

    async def num3(self):
        """Send the command 'num3' to the connected device."""
        await self.send_command("Num3")

    async def num4(self):
        """Send the command 'num4' to the connected device."""
        await self.send_command("Num4")

    async def num5(self):
        """Send the command 'num5' to the connected device."""
        await self.send_command("Num5")

    async def num6(self):
        """Send the command 'num6' to the connected device."""
        await self.send_command("Num6")

    async def num7(self):
        """Send the command 'num7' to the connected device."""
        await self.send_command("Num7")

    async def num8(self):
        """Send the command 'num8' to the connected device."""
        await self.send_command("Num8")

    async def num9(self):
        """Send the command 'num9' to the connected device."""
        await self.send_command("Num9")

    async def num0(self):
        """Send the command 'num0' to the connected device."""
        await self.send_command("Num0")

    async def display(self):
        """Send the command 'display' to the connected device."""
        await self.send_command("Display")

    async def audio(self):
        """Send the command 'audio' to the connected device."""
        await self.send_command("Audio")

    async def sub_title(self):
        """Send the command 'subTitle' to the connected device."""
        await self.send_command("SubTitle")

    async def favorites(self):
        """Send the command 'favorites' to the connected device."""
        await self.send_command("Favorites")

    async def yellow(self):
        """Send the command 'yellow' to the connected device."""
        await self.send_command("Yellow")

    async def blue(self):
        """Send the command 'blue' to the connected device."""
        await self.send_command("Blue")

    async def red(self):
        """Send the command 'red' to the connected device."""
        await self.send_command("Red")

    async def green(self):
        """Send the command 'green' to the connected device."""
        await self.send_command("Green")

    async def play(self):
        """Start playback through IRCC or AVTransport."""
        await self._send_transport_command("Play", "Play", {"Speed": "1"})

    async def stop(self):
        """Stop playback through IRCC or AVTransport."""
        await self._send_transport_command("Stop", "Stop")

    async def pause(self):
        """Pause playback through IRCC or AVTransport."""
        await self._send_transport_command("Pause", "Pause")

    async def rewind(self):
        """Send the command 'rewind' to the connected device."""
        await self.send_command("Rewind")

    async def forward(self):
        """Send the command 'forward' to the connected device."""
        await self.send_command("Forward")

    async def prev(self):
        """Select the previous item through IRCC or AVTransport."""
        await self._send_transport_command("Prev", "Previous")

    async def next(self):
        """Select the next item through IRCC or AVTransport."""
        await self._send_transport_command("Next", "Next")

    async def seek(self, position: int):
        """Seek DLNA playback to an absolute position in seconds."""
        if not self.capabilities.dlna:
            raise ValueError("DLNA AVTransport is required for seek")
        hours, remainder = divmod(max(0, int(position)), 3600)
        minutes, seconds = divmod(remainder, 60)
        await self._send_avtransport_action(
            "Seek",
            {
                "Unit": "REL_TIME",
                "Target": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            },
        )

    async def replay(self):
        """Send the command 'replay' to the connected device."""
        await self.send_command("Replay")

    async def advance(self):
        """Send the command 'advance' to the connected device."""
        await self.send_command("Advance")

    async def angle(self):
        """Send the command 'angle' to the connected device."""
        await self.send_command("Angle")

    async def top_menu(self):
        """Send the command 'top_menu' to the connected device."""
        await self.send_command("TopMenu")

    async def pop_up_menu(self):
        """Send the command 'pop_up_menu' to the connected device."""
        await self.send_command("PopUpMenu")

    async def eject(self):
        """Send the command 'eject' to the connected device."""
        await self.send_command("Eject")

    async def karaoke(self):
        """Send the command 'karaoke' to the connected device."""
        await self.send_command("Karaoke")

    async def netflix(self):
        """Send the command 'netflix' to the connected device."""
        await self.send_command("Netflix")

    async def mode_3d(self):
        """Send the command 'mode_3d' to the connected device."""
        await self.send_command("Mode3D")

    async def zoom_in(self):
        """Send the command 'zoom_in' to the connected device."""
        await self.send_command("ZoomIn")

    async def zoom_out(self):
        """Send the command 'zoom_out' to the connected device."""
        await self.send_command("ZoomOut")

    async def browser_back(self):
        """Send the command 'browser_back' to the connected device."""
        await self.send_command("BrowserBack")

    async def browser_forward(self):
        """Send the command 'browser_forward' to the connected device."""
        await self.send_command("BrowserForward")

    async def browser_bookmark_list(self):
        """Send the command 'browser_bookmarkList' to the connected device."""
        await self.send_command("BrowserBookmarkList")

    async def list(self):
        """Send the command 'list' to the connected device."""
        await self.send_command("List")


def xml_search_helper(data, param):
    """Perform find or findall on given xml with string from param."""
    if isinstance(param, (tuple, list)) and param[1]:
        result = data.findall(param[0])
    else:
        result = data.find(param)
    return result


def iterate_search_data(data, param):
    """Search in nested lists."""
    result = []
    for element in data:
        if isinstance(element, list):
            result.append(iterate_search_data(element, param))
        else:
            result.append(xml_search_helper(element, param))
    return result


def find_in_xml(data, search_params):
    """Try to find an element in an xml.

    Take an xml from string or as xml.etree.ElementTree
    and an iterable of strings (and/or tuples in case of findall) to search.
    The tuple should contain the string to search for and a true value.
    """
    if isinstance(data, str):
        data = xml.etree.ElementTree.fromstring(data)
    param = search_params[0]
    if isinstance(data, list):
        result = iterate_search_data(data, param)
    else:
        result = xml_search_helper(data, param)

    if len(search_params) == 1:
        return result
    return find_in_xml(result, search_params[1:])
