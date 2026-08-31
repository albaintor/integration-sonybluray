from pathlib import Path

path = Path("src/playback_probe.py")
text = path.read_text()

text = text.replace(
    '    parser.add_argument(\n        "--event-wait",\n        type=float,\n        default=10.0,\n        help="Seconds to keep listening for UPnP GENA events after sampling",\n    )\n',
    '    parser.add_argument(\n        "--event-wait",\n        type=float,\n        default=10.0,\n        help="Seconds to keep listening for UPnP GENA events after sampling",\n    )\n'
    '    parser.add_argument(\n'
    '        "--event-poll-interval",\n'
    '        type=float,\n'
    '        default=0.5,\n'
    '        help="Seconds between AVTransport polling checks while listening for events (0 disables)",\n'
    '    )\n',
    1,
)

marker = "\n\nasync def run_probe(args: argparse.Namespace) -> None:\n"
insert = r'''

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
            print(
                f"UPnP POLL AVTransport @ {timestamp}: "
                f"Actions={actions!r} TransportState={transport!r}"
            )
            previous = current

        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))

    return transitions
'''
if marker not in text:
    raise SystemExit("run_probe marker not found")
text = text.replace(marker, insert + marker, 1)

old = '''        if wait_seconds:
            print(
                f"Listening for additional UPnP events for {wait_seconds:g}s. "
                "Toggle Play/Pause or navigate on the player now to look for LastChange notifications."
            )
            await asyncio.sleep(wait_seconds)
        print(f"UPnP events received: {event_monitor.received}")
'''
new = '''        poll_transitions: list[tuple[str, str, str]] = []
        if wait_seconds:
            poll_interval = max(0.0, args.event_poll_interval)
            poll_note = (
                f" while polling AVTransport every {poll_interval:g}s"
                if poll_interval > 0
                else ""
            )
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
'''
if old not in text:
    raise SystemExit("event wait block not found")
text = text.replace(old, new, 1)

text = text.replace(
    '    print("- GENA LastChange notifications can expose transport variables not returned by polling getters.")\n',
    '    print("- GENA LastChange notifications can expose transport variables not returned by polling getters.")\n'
    '    print("- Event-window polling checks whether transient SOAP getter changes occur without matching GENA events.")\n',
    1,
)

path.write_text(text)
