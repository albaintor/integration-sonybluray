#!/usr/bin/env python
# pylint: skip-file
# flake8: noqa
"""Read-only playback diagnostic probe for Sony Blu-ray players."""

import argparse
import asyncio
import logging
import socket
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import aiohttp
from aiohttp import web

from sonyapilib.device import HttpMethod, PlaybackInfo, SonyDevice

AVTRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL_SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"
CONNECTION_MANAGER_SERVICE = "urn:schemas-upnp-org:service:ConnectionManager:1"

AVTRANSPORT_ACTIONS = (
    "GetTransportInfo",
    "GetPositionInfo",
    "GetMediaInfo",
    "GetDeviceCapabilities",
    "GetTransportSettings",
    "GetCurrentTransportActions",
)
RENDERING_CONTROL_ACTIONS = (
    "GetMute",
    "GetVolume",
    "GetVolumeDB",
    "GetLoudness",
)
EXTRA_CERS_ACTIONS = ("getHistoryList", "getText")


@dataclass
class DlnaService:
    """One UPnP service advertised by the DMR descriptor."""

    service_type: str
    service_id: str
    control_url: str
    scpd_url: str
    event_sub_url: str = ""
    actions: tuple[str, ...] = ()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Sample Sony CERS and DLNA playback information, enumerate UPnP services "
            "and compare the raw responses with values normalized by the integration."
        )
    )
    parser.add_argument("--address", required=True, help="Blu-ray player IP address")
    parser.add_argument("--client-name", default="PlaybackProbe", help="Registered Sony client name")
    parser.add_argument("--pin-code", default="", help="Existing Sony Media Remote PIN, if required")
    parser.add_argument("--mac-address", default="", help="Known player MAC address, optional")
    parser.add_argument("--app-port", type=int, default=50202)
    parser.add_argument("--dmr-port", type=int, default=52323)
    parser.add_argument("--ircc-port", type=int, default=50001)
    parser.add_argument("--samples", type=int, default=5, help="Number of playback samples")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples")
    parser.add_argument(
        "--event-wait",
        type=float,
        default=10.0,
        help="Seconds to keep listening for UPnP GENA events after sampling",
    )
    parser.add_argument(
        "--event-poll-interval",
        type=float,
        default=0.5,
        help="Seconds between AVTransport polling checks while listening for events (0 disables)",
    )
    parser.add_argument(
        "--callback-address",
        default="",
        help="Local callback IP advertised to the player (auto-detected by default)",
    )
    parser.add_argument(
        "--callback-port",
        type=int,
        default=0,
        help="Local callback TCP port (0 lets the OS choose one)",
    )
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="Disable temporary UPnP GENA event subscriptions",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print complete raw CERS and UPnP XML responses",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def info_dict(info: PlaybackInfo | None) -> dict[str, Any] | None:
    """Convert normalized playback information into printable values."""
    if info is None:
        return None
    values = asdict(info)
    values["state"] = info.state.name
    return values


def format_seconds(value: int | None) -> str:
    """Format seconds as H:MM:SS while preserving missing values."""
    if value is None:
        return "-"
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def print_info(label: str, info: PlaybackInfo | None) -> None:
    """Print one normalized playback record on a compact line."""
    if info is None:
        print(f"  {label:<10} unavailable")
        return
    print(
        f"  {label:<10} state={info.state.name:<7} "
        f"position={format_seconds(info.position):>8} "
        f"duration={format_seconds(info.duration):>8} "
        f"source={info.source or '-'} title={info.title or '-'} speed={info.speed}"
    )


def local_name(tag: str) -> str:
    """Return an XML local name without a namespace prefix."""
    return tag.rsplit("}", 1)[-1]


def child_text(element: ElementTree.Element, name: str) -> str | None:
    """Read a direct child by local XML name."""
    for child in element:
        if local_name(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def legacy_viewing_status(xml_data: str | None) -> bool:
    """Reproduce the historical X700 playback heuristic as permissively as possible."""
    if not xml_data:
        return False
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        return False
    return any(element.attrib.get("name", "").casefold() == "viewing" for element in root.iter())


def shorten(value: str, limit: int = 700) -> str:
    """Keep large protocol and metadata fields readable."""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}... ({len(compact)} chars)"


def summarize_xml(xml_data: str | None, limit: int = 20) -> list[str]:
    """Return compact XML attributes/text useful for identifying Sony firmware fields."""
    if not xml_data:
        return []
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError as exc:
        return [f"invalid XML: {exc}"]

    records: list[str] = []
    for element in root.iter():
        attrs = ", ".join(f"{name}={value!r}" for name, value in element.attrib.items())
        text = (element.text or "").strip()
        if not attrs and not text:
            continue
        details = attrs
        if text:
            details = f"{details}, text={shorten(text)!r}" if details else f"text={shorten(text)!r}"
        records.append(f"{local_name(element.tag)}({details})")
        if len(records) >= limit:
            records.append("...")
            break
    return records


def summarize_embedded_xml(value: str, limit: int = 12) -> list[str]:
    """Decode DIDL-Lite or other XML carried inside a SOAP metadata field."""
    value = value.strip()
    if not value.startswith("<"):
        return []
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError:
        return []

    records: list[str] = []
    for element in root.iter():
        name = local_name(element.tag)
        text = (element.text or "").strip()
        attrs = ", ".join(f"{key}={shorten(val, 250)!r}" for key, val in element.attrib.items())
        if not text and not attrs:
            continue
        details = attrs
        if text:
            details = f"{details}, text={shorten(text, 400)!r}" if details else f"text={shorten(text, 400)!r}"
        records.append(f"{name}({details})")
        if len(records) >= limit:
            records.append("...")
            break
    return records


def soap_values(xml_data: str | None) -> dict[str, str]:
    """Return leaf output values from a SOAP response."""
    if not xml_data:
        return {}
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        return {}

    values: dict[str, str] = {}
    for element in root.iter():
        if len(element):
            continue
        text = (element.text or "").strip()
        if text:
            values[local_name(element.tag)] = text
    return values


def summarize_soap(xml_data: str | None, limit: int = 25) -> list[str]:
    """Summarize SOAP output fields and decode embedded metadata XML."""
    values = soap_values(xml_data)
    records: list[str] = []
    for name, value in values.items():
        records.append(f"{name}={shorten(value)!r}")
        if "metadata" in name.casefold():
            for metadata_record in summarize_embedded_xml(value):
                records.append(f"  metadata: {metadata_record}")
        if len(records) >= limit:
            records.append("...")
            break
    return records


async def read_cers_status(device: SonyDevice) -> tuple[str | None, PlaybackInfo | None, str | None]:
    """Read and parse the CERS getStatus action without falling back to DLNA."""
    action = device.actions.get("getStatus")
    if action is None or action.url is None:
        return None, None, "getStatus not advertised"
    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        if not response:
            return response, None, "empty response"
        return response, device._parse_cers_playback_info(response), None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, None, f"{type(exc).__name__}: {exc}"


async def read_cers_content(device: SonyDevice) -> tuple[str | None, str | None]:
    """Read the optional CERS content metadata response."""
    action = device.actions.get("getContentInformation")
    if action is None or action.url is None:
        return None, "getContentInformation not advertised"
    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        return response, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"{type(exc).__name__}: {exc}"


async def read_cers_action(device: SonyDevice, action_name: str) -> tuple[str | None, str | None]:
    """Read one optional CERS action without interpreting its firmware-specific payload."""
    action = device.actions.get(action_name)
    if action is None or action.url is None:
        return None, f"{action_name} not advertised"
    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        return response, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"{type(exc).__name__}: {exc}"


async def discover_dlna_services(device: SonyDevice) -> tuple[dict[str, DlnaService], str | None]:
    """Read DMR.xml and each service SCPD to discover the real UPnP action surface."""
    try:
        dmr_response = await device._send_http(device.dmr_url, method=HttpMethod.GET, raise_errors=True)
        if not dmr_response:
            return {}, "empty DMR descriptor"
        root = ElementTree.fromstring(dmr_response)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {}, f"{type(exc).__name__}: {exc}"

    services: dict[str, DlnaService] = {}
    for service_element in root.iter():
        if local_name(service_element.tag) != "service":
            continue
        service_type = child_text(service_element, "serviceType")
        control_url = child_text(service_element, "controlURL")
        scpd_url = child_text(service_element, "SCPDURL")
        event_sub_url = child_text(service_element, "eventSubURL")
        if not service_type or not control_url or not scpd_url:
            continue

        service = DlnaService(
            service_type=service_type,
            service_id=child_text(service_element, "serviceId") or "",
            control_url=urljoin(device.dmr_url, control_url),
            scpd_url=urljoin(device.dmr_url, scpd_url),
            event_sub_url=urljoin(device.dmr_url, event_sub_url) if event_sub_url else "",
        )
        try:
            scpd_response = await device._send_http(service.scpd_url, method=HttpMethod.GET, raise_errors=True)
            if scpd_response:
                scpd_root = ElementTree.fromstring(scpd_response)
                action_names: list[str] = []
                for action_element in scpd_root.iter():
                    if local_name(action_element.tag) != "action":
                        continue
                    action_name = child_text(action_element, "name")
                    if action_name:
                        action_names.append(action_name)
                service.actions = tuple(action_names)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.getLogger(__name__).debug("Cannot read SCPD %s: %s", service.scpd_url, exc)
        services[service_type] = service

    return services, None


async def call_upnp_action(
    device: SonyDevice,
    service: DlnaService,
    action_name: str,
    arguments: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Call a read-only UPnP SOAP action."""
    arguments = arguments or {}
    params = "".join(f"<{name}>{escape(str(value))}</{name}>" for name, value in arguments.items())
    data = f'<m:{action_name} xmlns:m="{service.service_type}">{params}</m:{action_name}>'
    soap_action = f"{service.service_type}#{action_name}"
    try:
        response = await device._post_soap_request(url=service.control_url, params=data, action=soap_action)
        return response, None if response else "empty response"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"{type(exc).__name__}: {exc}"


async def read_dlna(
    device: SonyDevice,
    services: dict[str, DlnaService],
) -> tuple[dict[str, str | None], PlaybackInfo | None, dict[str, str]]:
    """Read all useful AVTransport getters and parse transport/position information."""
    service = services.get(AVTRANSPORT_SERVICE)
    if service is None:
        return {}, None, {"AVTransport": "not advertised"}

    responses: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for action_name in AVTRANSPORT_ACTIONS:
        if service.actions and action_name not in service.actions:
            errors[action_name] = "not advertised by SCPD"
            continue
        response, error = await call_upnp_action(device, service, action_name, {"InstanceID": "0"})
        responses[action_name] = response
        if error:
            errors[action_name] = error

    parsed = device._parse_dlna_playback_info(
        responses.get("GetTransportInfo"),
        responses.get("GetPositionInfo"),
    )
    return responses, parsed, errors


async def print_auxiliary_dlna_diagnostics(
    device: SonyDevice,
    services: dict[str, DlnaService],
    raw: bool,
) -> None:
    """Probe read-only RenderingControl and ConnectionManager information."""
    rendering = services.get(RENDERING_CONTROL_SERVICE)
    if rendering:
        print("RenderingControl getters:")
        for action_name in RENDERING_CONTROL_ACTIONS:
            if rendering.actions and action_name not in rendering.actions:
                continue
            response, error = await call_upnp_action(
                device,
                rendering,
                action_name,
                {"InstanceID": "0", "Channel": "Master"},
            )
            if error:
                print(f"  {action_name}: {error}")
                continue
            print(f"  {action_name}:")
            for record in summarize_soap(response):
                print(f"    {record}")
            if raw and response:
                print(f"--- RAW {action_name} ---")
                print(response)
                print(f"--- END RAW {action_name} ---")
        print()

    connection = services.get(CONNECTION_MANAGER_SERVICE)
    if not connection:
        return

    print("ConnectionManager getters:")
    connection_ids: list[str] = []
    for action_name in ("GetProtocolInfo", "GetCurrentConnectionIDs"):
        if connection.actions and action_name not in connection.actions:
            continue
        response, error = await call_upnp_action(device, connection, action_name)
        if error:
            print(f"  {action_name}: {error}")
            continue
        print(f"  {action_name}:")
        for record in summarize_soap(response):
            print(f"    {record}")
        if action_name == "GetCurrentConnectionIDs":
            ids_value = soap_values(response).get("ConnectionIDs", "")
            connection_ids = [item.strip() for item in ids_value.split(",") if item.strip()]
        if raw and response:
            print(f"--- RAW {action_name} ---")
            print(response)
            print(f"--- END RAW {action_name} ---")

    if (not connection.actions or "GetCurrentConnectionInfo" in connection.actions) and connection_ids:
        for connection_id in connection_ids:
            response, error = await call_upnp_action(
                device,
                connection,
                "GetCurrentConnectionInfo",
                {"ConnectionID": connection_id},
            )
            if error:
                print(f"  GetCurrentConnectionInfo({connection_id}): {error}")
                continue
            print(f"  GetCurrentConnectionInfo({connection_id}):")
            for record in summarize_soap(response):
                print(f"    {record}")
            if raw and response:
                print(f"--- RAW GetCurrentConnectionInfo {connection_id} ---")
                print(response)
                print("--- END RAW GetCurrentConnectionInfo ---")
    print()


def callback_address_for(remote_host: str) -> str:
    """Return the local IPv4 address used by the route toward the player."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_host, 9))
        return sock.getsockname()[0]
    finally:
        sock.close()


def summarize_event_payload(xml_data: str | None, limit: int = 50) -> list[str]:
    """Summarize a UPnP event propertyset and decode LastChange XML."""
    values = soap_values(xml_data)
    records: list[str] = []
    for name, value in values.items():
        if name.casefold() == "lastchange":
            records.append("LastChange:")
            embedded = summarize_embedded_xml(value, limit=limit)
            if embedded:
                records.extend(f"  {record}" for record in embedded)
            else:
                records.append(f"  {shorten(value)!r}")
        else:
            records.append(f"{name}={shorten(value)!r}")
        if len(records) >= limit:
            records.append("...")
            break
    return records


def last_change_values(xml_data: str | None) -> dict[str, str]:
    """Extract val attributes from an evented UPnP LastChange payload."""
    outer = soap_values(xml_data)
    last_change = outer.get("LastChange")
    if not last_change:
        return {}
    try:
        root = ElementTree.fromstring(last_change)
    except ElementTree.ParseError:
        return {}

    values: dict[str, str] = {}
    for element in root.iter():
        if "val" in element.attrib:
            values[local_name(element.tag)] = element.attrib["val"]
    return values


class UpnpEventMonitor:
    """Temporary GENA callback server and subscriptions for read-only diagnostics."""

    def __init__(
        self,
        player_host: str,
        callback_address: str,
        callback_port: int,
        raw: bool,
    ) -> None:
        self.player_host = player_host
        self.callback_address = callback_address or callback_address_for(player_host)
        self.callback_port = callback_port
        self.raw = raw
        self.runner: web.AppRunner | None = None
        self.session: aiohttp.ClientSession | None = None
        self.subscriptions: list[tuple[str, str, str]] = []
        self.received = 0
        self.closing = False
        self.avtransport_transitions: list[tuple[str, str, str | None]] = []

    async def start(self) -> None:
        """Start a local HTTP endpoint that accepts UPnP NOTIFY requests."""
        app = web.Application()
        app.router.add_route("NOTIFY", "/upnp-events/{service}", self._handle_notify)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.callback_port)
        await site.start()
        sockets = getattr(site._server, "sockets", ())  # pylint: disable=protected-access
        if not sockets:
            raise RuntimeError("UPnP callback server did not expose a listening socket")
        self.callback_port = sockets[0].getsockname()[1]
        self.session = aiohttp.ClientSession()

    @property
    def callback_base(self) -> str:
        """Return the callback base URL advertised to the Sony player."""
        return f"http://{self.callback_address}:{self.callback_port}"

    async def subscribe(self, label: str, service: DlnaService) -> tuple[str | None, str | None]:
        """Subscribe to one UPnP event endpoint."""
        if not service.event_sub_url:
            return None, "eventSubURL not advertised"
        if self.session is None:
            return None, "callback server not started"
        callback = f"<{self.callback_base}/upnp-events/{label}>"
        headers = {
            "CALLBACK": callback,
            "NT": "upnp:event",
            "TIMEOUT": "Second-120",
        }
        try:
            async with self.session.request("SUBSCRIBE", service.event_sub_url, headers=headers) as response:
                body = await response.text()
                response.raise_for_status()
                sid = response.headers.get("SID")
                timeout = response.headers.get("TIMEOUT", "")
                if not sid:
                    return None, f"subscription accepted without SID: {shorten(body)}"
                self.subscriptions.append((label, service.event_sub_url, sid))
                return sid, timeout or None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return None, f"{type(exc).__name__}: {exc}"

    async def _handle_notify(self, request: web.Request) -> web.Response:
        """Receive and print one GENA NOTIFY payload."""
        body = await request.text()
        if self.closing:
            return web.Response(status=200)

        self.received += 1
        label = request.match_info.get("service", "unknown")
        sid = request.headers.get("SID", "-")
        seq = request.headers.get("SEQ", "-")
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

        if label == "AVTransport":
            values = last_change_values(body)
            if "CurrentTransportActions" in values:
                self.avtransport_transitions.append(
                    (timestamp, values["CurrentTransportActions"], values.get("TransportState"))
                )

        print(f"\nUPnP EVENT {label} @ {timestamp} SID={sid} SEQ={seq}")
        details = summarize_event_payload(body)
        if details:
            for record in details:
                print(f"  {record}")
        else:
            print("  (empty or unrecognized event payload)")
        if self.raw and body:
            print(f"--- RAW NOTIFY {label} ---")
            print(body)
            print(f"--- END RAW NOTIFY {label} ---")
        return web.Response(status=200)

    async def close(self) -> None:
        """Unsubscribe and stop the temporary callback server."""
        self.closing = True
        if self.session is not None:
            for label, event_url, sid in self.subscriptions:
                try:
                    async with self.session.request("UNSUBSCRIBE", event_url, headers={"SID": sid}) as response:
                        await response.read()
                        if response.status >= 400:
                            print(f"UPnP unsubscribe {label}: HTTP {response.status}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"UPnP unsubscribe {label}: {type(exc).__name__}: {exc}")
            await self.session.close()
            self.session = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None


async def poll_avtransport_during_event_window(
    device: SonyDevice,
    services: dict[str, DlnaService],
    duration: float,
    interval: float,
) -> list[tuple[str, str, str]]:
    """Poll the two AVTransport state getters while GENA notifications are being observed."""
    service = services.get(AVTRANSPORT_SERVICE)
    if service is None or duration <= 0 or interval <= 0:
        if duration > 0:
            await asyncio.sleep(duration)
        return []

    transitions: list[tuple[str, str, str]] = []
    previous: tuple[str, str] | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration

    while True:
        actions_response, actions_error = await call_upnp_action(
            device,
            service,
            "GetCurrentTransportActions",
            {"InstanceID": "0"},
        )
        transport_response, transport_error = await call_upnp_action(
            device,
            service,
            "GetTransportInfo",
            {"InstanceID": "0"},
        )

        actions = soap_values(actions_response).get("Actions", "") if not actions_error else f"ERROR:{actions_error}"
        transport = (
            soap_values(transport_response).get("CurrentTransportState", "")
            if not transport_error
            else f"ERROR:{transport_error}"
        )
        current = (actions, transport)
        if current != previous:
            timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
            transitions.append((timestamp, actions, transport))
            print(f"UPnP POLL AVTransport @ {timestamp}: " f"Actions={actions!r} TransportState={transport!r}")
            previous = current

        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))

    return transitions


async def run_probe(args: argparse.Namespace) -> None:
    """Initialize the player and repeatedly sample all playback information sources."""
    device = SonyDevice(
        host=args.address,
        nickname=args.client_name,
        app_port=args.app_port,
        dmr_port=args.dmr_port,
        ircc_port=args.ircc_port,
    )
    device.pin = args.pin_code or None
    device.mac = args.mac_address or None

    if not await device.init_device():
        raise RuntimeError("Player did not expose a supported IRCC or DLNA protocol")

    services, services_error = await discover_dlna_services(device)

    print(f"Player: {args.address}")
    print(f"API version: {device.api_version}")
    print(f"Capabilities: {', '.join(device.capabilities.protocols) or 'none'}")
    print(f"CERS actions: {', '.join(sorted(device.actions)) or 'none'}")
    print(f"AVTransport: {device.av_transport_url or 'none'}")
    print()

    if services_error:
        print(f"DLNA service discovery: {services_error}")
    elif services:
        print("DLNA/UPnP services advertised by DMR.xml:")
        for service in services.values():
            print(f"  {service.service_type}")
            print(f"    control: {service.control_url}")
            print(f"    SCPD:    {service.scpd_url}")
            print(f"    actions: {', '.join(service.actions) or 'SCPD unavailable'}")
        print()

    event_monitor: UpnpEventMonitor | None = None
    if services and not args.no_events:
        try:
            event_monitor = UpnpEventMonitor(
                player_host=args.address,
                callback_address=args.callback_address,
                callback_port=args.callback_port,
                raw=args.raw,
            )
            await event_monitor.start()
            print("UPnP GENA event subscriptions:")
            print(f"  callback: {event_monitor.callback_base}")
            for service_type, label in (
                (AVTRANSPORT_SERVICE, "AVTransport"),
                (RENDERING_CONTROL_SERVICE, "RenderingControl"),
                (CONNECTION_MANAGER_SERVICE, "ConnectionManager"),
            ):
                service = services.get(service_type)
                if service is None:
                    continue
                sid, subscription_info = await event_monitor.subscribe(label, service)
                if sid:
                    print(f"  {label}: subscribed SID={sid} timeout={subscription_info or '-'}")
                else:
                    print(f"  {label}: {subscription_info}")
            print()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"UPnP event monitor: {type(exc).__name__}: {exc}")
            if event_monitor is not None:
                await event_monitor.close()
            event_monitor = None
            print()

    content_response, content_error = await read_cers_content(device)
    if content_error:
        print(f"getContentInformation: {content_error}")
    elif content_response:
        print("getContentInformation fields:")
        for record in summarize_xml(content_response):
            print(f"  {record}")
        if args.raw:
            print("--- RAW getContentInformation ---")
            print(content_response)
            print("--- END RAW getContentInformation ---")
    print()

    for action_name in EXTRA_CERS_ACTIONS:
        action_response, action_error = await read_cers_action(device, action_name)
        if action_error:
            print(f"{action_name}: {action_error}")
            continue
        print(f"{action_name} fields:")
        for record in summarize_xml(action_response):
            print(f"  {record}")
        if args.raw and action_response:
            print(f"--- RAW {action_name} ---")
            print(action_response)
            print(f"--- END RAW {action_name} ---")
        print()

    if services:
        await print_auxiliary_dlna_diagnostics(device, services, args.raw)

    previous_position: int | None = None
    previous_dlna_details: dict[str, list[str]] = {}
    for sample in range(1, max(1, args.samples) + 1):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        cers_raw, cers_info, cers_error = await read_cers_status(device)
        dlna_raw, dlna_info, dlna_errors = await read_dlna(device, services)

        try:
            normalized = await device.get_playback_info()
            normalized_error = None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            normalized = None
            normalized_error = f"{type(exc).__name__}: {exc}"

        print(f"Sample {sample}/{max(1, args.samples)} @ {timestamp}")
        print_info("CERS", cers_info)
        print(f"  {'LEGACY':<10} viewing={legacy_viewing_status(cers_raw)}")
        print_info("DLNA", dlna_info)
        print_info("NORMALIZED", normalized)

        cers_fields = summarize_xml(cers_raw)
        if cers_fields:
            print("  CERS XML fields:")
            for record in cers_fields:
                print(f"    {record}")

        for action_name in AVTRANSPORT_ACTIONS:
            details = summarize_soap(dlna_raw.get(action_name))
            if not details:
                continue
            if sample == 1 or details != previous_dlna_details.get(action_name):
                print(f"  DLNA {action_name} fields:")
                for record in details:
                    print(f"    {record}")
            previous_dlna_details[action_name] = details

        if cers_error:
            print(f"  CERS error: {cers_error}")
        for action_name, error in dlna_errors.items():
            print(f"  DLNA {action_name}: {error}")
        if normalized_error:
            print(f"  Normalized error: {normalized_error}")

        if normalized is not None and normalized.position is not None:
            if previous_position is not None:
                print(f"  position delta: {normalized.position - previous_position:+d}s")
            previous_position = normalized.position

        if args.raw:
            if cers_raw:
                print("\n--- RAW getStatus ---")
                print(cers_raw)
                print("--- END RAW getStatus ---")
            for action_name, response in dlna_raw.items():
                if response:
                    print(f"\n--- RAW {action_name} ---")
                    print(response)
                    print(f"--- END RAW {action_name} ---")

        print()
        if sample < max(1, args.samples):
            await asyncio.sleep(max(0.0, args.interval))

    if event_monitor is not None:
        wait_seconds = max(0.0, args.event_wait)
        poll_transitions: list[tuple[str, str, str]] = []
        if wait_seconds:
            poll_interval = max(0.0, args.event_poll_interval)
            poll_note = f" while polling AVTransport every {poll_interval:g}s" if poll_interval > 0 else ""
            print(
                f"Listening for additional UPnP events for {wait_seconds:g}s{poll_note}. "
                "Toggle Play/Pause or navigate on the player now to look for LastChange notifications."
            )
            poll_transitions = await poll_avtransport_during_event_window(
                device,
                services,
                wait_seconds,
                poll_interval,
            )
        print(f"UPnP events received: {event_monitor.received}")
        if poll_transitions:
            print("AVTransport polling transitions during event window:")
            for timestamp, actions, transport in poll_transitions:
                print(f"  {timestamp}: actions={actions!r} transportState={transport!r}")
        if event_monitor.avtransport_transitions:
            print("AVTransport CurrentTransportActions transitions:")
            for timestamp, actions, transport_state in event_monitor.avtransport_transitions:
                state_text = f" transportState={transport_state!r}" if transport_state is not None else ""
                print(f"  {timestamp}: actions={actions!r}{state_text}")
        if event_monitor.received == 0:
            print(
                "  No NOTIFY received. If SUBSCRIBE succeeded, verify that the callback address is "
                "reachable from the player; WSL/Docker users may need --callback-address."
            )
        await event_monitor.close()
        print()

    print("Interpretation:")
    print("- GetDeviceCapabilities/PlayMedia shows which media the UPnP renderer actually controls.")
    print("- GetMediaInfo and GetPositionInfo may expose URI, DIDL-Lite metadata, track, duration and position.")
    print("- GetCurrentTransportActions shows which transport operations UPnP currently considers valid.")
    print("- ConnectionManager reports active DLNA connections and supported source/sink protocolInfo values.")
    print("- DLNA STOPPED/NO_MEDIA_PRESENT with 0:00:00 timing still indicates the network renderer is idle.")
    print("- GENA LastChange notifications can expose transport variables not returned by polling getters.")
    print("- Event-window polling checks whether transient SOAP getter changes occur without matching GENA events.")
    print("- If GENA also reports NONE/NO_MEDIA_PRESENT while CERS reports status=disc, UPnP is network-renderer only.")
    print(
        "- If CERS only reports status=disc while AVTransport is idle, neither polling API exposes physical-disc timing."
    )


async def main() -> None:
    """Run the playback probe."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await run_probe(args)


if __name__ == "__main__":
    asyncio.run(main())
