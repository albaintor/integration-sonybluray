#!/usr/bin/env python
"""Read-only playback diagnostic probe for Sony Blu-ray players."""

import argparse
import asyncio
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sonyapilib.device import HttpMethod, PlaybackInfo, SonyDevice

AVTRANSPORT_ACTIONS = ("GetTransportInfo", "GetPositionInfo", "GetMediaInfo")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Sample Sony CERS and DLNA playback information and compare the raw "
            "responses with the values normalized by the integration."
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
        help="Print the raw CERS and AVTransport XML responses for every sample",
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


async def read_dlna(device: SonyDevice) -> tuple[dict[str, str | None], PlaybackInfo | None, str | None]:
    """Read AVTransport responses and parse transport/position information."""
    if not device.av_transport_url:
        return {}, None, "AVTransport not advertised"

    responses: dict[str, str | None] = {}
    try:
        for action_name in AVTRANSPORT_ACTIONS:
            responses[action_name] = await device._get_avtransport_action(action_name)
        parsed = device._parse_dlna_playback_info(
            responses.get("GetTransportInfo"),
            responses.get("GetPositionInfo"),
        )
        return responses, parsed, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return responses, None, f"{type(exc).__name__}: {exc}"


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

    print(f"Player: {args.address}")
    print(f"API version: {device.api_version}")
    print(f"Capabilities: {', '.join(device.capabilities.protocols) or 'none'}")
    print(f"CERS actions: {', '.join(sorted(device.actions)) or 'none'}")
    print(f"AVTransport: {device.av_transport_url or 'none'}")
    print()

    content_response, content_error = await read_cers_content(device)
    if content_error:
        print(f"getContentInformation: {content_error}")
    elif args.raw and content_response:
        print("--- RAW getContentInformation ---")
        print(content_response)
        print("--- END RAW getContentInformation ---")
    print()

    previous_position: int | None = None
    for sample in range(1, max(1, args.samples) + 1):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        cers_raw, cers_info, cers_error = await read_cers_status(device)
        dlna_raw, dlna_info, dlna_error = await read_dlna(device)

        try:
            normalized = await device.get_playback_info()
            normalized_error = None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            normalized = None
            normalized_error = f"{type(exc).__name__}: {exc}"

        print(f"Sample {sample}/{max(1, args.samples)} @ {timestamp}")
        print_info("CERS", cers_info)
        print_info("DLNA", dlna_info)
        print_info("NORMALIZED", normalized)

        if cers_error:
            print(f"  CERS error: {cers_error}")
        if dlna_error:
            print(f"  DLNA error: {dlna_error}")
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
    print("- CERS position/duration present but NORMALIZED missing: parser mapping needs adjustment.")
    print("- DLNA position/duration present only: AVTransport can provide timing for the current media.")
    print("- Both sources show '-': the player/firmware is not exposing timing for the current playback mode.")
    print("- Run with --raw to inspect Sony field names before changing the parser.")


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
