from pathlib import Path

path = Path("src/playback_probe.py")
text = path.read_text()

text = text.replace(
    "        self.subscriptions: list[tuple[str, str, str]] = []\n        self.received = 0\n",
    "        self.subscriptions: list[tuple[str, str, str]] = []\n"
    "        self.received = 0\n"
    "        self.closing = False\n"
    "        self.avtransport_transitions: list[tuple[str, str, str | None]] = []\n",
)

marker = "\n\nclass UpnpEventMonitor:\n"
helper = r'''


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
'''
if marker not in text:
    raise SystemExit("event monitor marker not found")
text = text.replace(marker, helper + marker, 1)

old_handler = '''    async def _handle_notify(self, request: web.Request) -> web.Response:\n        """Receive and print one GENA NOTIFY payload."""\n        body = await request.text()\n        self.received += 1\n        label = request.match_info.get("service", "unknown")\n        sid = request.headers.get("SID", "-")\n        seq = request.headers.get("SEQ", "-")\n        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")\n        print(f"\\nUPnP EVENT {label} @ {timestamp} SID={sid} SEQ={seq}")\n        details = summarize_event_payload(body)\n        if details:\n            for record in details:\n                print(f"  {record}")\n        else:\n            print("  (empty or unrecognized event payload)")\n        if self.raw and body:\n            print(f"--- RAW NOTIFY {label} ---")\n            print(body)\n            print(f"--- END RAW NOTIFY {label} ---")\n        return web.Response(status=200)\n'''
new_handler = '''    async def _handle_notify(self, request: web.Request) -> web.Response:\n        """Receive and print one GENA NOTIFY payload."""\n        body = await request.text()\n        if self.closing:\n            return web.Response(status=200)\n\n        self.received += 1\n        label = request.match_info.get("service", "unknown")\n        sid = request.headers.get("SID", "-")\n        seq = request.headers.get("SEQ", "-")\n        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")\n\n        if label == "AVTransport":\n            values = last_change_values(body)\n            if "CurrentTransportActions" in values:\n                self.avtransport_transitions.append(\n                    (timestamp, values["CurrentTransportActions"], values.get("TransportState"))\n                )\n\n        print(f"\\nUPnP EVENT {label} @ {timestamp} SID={sid} SEQ={seq}")\n        details = summarize_event_payload(body)\n        if details:\n            for record in details:\n                print(f"  {record}")\n        else:\n            print("  (empty or unrecognized event payload)")\n        if self.raw and body:\n            print(f"--- RAW NOTIFY {label} ---")\n            print(body)\n            print(f"--- END RAW NOTIFY {label} ---")\n        return web.Response(status=200)\n'''
if old_handler not in text:
    raise SystemExit("notify handler not found")
text = text.replace(old_handler, new_handler, 1)

text = text.replace(
    '    async def close(self) -> None:\n        """Unsubscribe and stop the temporary callback server."""\n        if self.session is not None:\n',
    '    async def close(self) -> None:\n        """Unsubscribe and stop the temporary callback server."""\n'
    '        self.closing = True\n'
    '        if self.session is not None:\n',
    1,
)

old_summary = '''        print(f"UPnP events received: {event_monitor.received}")\n        if event_monitor.received == 0:\n            print(\n                "  No NOTIFY received. If SUBSCRIBE succeeded, verify that the callback address is "\n                "reachable from the player; WSL/Docker users may need --callback-address."\n            )\n        await event_monitor.close()\n        print()\n'''
new_summary = '''        print(f"UPnP events received: {event_monitor.received}")\n        if event_monitor.avtransport_transitions:\n            print("AVTransport CurrentTransportActions transitions:")\n            for timestamp, actions, transport_state in event_monitor.avtransport_transitions:\n                state_text = f" transportState={transport_state!r}" if transport_state is not None else ""\n                print(f"  {timestamp}: actions={actions!r}{state_text}")\n        if event_monitor.received == 0:\n            print(\n                "  No NOTIFY received. If SUBSCRIBE succeeded, verify that the callback address is "\n                "reachable from the player; WSL/Docker users may need --callback-address."\n            )\n        await event_monitor.close()\n        print()\n'''
if old_summary not in text:
    raise SystemExit("event summary block not found")
text = text.replace(old_summary, new_summary, 1)

path.write_text(text)
