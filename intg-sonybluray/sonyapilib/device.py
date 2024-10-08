"""Sony Media player lib"""
import asyncio
import base64
import json
import logging
import socket
import struct
import aiohttp
import xml.etree.ElementTree
from enum import Enum
from urllib.parse import (
    urljoin,
    urlparse,
    quote,
)

import jsonpickle
from aiohttp import ClientTimeout, ClientResponseError
from aiohttp.web_exceptions import HTTPError

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 5
URN_UPNP_DEVICE = "{urn:schemas-upnp-org:device-1-0}"
URN_SONY_AV = "{urn:schemas-sony-com:av}"
URN_SONY_IRCC = "urn:schemas-sony-com:serviceId:IRCC"
URN_SCALAR_WEB_API_DEVICE_INFO = "{urn:schemas-sony-com:av}"
WEBAPI_SERVICETYPE = "av:X_ScalarWebAPI_ServiceType"


class DeviceState(Enum):
    OFF = 0
    STOPPED = 1
    PLAYING = 2


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
        ('Num1', 0),
        ('Num2', 1),
        ('Num3', 2),
        ('Num4', 3),
        ('Num5', 4),
        ('Num6', 5),
        ('Num7', 6),
        ('Num8', 7),
        ('Num9', 8),
        ('Num0', 9),
        ('Power', 21),
        ('Eject', 22),
        ('Stop', 24),
        ('Pause', 25),
        ('Play', 26),
        ('Rewind', 27),
        ('Forward', 28),
        ('PopUpMenu', 41),
        ('TopMenu', 44),
        ('Up', 57),
        ('Down', 58),
        ('Left', 59),
        ('Right', 60),
        ('Confirm', 61),
        ('Options', 63),
        ('Display', 65),
        ('Home', 66),
        ('Return', 67),
        ('Karaoke', 74),
        ('Netflix', 75),
        ('Mode3D', 77),
        ('Next', 86),
        ('Prev', 87),
        ('Favorites', 94),
        ('SubTitle', 99),
        ('Audio', 100),
        ('Angle', 101),
        ('Blue', 102),
        ('Red', 103),
        ('Green', 104),
        ('Yellow', 105),
        ('Advance', 117),
        ('Replay', 118),
    )
}


class XmlApiObject:
    # pylint: disable=too-few-public-methods
    """Holds data for a device action or a command."""

    def __init__(self, xml_data):
        """Init xml object with given data"""
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

    def __init__(self, host, nickname, psk=None,
                 app_port=50202, dmr_port=52323, ircc_port=50001):
        # pylint: disable=too-many-arguments
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
        self.actions = {}
        self.headers = {}
        # commands are alike to buttons on the remote
        self.commands = {}
        self.apps = {}

        self.pin = None
        self.cookies = None
        self.mac: str | None = None
        self.api_version = 0

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

    async def init_device(self):
        """Update this object with data from the device"""
        if not await self._update_service_urls():
            return
        await self._update_commands()
        self._add_headers()

        if self.pin:
            self._recreate_authentication()
            await self._update_applist()

    @property
    def initialized(self) -> bool:
        return self.api_version != 0

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
        except aiohttp.ClientConnectorError:
            return False
        except HTTPError as exc:
            _LOGGER.error("Failed to get DMR: %s", type(exc), exc)
            return False

        try:
            if content:
                self._parse_dmr(content)
            if self.api_version <= 3:
                await self._parse_ircc()
                await self._parse_action_list()
                if self.api_version > 0:
                    await self._parse_system_information()
            else:
                await self._parse_system_information_v4()
            return True
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.exception("failed to get device information", ex)
            return False

    async def _parse_action_list(self):
        try:
            response = await self._send_http(self.actionlist_url, method=HttpMethod.GET)
            if not response:
                return
        except (Exception, HTTPError) as ex:
            _LOGGER.debug("Error on %s", self.actionlist_url, ex)
            return

        for element in find_in_xml(response, [("action", True)]):
            action = XmlApiObject(element.attrib)
            _LOGGER.debug("Available action %s : %s", action.name, action.url)
            self.actions[action.name] = action

            if action.mode is None:
                action.mode = self.api_version
            if action.url is None and action.name:
                action.url = urljoin(self.actionlist_url, "?action={}".format(action.name))
                separator = "&"
            else:
                separator = "?"

            if action.name == "register":
                # the authentication is based on the device id and the mac
                action.url = \
                    f"{action.url}{separator}name={quote(self.nickname)}&registrationType=initial&deviceId={quote(self.client_id)}"
                self.api_version = action.mode
                if action.mode == 3:
                    action.url = action.url + "&wolSupport=true"

    async def _parse_ircc(self):
        content = await self._send_http(
            self.ircc_url, method=HttpMethod.GET, raise_errors=True)

        upnp_device = "{}device".format(URN_UPNP_DEVICE)
        # the action list contains everything the device supports
        self.actionlist_url = find_in_xml(
            content,
            [upnp_device,
             "{}X_UNR_DeviceInfo".format(URN_SONY_AV),
             "{}X_CERS_ActionList_URL".format(URN_SONY_AV)]
        ).text
        services = find_in_xml(
            content,
            [upnp_device,
             "{}serviceList".format(URN_UPNP_DEVICE),
             ("{}service".format(URN_UPNP_DEVICE), True)],
        )

        lirc_url = urlparse(self.ircc_url)
        for service in services:
            service_id = service.find(
                "{0}serviceId".format(URN_UPNP_DEVICE))

            if service_id is None or \
                    URN_SONY_IRCC not in service_id.text:
                continue

            service_location = service.find(
                "{0}controlURL".format(URN_UPNP_DEVICE)).text

            if service_location.startswith('http://'):
                service_url = ''
            else:
                service_url = lirc_url.scheme + "://" + lirc_url.netloc
            self.control_url = service_url + service_location

        categories = find_in_xml(
            content,
            [upnp_device,
             "{}X_IRCC_DeviceInfo".format(URN_SONY_AV),
             "{}X_IRCC_CategoryList".format(URN_SONY_AV),
             ("{}X_IRCC_Category".format(URN_SONY_AV), True)]
        )

        for category in categories:
            category_info = category.find(
                "{}X_CategoryInfo".format(URN_SONY_AV))
            if category_info is None:
                continue

            self._ircc_categories.add(category_info.text)

    async def _parse_system_information_v4(self):
        url = urljoin(self.base_url, "system")
        json_data = self._create_api_json("getSystemSupportedFunction")
        response = await self._send_http(url, HttpMethod.POST, json=json_data)
        if not response:
            _LOGGER.debug("no response received, device might be off")
            return

        json_resp = json.loads(response)
        if json_resp and not json_resp.get('error'):
            for option in json_resp.get('result')[0]:
                if option['option'] == 'WOL':
                    self.mac = option['value']

    async def _parse_system_information(self):
        try:
            content = await self._send_http(
                self._get_action(
                    "getSystemInformation").url, method=HttpMethod.GET)
            if not content:
                return
        except (Exception, HTTPError):
            return
        for element in find_in_xml(
                content, [("supportFunction", "all"), ("function", True)]
        ):
            for function in element:
                if function.attrib["name"] == "WOL":
                    self.mac = function.find(
                        "functionItem").attrib["value"]

    def _parse_dmr(self, data):
        lirc_url = urlparse(self.ircc_url)
        xml_data = xml.etree.ElementTree.fromstring(data)

        for device in find_in_xml(xml_data, [
            ("{0}device".format(URN_UPNP_DEVICE), True),
            "{0}serviceList".format(URN_UPNP_DEVICE)
        ]):
            for service in device:
                service_id = service.find(
                    "{0}serviceId".format(URN_UPNP_DEVICE))
                if "urn:upnp-org:serviceId:AVTransport" not in service_id.text:
                    continue
                transport_location = service.find(
                    "{0}controlURL".format(URN_UPNP_DEVICE)).text
                self.av_transport_url = "{0}://{1}:{2}{3}".format(
                    lirc_url.scheme, lirc_url.netloc.split(":")[0],
                    self.dmr_port, transport_location
                )

        # this is only true for v4 devices.
        if WEBAPI_SERVICETYPE not in data:
            return

        self.api_version = 4
        device_info_name = "{0}X_ScalarWebAPI_DeviceInfo".format(
            URN_SCALAR_WEB_API_DEVICE_INFO
        )

        search_params = [
            ("{0}device".format(URN_UPNP_DEVICE), True),
            (device_info_name, True),
            "{0}X_ScalarWebAPI_BaseURL".format(URN_SCALAR_WEB_API_DEVICE_INFO),
        ]
        for device in find_in_xml(xml_data, search_params):
            for xml_url in device:
                self.base_url = xml_url.text
                if not self.base_url.endswith("/"):
                    self.base_url = "{}/".format(self.base_url)

                action = XmlApiObject({})
                action.url = urljoin(self.base_url, "accessControl")
                action.mode = 4
                self.actions["register"] = action

                action = XmlApiObject({})
                action.url = urljoin(self.base_url, "system")
                action.value = "getRemoteControllerInfo"
                self.actions["getRemoteCommandList"] = action
                self.control_url = urljoin(self.base_url, "IRCC")

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

        response = await self._send_http(
            action.url, HttpMethod.POST, json=json_data, headers={}
        )

        if not response:
            _LOGGER.debug("no response received, device might be off")
            return

        json_resp = json.loads(response)
        if json_resp and not json_resp.get('error'):
            for command in json_resp.get('result')[1]:
                api_object = XmlApiObject(command)
                if api_object.name == "PowerOff":
                    api_object.name = "Power"
                self.commands[api_object.name] = api_object
        else:
            _LOGGER.error("JSON request error: %s",
                          json.dumps(json_resp, indent=4))

    async def _parse_command_list(self):
        """Parse the list of available command in devices with the legacy api."""
        action_name = "getRemoteCommandList"
        if action_name not in self.actions:
            _LOGGER.debug(
                "Action list not set in device, try calling init_device")
            return

        action = self.actions[action_name]
        url = action.url
        response = await self._send_http(url, method=HttpMethod.GET)
        if not response:
            _LOGGER.debug(
                "Failed to get response for command list, device might be off")
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
                data = XmlApiObject({
                    "name": name,
                    "type": "ircc",
                    "value": value.decode("ascii"),
                })
                self.commands[name] = data

    async def _update_applist(self):
        """Update the list of apps which are supported by the device."""
        if self.api_version < 4:
            url = self.app_url + "/appslist"
            response = await self._send_http(url, method=HttpMethod.GET)
        else:
            url = 'http://{}/DIAL/sony/applist'.format(self.host)
            response = await self._send_http(
                url,
                method=HttpMethod.GET,
                cookies={"auth", self.cookies.get("auth", None)})

        if response:
            for app in find_in_xml(response, [(".//app", True)]):
                data = XmlApiObject({
                    "name": app.find("name").text,
                    "id": app.find("id").text,
                })
                self.apps[data.name] = data

    def _recreate_authentication(self):
        """Recreate auth authentication"""
        registration_action = self._get_action("register")
        if any([not registration_action, registration_action.mode < 3]):
            return

        self._add_headers()
        username = ''
        base64string = base64.encodebytes(
            ('%s:%s' % (username, self.pin)).encode()).decode().replace('\n', '')

        self.headers['Authorization'] = "Basic %s" % base64string
        if registration_action.mode == 4:
            self.headers['Connection'] = "keep-alive"

        if self.psk:
            self.headers['X-Auth-PSK'] = self.psk

    def _create_api_json(self, method, params=None):
        # pylint: disable=invalid-name
        """Create json data which will be send via post for the V4 api"""
        if not params:
            params = [{
                "clientid": self.client_id,
                "nickname": self.nickname
            }, [{
                "clientid": self.client_id,
                "nickname": self.nickname,
                "value": "yes",
                "function": "WOL"
            }]]

        return {
            "method": method,
            "params": params,
            "id": 1,
            "version": "1.0"
        }

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

        _LOGGER.debug(
            "Calling http url %s method %s", url, method)
        if url is None:
            return None

        try:
            cookies = {} if self.cookies is None else {"auth", self.cookies.get("auth", None)}
            async with aiohttp.ClientSession(timeout=ClientTimeout(sock_read=60, sock_connect=timeout,
                                                                   connect=timeout, total=60),
                                             cookies=cookies) as session:
                response = await getattr(session, method)(url, **params)
                response.raise_for_status()
                return await response.text(encoding="utf-8")
        except aiohttp.ClientConnectorError as ex:
            if log_errors:
                _LOGGER.error("HTTPError: %s", str(ex))
            if raise_errors:
                raise

    async def _post_soap_request(self, url, params, action) -> str | None:
        headers = {
            'SOAPACTION': '"{0}"'.format(action),
            "Content-Type": "text/xml"
        }

        data = """<?xml version='1.0' encoding='utf-8'?>
                    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
                        SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
                        <SOAP-ENV:Body>
                            {0}
                        </SOAP-ENV:Body>
                    </SOAP-ENV:Envelope>""".format(params)
        response = await self._send_http(
            url, method=HttpMethod.POST, headers=headers, data=data)
        if response:
            return response
        return None

    async def _send_req_ircc(self, params):
        """Send an IRCC command via HTTP to Sony Bravia."""
        data = """<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">
                    <IRCCCode>{0}</IRCCCode>
                  </u:X_SendIRCC>""".format(params)
        action = "urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"

        content = await self._post_soap_request(
            url=self.control_url, params=data, action=action)
        return content

    async def _send_command(self, name):
        if not self.commands:
            raise ValueError('Unknown command: %s' % name)
            # self.init_device()

        if self.commands:
            if name in self.commands:
                await self._send_req_ircc(self.commands[name].value)
            else:
                raise ValueError('Unknown command: %s' % name)
        else:
            raise ValueError('Failed to read command list from device.')

    def _get_action(self, name):
        """Get the action object for the action with the given name"""
        if name not in self.actions and not self.actions:
            # self.init_device()
            # if name not in self.actions and not self.actions:
            raise ValueError('Failed to read action list from device.')

        return self.actions[name]

    async def _register_without_auth(self, registration_action):
        try:
            await self._send_http(
                registration_action.url,
                method=HttpMethod.GET,
                raise_errors=True)
            # set the pin to something to make sure init_device is called
            self.pin = 9999
        except (Exception, HTTPError) as ex:
            _LOGGER.error("Registration error", ex)
            return AuthenticationResult.ERROR
        else:
            return AuthenticationResult.SUCCESS

    async def _register_v3(self, registration_action):
        try:
            await self._send_http(registration_action.url,
                                  method=HttpMethod.GET, raise_errors=True)
        except ClientResponseError as ex:
            _LOGGER.error("Registration v3 error", ex)
            if ex.status == 401:
                return AuthenticationResult.PIN_NEEDED
            return AuthenticationResult.ERROR
        else:
            return AuthenticationResult.SUCCESS

    async def _register_v4(self, registration_action):
        authorization = self._create_api_json("actRegister")

        try:
            headers = {
                "Content-Type": "application/json"
            }

            if self.pin is None:
                auth_pin = ''
            else:
                auth_pin = str(self.pin)

            async with aiohttp.ClientSession(timeout=ClientTimeout(sock_read=60, sock_connect=TIMEOUT,
                                                                   connect=TIMEOUT, total=60),
                                             raise_for_status=True) as session:
                response = await session.post(registration_action.url,
                                              data=json.dumps(authorization),
                                              headers=headers,
                                              params={'auth': ('', auth_pin)})

                # response = await self._send_http(registration_action.url,
                #                                  method=HttpMethod.POST,
                #                                  headers=headers,
                #                                  auth=('', auth_pin),
                #                                  data=json.dumps(authorization),
                #                                  raise_errors=True)
                resp = await response.json()
                _LOGGER.debug("Registration v4 %s", resp)
                if not resp or resp.get('error'):
                    return AuthenticationResult.ERROR
                self.cookies = response.cookies
                return AuthenticationResult.SUCCESS
        except ClientResponseError as ex:
            _LOGGER.error("Registration v3 error", ex)
            if ex.status == 401:
                return AuthenticationResult.PIN_NEEDED
            return AuthenticationResult.ERROR

    def _add_headers(self):
        """Add headers which all devices need"""
        self.headers['X-CERS-DEVICE-ID'] = self.client_id
        self.headers['X-CERS-DEVICE-INFO'] = self.client_id

    async def register(self):
        """Register at the api.

        The name which will be displayed in the UI of the device.
        Make sure this name does not exist yet.
        For this the device must be put in registration mode.
        """
        registration_result = AuthenticationResult.ERROR
        registration_action = registration_action = self._get_action(
            "register")

        if registration_action.mode < 3:
            registration_result = await self._register_without_auth(
                registration_action)
        elif registration_action.mode == 3:
            registration_result = await self._register_v3(registration_action)
        elif registration_action.mode == 4:
            registration_result = await self._register_v4(registration_action)
        else:
            raise ValueError(
                "Registration mode {0} is not supported"
                .format(registration_action.mode))

        if registration_result is AuthenticationResult.SUCCESS:
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

    def wakeonlan(self, broadcast='255.255.255.255') -> None:
        """Send WOL command. to known mac addresses."""
        messages = [self._create_magic_packet(self.mac)]
        broadcast = "<broadcast>" if broadcast is None else broadcast
        socket_instance = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        socket_instance.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for msg in messages:
            socket_instance.sendto(msg, (broadcast, 9))

    async def get_status(self) -> DeviceState:
        response = await self._send_http(
            self._get_action(
                "getStatus").url, method=HttpMethod.GET)
        if not response:
            return DeviceState.OFF
        for element in find_in_xml(
                response, [("status", True)]
        ):
            if element.attrib["name"] == "viewing":
                return DeviceState.PLAYING
        return DeviceState.STOPPED

    async def get_playing_status(self):
        """Get the status of playback from the device"""
        data = """<m:GetTransportInfo xmlns:m="urn:schemas-upnp-org:service:AVTransport:1">
            <InstanceID>0</InstanceID>
            </m:GetTransportInfo>"""

        action = "urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"

        content = await self._post_soap_request(
            url=self.av_transport_url, params=data, action=action)
        if not content:
            return "OFF"

        return find_in_xml(content, [".//CurrentTransportState"]).text

    async def get_power_status(self, timeout=TIMEOUT):
        """Check if the device is online."""
        if self.api_version < 4:
            url = self.actionlist_url
            try:
                await self._send_http(url, HttpMethod.GET,
                                      log_errors=False, raise_errors=True, timeout=timeout)
            except Exception as ex:
                _LOGGER.debug(ex)
                return False
            return True
        try:
            resp = await self._send_http(urljoin(self.base_url, "system"),
                                         HttpMethod.POST,
                                         json=self._create_api_json(
                                             "getPowerStatus"),
                                         timeout=timeout)
            if not resp:
                return False
            json_data = json.loads(resp)
            if not json_data.get('error'):
                power_data = json_data.get('result')[0]
                return power_data.get('status') != "off"
        except Exception:
            pass
        return False

    async def start_app(self, app_name):
        """Start an app by name"""
        # sometimes device does not start app if already running one
        await self.home()

        if self.api_version < 4:
            url = "{0}/apps/{1}".format(self.app_url, self.apps[app_name].id)
            data = "LOCATION: {0}/run".format(url)
            await self._send_http(url, HttpMethod.POST, data=data)
        else:
            url = 'http://{}/DIAL/apps/{}'.format(
                self.host, self.apps[app_name].id)
            await self._send_http(url, HttpMethod.POST,
                                  cookies={"auth", self.cookies.get("auth")})

    async def power(self, power_on, broadcast='255.255.255.255'):
        """Powers the device on or shuts it off."""
        if power_on:
            _LOGGER.debug("Wake on lan")
            self.wakeonlan(broadcast)
            # Try using the power on command incase the WOL doesn't work
            if self.initialized and not await self.get_power_status(timeout=2):
                # Try using the power on command incase the WOL doesn't work
                _LOGGER.debug("Sends power command asynchronously")
                self._event_loop.create_task(self._send_command('Power'))
        else:
            await self._send_command('Power')

    def get_apps(self):
        """Get the apps from the stored dict."""
        return list(self.apps.keys())

    async def volume_up(self):
        # pylint: disable=invalid-name
        """Send the command 'VolumeUp' to the connected device."""
        await self._send_command('VolumeUp')

    async def volume_down(self):
        # pylint: disable=invalid-name
        """Send the command 'VolumeDown' to the connected device."""
        await self._send_command('VolumeDown')

    async def mute(self):
        # pylint: disable=invalid-name
        """Send the command 'Mute' to the connected device."""
        await self._send_command('Mute')

    async def up(self):
        # pylint: disable=invalid-name
        """Send the command 'up' to the connected device."""
        await self._send_command('Up')

    async def confirm(self):
        """Send the command 'confirm' to the connected device."""
        await self._send_command('Confirm')

    async def down(self):
        """Send the command 'down' to the connected device."""
        await self._send_command('Down')

    async def right(self):
        """Send the command 'right' to the connected device."""
        await self._send_command('Right')

    async def left(self):
        """Send the command 'left' to the connected device."""
        await self._send_command('Left')

    async def home(self):
        """Send the command 'home' to the connected device."""
        await self._send_command('Home')

    async def options(self):
        """Send the command 'options' to the connected device."""
        await self._send_command('Options')

    async def returns(self):
        """Send the command 'returns' to the connected device."""
        await self._send_command('Return')

    async def num1(self):
        """Send the command 'num1' to the connected device."""
        await self._send_command('Num1')

    async def num2(self):
        """Send the command 'num2' to the connected device."""
        await self._send_command('Num2')

    async def num3(self):
        """Send the command 'num3' to the connected device."""
        await self._send_command('Num3')

    async def num4(self):
        """Send the command 'num4' to the connected device."""
        await self._send_command('Num4')

    async def num5(self):
        """Send the command 'num5' to the connected device."""
        await self._send_command('Num5')

    async def num6(self):
        """Send the command 'num6' to the connected device."""
        await self._send_command('Num6')

    async def num7(self):
        """Send the command 'num7' to the connected device."""
        await self._send_command('Num7')

    async def num8(self):
        """Send the command 'num8' to the connected device."""
        await self._send_command('Num8')

    async def num9(self):
        """Send the command 'num9' to the connected device."""
        await self._send_command('Num9')

    async def num0(self):
        """Send the command 'num0' to the connected device."""
        await self._send_command('Num0')

    async def display(self):
        """Send the command 'display' to the connected device."""
        await self._send_command('Display')

    async def audio(self):
        """Send the command 'audio' to the connected device."""
        await self._send_command('Audio')

    async def sub_title(self):
        """Send the command 'subTitle' to the connected device."""
        await self._send_command('SubTitle')

    async def favorites(self):
        """Send the command 'favorites' to the connected device."""
        await self._send_command('Favorites')

    async def yellow(self):
        """Send the command 'yellow' to the connected device."""
        await self._send_command('Yellow')

    async def blue(self):
        """Send the command 'blue' to the connected device."""
        await self._send_command('Blue')

    async def red(self):
        """Send the command 'red' to the connected device."""
        await self._send_command('Red')

    async def green(self):
        """Send the command 'green' to the connected device."""
        await self._send_command('Green')

    async def play(self):
        """Send the command 'play' to the connected device."""
        await self._send_command('Play')

    async def stop(self):
        """Send the command 'stop' to the connected device."""
        await self._send_command('Stop')

    async def pause(self):
        """Send the command 'pause' to the connected device."""
        await self._send_command('Pause')

    async def rewind(self):
        """Send the command 'rewind' to the connected device."""
        await self._send_command('Rewind')

    async def forward(self):
        """Send the command 'forward' to the connected device."""
        await self._send_command('Forward')

    async def prev(self):
        """Send the command 'prev' to the connected device."""
        await self._send_command('Prev')

    async def next(self):
        """Send the command 'next' to the connected device."""
        await self._send_command('Next')

    async def replay(self):
        """Send the command 'replay' to the connected device."""
        await self._send_command('Replay')

    async def advance(self):
        """Send the command 'advance' to the connected device."""
        await self._send_command('Advance')

    async def angle(self):
        """Send the command 'angle' to the connected device."""
        await self._send_command('Angle')

    async def top_menu(self):
        """Send the command 'top_menu' to the connected device."""
        await self._send_command('TopMenu')

    async def pop_up_menu(self):
        """Send the command 'pop_up_menu' to the connected device."""
        await self._send_command('PopUpMenu')

    async def eject(self):
        """Send the command 'eject' to the connected device."""
        await self._send_command('Eject')

    async def karaoke(self):
        """Send the command 'karaoke' to the connected device."""
        await self._send_command('Karaoke')

    async def netflix(self):
        """Send the command 'netflix' to the connected device."""
        await self._send_command('Netflix')

    async def mode_3d(self):
        """Send the command 'mode_3d' to the connected device."""
        await self._send_command('Mode3D')

    async def zoom_in(self):
        """Send the command 'zoom_in' to the connected device."""
        await self._send_command('ZoomIn')

    async def zoom_out(self):
        """Send the command 'zoom_out' to the connected device."""
        await self._send_command('ZoomOut')

    async def browser_back(self):
        """Send the command 'browser_back' to the connected device."""
        await self._send_command('BrowserBack')

    async def browser_forward(self):
        """Send the command 'browser_forward' to the connected device."""
        await self._send_command('BrowserForward')

    async def browser_bookmark_list(self):
        """Send the command 'browser_bookmarkList' to the connected device."""
        await self._send_command('BrowserBookmarkList')

    async def list(self):
        """Send the command 'list' to the connected device."""
        await self._send_command('List')


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
    """Try to find an element in an xml

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
