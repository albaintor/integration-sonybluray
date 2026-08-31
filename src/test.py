# pylint: skip-file
# flake8: noqa
"""Read-only diagnostic tool for Sony Blu-ray network capabilities."""

import argparse
import asyncio
import json
import logging
from typing import Any
from xml.etree import ElementTree

from discover import async_identify_sonybluray_devices
from sonyapilib.device import HttpMethod, SonyDevice

DEFAULT_ADDRESS = "192.168.1.117"
DEFAULT_CLIENT_NAME = "Damien-PC"
DEFAULT_MAC_ADDRESS = "38-18-4c-31-5a-45"
DEFAULT_PIN_CODE = "4624"

AVTRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
AVTRANSPORT_ACTIONS = {
    "transport_info": "GetTransportInfo",
    "position_info": "GetPositionInfo",
    "media_info": "GetMediaInfo",
    "current_transport_actions": "GetCurrentTransportActions",
}


def parse_args() -> argparse.Namespace:
    """Parse command-line options while preserving the current test device defaults."""
    parser = argparse.ArgumentParser(
        description="Inspect the IRCC, CERS, DLNA and Wake-on-LAN capabilities of a Sony Blu-ray player."
    )
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="Player IP address")
    parser.add_argument("--client-name", default=DEFAULT_CLIENT_NAME)
    parser.add_argument("--pin-code", default=DEFAULT_PIN_CODE)
    parser.add_argument("--mac-address", default=DEFAULT_MAC_ADDRESS)
    parser.add_argument("--app-port", type=int, default=50202)
    parser.add_argument("--dmr-port", type=int, default=52323)
    parser.add_argument("--ircc-port", type=int, default=50001)
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Also run SSDP discovery and include all detected Sony players",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def local_name(tag: str) -> str:
    """Return an XML tag or attribute name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def xml_records(xml_data: str | None) -> list[dict[str, Any]]:
    """Flatten useful XML values without assuming a model-specific response schema."""
    if not xml_data:
        return []

    root = ElementTree.fromstring(xml_data)
    records: list[dict[str, Any]] = []
    for element in root.iter():
        attributes = {local_name(name): value for name, value in element.attrib.items()}
        text = (element.text or "").strip()
        if not attributes and not text:
            continue

        record: dict[str, Any] = {"tag": local_name(element.tag)}
        if attributes:
            record["attributes"] = attributes
        if text:
            record["text"] = text
        records.append(record)
    return records


def first_xml_text(root: ElementTree.Element, name: str) -> str | None:
    """Return the first non-empty text value for a local XML tag name."""
    for element in root.iter():
        if local_name(element.tag) == name and element.text:
            return element.text.strip()
    return None


def has_xml_tag(root: ElementTree.Element, name: str) -> bool:
    """Return whether a local XML tag name is present."""
    return any(local_name(element.tag) == name for element in root.iter())


def xml_flag(root: ElementTree.Element, name: str) -> bool:
    """Read a boolean-like XML flag."""
    value = first_xml_text(root, name)
    return value is not None and value.casefold() in {"1", "true", "yes", "supported"}


def xml_text_values(root: ElementTree.Element, name: str) -> list[str]:
    """Return sorted, unique text values for a local XML tag name."""
    values: set[str] = set()
    for element in root.iter():
        if local_name(element.tag) != name:
            continue
        value = (element.text or "").strip()
        if value:
            values.add(value)
    return sorted(values)


def summarize_descriptor(xml_data: str | None) -> dict[str, Any]:
    """Extract model identity and advertised network services from dmr.xml."""
    if not xml_data:
        return {"error": "No DMR descriptor received"}

    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError as exc:
        return {"error": f"Invalid DMR XML: {exc}"}

    return {
        "manufacturer": first_xml_text(root, "manufacturer"),
        "model": first_xml_text(root, "modelName"),
        "friendly_name": first_xml_text(root, "friendlyName"),
        "device_types": xml_text_values(root, "deviceType"),
        "services": xml_text_values(root, "serviceType"),
        "standard_dmr": has_xml_tag(root, "X_StandardDMR"),
        "ircc_advertised": has_xml_tag(root, "X_IRCC_DeviceInfo"),
        "cers_action_list_url": first_xml_text(root, "X_CERS_ActionList_URL"),
        "scalar_web_api": has_xml_tag(root, "X_ScalarWebAPI_DeviceInfo"),
        "magic_packet_wake_supported": xml_flag(root, "X_MagicPacketWakeSupported")
        or xml_flag(root, "magicPacketWakeSupported"),
    }


async def read_cers_action(device: SonyDevice, action_name: str) -> dict[str, Any]:
    """Call one advertised CERS read action and preserve its model-specific fields."""
    action = device.actions.get(action_name)
    if action is None or action.url is None:
        return {"available": False}

    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        return {
            "available": True,
            "url": action.url,
            "response": xml_records(response),
        }
    except Exception as exc:
        return {
            "available": True,
            "url": action.url,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def read_avtransport_action(device: SonyDevice, action_name: str) -> dict[str, Any]:
    """Call one read-only AVTransport action."""
    if not device.av_transport_url:
        return {"available": False}

    params = (
        f'<m:{action_name} xmlns:m="{AVTRANSPORT_SERVICE}">'
        "<InstanceID>0</InstanceID>"
        f"</m:{action_name}>"
    )
    soap_action = f"{AVTRANSPORT_SERVICE}#{action_name}"

    try:
        response = await device._post_soap_request(
            url=device.av_transport_url,
            params=params,
            action=soap_action,
        )
        return {
            "available": True,
            "response": xml_records(response),
        }
    except Exception as exc:
        return {
            "available": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def diagnose(args: argparse.Namespace) -> dict[str, Any]:
    """Build a capability and status report without sending control commands."""
    device = SonyDevice(
        host=args.address,
        app_port=args.app_port,
        ircc_port=args.ircc_port,
        dmr_port=args.dmr_port,
        psk=None,
        nickname=args.client_name,
    )
    device.pin = args.pin_code or None
    device.mac = args.mac_address or None

    descriptor_xml: str | None = None
    descriptor_error: str | None = None
    try:
        descriptor_xml = await device._send_http(
            device.dmr_url,
            method=HttpMethod.GET,
            raise_errors=True,
        )
    except Exception as exc:
        descriptor_error = f"{type(exc).__name__}: {exc}"

    initialized = False
    initialization_error: str | None = None
    if descriptor_xml:
        try:
            initialized = await device.init_device()
        except Exception as exc:
            initialization_error = f"{type(exc).__name__}: {exc}"
    else:
        initialization_error = descriptor_error or "DMR descriptor unavailable"

    descriptor = summarize_descriptor(descriptor_xml)
    if descriptor_error:
        descriptor["request_error"] = descriptor_error

    ircc = bool(descriptor.get("ircc_advertised") or device.control_url)
    cers = bool(descriptor.get("cers_action_list_url") or device.actions)
    dlna_transport = bool(
        device.av_transport_url
        or any("AVTransport" in service for service in descriptor.get("services", []))
    )
    wol_advertised = bool(descriptor.get("magic_packet_wake_supported"))
    wol_configured = bool(device.mac)

    if ircc and cers:
        backend = "ircc_cers"
    elif dlna_transport:
        backend = "dlna_only"
    elif wol_advertised or wol_configured:
        backend = "wol_only"
    else:
        backend = "unsupported"

    report: dict[str, Any] = {
        "target": {
            "address": args.address,
            "dmr_url": device.dmr_url,
            "ircc_url": device.ircc_url,
        },
        "initialized": initialized,
        "initialization_error": initialization_error,
        "api_version": device.api_version,
        "descriptor": descriptor,
        "capabilities": {
            "backend": backend,
            "ircc": ircc,
            "cers": cers,
            "dlna_transport": dlna_transport,
            "wol_advertised": wol_advertised,
            "wol_configured": wol_configured,
        },
        "endpoints": {
            "ircc_control": device.control_url,
            "cers_action_list": device.actionlist_url,
            "av_transport": device.av_transport_url,
        },
        "available_cers_actions": sorted(device.actions),
        "available_remote_commands": sorted(device.commands),
        "cers": {},
        "av_transport": {},
    }

    for action_name in ("getStatus", "getContentInformation", "getSystemInformation"):
        report["cers"][action_name] = await read_cers_action(device, action_name)

    for report_name, action_name in AVTRANSPORT_ACTIONS.items():
        report["av_transport"][report_name] = await read_avtransport_action(device, action_name)

    if args.discover:
        try:
            report["discovered_devices"] = await async_identify_sonybluray_devices()
        except Exception as exc:
            report["discovery_error"] = f"{type(exc).__name__}: {exc}"

    return report


async def main() -> None:
    """Run the diagnostic and print one machine-readable report."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report = await diagnose(args)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
