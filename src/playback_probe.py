#!/usr/bin/env python
# pylint: skip-file
# flake8: noqa
"""Read-only playback diagnostic probe for Sony Blu-ray players."""

import argparse
import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree
from xml.sax.saxutils import escape

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
        if not service_type or not control_url or not scpd_url:
            continue

        service = DlnaService(
            service_type=service_type,
            service_id=child_text(service_element, "serviceId") or "",
            control_url=urljoin(device.dmr_url, control_url),
            scpd_url=urljoin(device.dmr_url, scpd_url),
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

    print("Interpretation:")
    print("- GetDeviceCapabilities/PlayMedia shows which media the UPnP renderer actually controls.")
    print("- GetMediaInfo and GetPositionInfo may expose URI, DIDL-Lite metadata, track, duration and position.")
    print("- GetCurrentTransportActions shows which transport operations UPnP currently considers valid.")
    print("- ConnectionManager reports active DLNA connections and supported source/sink protocolInfo values.")
    print("- DLNA STOPPED/NO_MEDIA_PRESENT with 0:00:00 timing still indicates the network renderer is idle.")
    print("- If CERS only reports status=disc while AVTransport is idle, neither API exposes physical-disc timing.")


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
