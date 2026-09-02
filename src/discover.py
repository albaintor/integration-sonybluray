#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""This module implements a discovery function for Orange TV."""

import asyncio
import logging
import re
import socket
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

# import netifaces
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring
from httpx import Response

_LOGGER = logging.getLogger(__name__)

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 2
SSDP_TARGET = (SSDP_ADDR, SSDP_PORT)
SSDP_ST_1 = "ssdp:all"
SSDP_ST_2 = "upnp:rootdevice"
SSDP_ST_3 = "urn:schemas-upnp-org:device:Basic:1"
SSDP_ST_4 = "urn:schemas-upnp-org:device:MediaRenderer:1"

SSDP_ST_LIST = (SSDP_ST_1, SSDP_ST_2, SSDP_ST_3, SSDP_ST_4)

SSDP_LOCATION_PATTERN = re.compile(r"(?<=LOCATION:\s).+?(?=\r)")

SCPD_XMLNS = "{urn:schemas-upnp-org:device-1-0}"
SCPD_DEVICE = f"{SCPD_XMLNS}device"
SCPD_DEVICELIST = f"{SCPD_XMLNS}deviceList"
SCPD_DEVICETYPE = f"{SCPD_XMLNS}deviceType"
SCPD_MANUFACTURER = f"{SCPD_XMLNS}manufacturer"
SCPD_MODELNAME = f"{SCPD_XMLNS}modelName"
SCPD_SERIALNUMBER = f"{SCPD_XMLNS}serialNumber"
SCPD_FRIENDLYNAME = f"{SCPD_XMLNS}friendlyName"
SCPD_PRESENTATIONURL = f"{SCPD_XMLNS}presentationURL"

AV_XMLNS = "{urn:schemas-sony-com:av}"
AV_DMR_TAG = f"{AV_XMLNS}X_StandardDMR"
AV_IRCC_TAG = f"{AV_XMLNS}X_IRCC_DeviceInfo"
AV_DMR_TAG2 = "X_StandardDMR"

SUPPORTED_DEVICETYPES = [
    "urn:schemas-upnp-org:device:Basic:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
]

SUPPORTED_MANUFACTURERS = ["Sony Corporation"]


def ssdp_request(ssdp_st: str, ssdp_mx: float = SSDP_MX) -> bytes:
    """Return request bytes for given st and mx."""
    return "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            f"ST: {ssdp_st}",
            f"MX: {ssdp_mx:d}",
            'MAN: "ssdp:discover"',
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
            "",
            "",
        ]
    ).encode("utf-8")


def get_local_ips() -> List[str]:
    """Get IPs of local network adapters."""
    return [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)]
    # ips = []
    # pylint: disable=c-extension-no-member
    # for interface in netifaces.interfaces():
    #     addresses = netifaces.ifaddresses(interface)
    #     for address in addresses.get(netifaces.AF_INET, []):
    #         ips.append(address["addr"])
    # return ips


async def async_identify_sonybluray_devices() -> List[Dict]:
    """
    Identify device using SSDP and SCPD queries.

    Returns a list of dictionaries which includes all discovered devices
    devices with keys "host", "modelName", "friendlyName", "presentationURL".
    """
    # Sending SSDP broadcast message to get resource urls from devices
    urls = await async_send_ssdp_broadcast()

    # Check which responding device is a Orange TV device and prepare output
    devices = []

    for url in urls:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=5.0)
                res.raise_for_status()
        except httpx.HTTPError:
            continue
        else:
            device = evaluate_scpd_xml(url, res)
            if device is not None:
                devices.append(device)

    consolidated_devices: List[Dict] = []
    for device in devices:
        existing = [dev for dev in consolidated_devices if dev.get("host", "") == device.get("host", "")]
        if len(existing) == 0:
            consolidated_devices.append(device)
        else:
            print("Updated device", device)
            if device.get("irccPort", None):
                existing[0]["irccPort"] = device["irccPort"]
            if device.get("dmrPort", None):
                existing[0]["dmrPort"] = device["dmrPort"]

    return consolidated_devices


async def async_send_ssdp_broadcast() -> Set[str]:
    """
    Send SSDP broadcast messages to discover UPnP devices.

    Returns a set of SCPD XML resource urls for all discovered devices.
    """
    # Send up to three different broadcast messages
    ips = get_local_ips()
    # Prepare output of responding devices
    urls = set()

    tasks = []
    for ip_addr in ips:
        tasks.append(async_send_ssdp_broadcast_ip(ip_addr))
    tasks.append(async_send_ssdp_broadcast_ip(""))
    results = await asyncio.gather(*tasks)

    for result in results:
        _LOGGER.debug("SSDP broadcast result received: %s", result)
        urls = urls.union(result)

    _LOGGER.debug("Following devices found: %s", urls)
    return urls


async def async_send_ssdp_broadcast_ip(ip_addr: str) -> Set[str]:
    """Send SSDP broadcast messages to a single IP."""
    try:
        # Ignore 169.254.0.0/16 addresses
        if ip_addr.startswith("169.254."):
            return set()

        # Prepare socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.bind((ip_addr, 0))

        # Get asyncio loop
        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(SonyBluraySSDP, sock=sock)

        # Wait for the timeout period
        await asyncio.sleep(SSDP_MX)

        # Close the connection
        transport.close()

        _LOGGER.debug("Got %s results after SSDP queries using ip %s", len(protocol.urls), ip_addr)

        return protocol.urls
    # pylint: disable = W0718
    except Exception:
        return set()


def evaluate_scpd_xml(url: str, response: Response) -> Optional[Dict]:
    # pylint: disable=too-many-statements
    """
    Evaluate SCPD XML.

    Returns dictionary with keys "host", "modelName", "friendlyName" and
    "presentationURL" if a Orange TV device was found and "None" if not.
    """
    try:
        root = fromstring(response.text)
        # Look for manufacturer "SoftAtHome" in response.
        # Using "try" in case tags are not available in XML
        device = {}
        device_xml: ET.Element | None = None

        device["manufacturer"] = root.find(SCPD_DEVICE).find(SCPD_MANUFACTURER).text

        _LOGGER.debug("Device %s has manufacturer %s", url, device["manufacturer"])

        if device["manufacturer"] not in SUPPORTED_MANUFACTURERS:
            return None

        if root.find(SCPD_DEVICE).find(SCPD_DEVICETYPE).text in SUPPORTED_DEVICETYPES:
            device_xml = root.find(SCPD_DEVICE)
        elif root.find(SCPD_DEVICE).find(SCPD_DEVICELIST) is not None:
            for dev in root.find(SCPD_DEVICE).find(SCPD_DEVICELIST):
                if dev.find(SCPD_DEVICETYPE).text in SUPPORTED_DEVICETYPES and dev.find(SCPD_SERIALNUMBER) is not None:
                    device_xml = dev
                    break

        if device_xml is None:
            return None

        presentation_url: str | None = None
        if device_xml.find(SCPD_PRESENTATIONURL) is not None:
            presentation_url = device_xml.find(SCPD_PRESENTATIONURL).text

        if presentation_url is not None and len(presentation_url) > 0:
            device["host"] = urlparse(presentation_url).hostname
            device["presentationURL"] = presentation_url
            device["port"] = urlparse(url).port
        else:
            device["host"] = urlparse(url).hostname
            device["port"] = urlparse(url).port

        if device["host"] is None:
            device["host"] = urlparse(url).hostname
            device["port"] = urlparse(url).port

        device["modelName"] = device_xml.find(SCPD_MODELNAME).text
        device["friendlyName"] = device_xml.find(SCPD_FRIENDLYNAME).text

        protocols: Set[str] = set()
        if device_xml.find(AV_IRCC_TAG) is not None:
            device["irccPort"] = device.get("port", 0)
            protocols.add("ircc")
        if device_xml.find(f".//{AV_XMLNS}X_CERS_ActionList_URL") is not None:
            protocols.add("cers")
        if device_xml.find(f".//{AV_XMLNS}X_ScalarWebAPI_DeviceInfo") is not None:
            protocols.add("scalar")
        # Not working in certain cases which needs a second way
        if device_xml.find(AV_DMR_TAG) is not None:
            device["dmrPort"] = device.get("port", 0)
            protocols.add("dlna")
        elif device_xml.findall(f"av:{AV_DMR_TAG2}", namespaces={"av": "urn:schemas-sony-com:av"}):
            device["dmrPort"] = device.get("port", 0)
            protocols.add("dlna")

        for element in device_xml.iter():
            name = element.tag.rsplit("}", 1)[-1].casefold()
            value = (element.text or "").strip()
            if name in {"servicetype", "serviceid"} and "AVTransport" in value:
                device["dmrPort"] = device.get("port", 0)
                protocols.add("dlna")
            if name in {"magicpacketwakesupported", "x_magicpacketwakesupported"} and value.casefold() in {
                "1",
                "true",
                "yes",
                "supported",
            }:
                protocols.add("wol")

        device["protocols"] = sorted(protocols)

        return device
    except (
        AttributeError,
        ValueError,
        ET.ParseError,
        DefusedXmlException,
        ParseError,
        UnicodeDecodeError,
    ) as err:
        _LOGGER.error("Error occurred during evaluation of SCPD XML from URI %s: %s", url, err)
        return None


class SonyBluraySSDP(asyncio.DatagramProtocol):
    """Implements datagram protocol for SSDP discovery of Orange TV devices."""

    def __init__(self) -> None:
        """Create instance."""
        self.urls = set()

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """Send SSDP request when connection was made."""
        # Prepare SSDP and send broadcast message
        for ssdp_st in SSDP_ST_LIST:
            request = ssdp_request(ssdp_st)
            transport.sendto(request, SSDP_TARGET)
            _LOGGER.debug("SSDP request sent %s", request)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Receive responses to SSDP call."""
        # Some string operations to get the receivers URL
        # which could be found between LOCATION and end of line of the response
        _LOGGER.debug("Response to SSDP call received: %s", data)
        data_text = data.decode("utf-8")
        match = SSDP_LOCATION_PATTERN.search(data_text)
        if match:
            self.urls.add(match.group(0))
