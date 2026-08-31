from pathlib import Path

path = Path("src/playback_probe.py")
text = path.read_text()

text = text.replace(
    "import logging\nfrom dataclasses import asdict, dataclass\n",
    "import logging\nimport socket\nfrom dataclasses import asdict, dataclass\n",
)
text = text.replace(
    "from xml.sax.saxutils import escape\n\nfrom sonyapilib.device",
    "from xml.sax.saxutils import escape\n\nimport aiohttp\nfrom aiohttp import web\n\nfrom sonyapilib.device",
)

text = text.replace(
    "    control_url: str\n    scpd_url: str\n    actions: tuple[str, ...] = ()\n",
    "    control_url: str\n    scpd_url: str\n    event_sub_url: str = \"\"\n    actions: tuple[str, ...] = ()\n",
)

text = text.replace(
    '    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples")\n',
    '    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples")\n'
    '    parser.add_argument(\n'
    '        "--event-wait",\n'
    '        type=float,\n'
    '        default=10.0,\n'
    '        help="Seconds to keep listening for UPnP GENA events after sampling",\n'
    '    )\n'
    '    parser.add_argument(\n'
    '        "--callback-address",\n'
    '        default="",\n'
    '        help="Local callback IP advertised to the player (auto-detected by default)",\n'
    '    )\n'
    '    parser.add_argument(\n'
    '        "--callback-port",\n'
    '        type=int,\n'
    '        default=0,\n'
    '        help="Local callback TCP port (0 lets the OS choose one)",\n'
    '    )\n'
    '    parser.add_argument(\n'
    '        "--no-events",\n'
    '        action="store_true",\n'
    '        help="Disable temporary UPnP GENA event subscriptions",\n'
    '    )\n',
)

text = text.replace(
    '        control_url = child_text(service_element, "controlURL")\n        scpd_url = child_text(service_element, "SCPDURL")\n',
    '        control_url = child_text(service_element, "controlURL")\n        scpd_url = child_text(service_element, "SCPDURL")\n        event_sub_url = child_text(service_element, "eventSubURL")\n',
)
text = text.replace(
    '            control_url=urljoin(device.dmr_url, control_url),\n            scpd_url=urljoin(device.dmr_url, scpd_url),\n',
    '            control_url=urljoin(device.dmr_url, control_url),\n            scpd_url=urljoin(device.dmr_url, scpd_url),\n            event_sub_url=urljoin(device.dmr_url, event_sub_url) if event_sub_url else "",\n',
)

marker = "\n\nasync def run_probe(args: argparse.Namespace) -> None:\n"
insert = r'''


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
        self.received += 1
        label = request.match_info.get("service", "unknown")
        sid = request.headers.get("SID", "-")
        seq = request.headers.get("SEQ", "-")
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
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
'''
if marker not in text:
    raise SystemExit("run_probe marker not found")
text = text.replace(marker, insert + marker)

anchor = "    content_response, content_error = await read_cers_content(device)\n"
event_setup = r'''    event_monitor: UpnpEventMonitor | None = None
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

'''
if anchor not in text:
    raise SystemExit("content anchor not found")
text = text.replace(anchor, event_setup + anchor, 1)

end_anchor = '    print("Interpretation:")\n'
event_wait = r'''    if event_monitor is not None:
        wait_seconds = max(0.0, args.event_wait)
        if wait_seconds:
            print(
                f"Listening for additional UPnP events for {wait_seconds:g}s. "
                "Toggle Play/Pause or navigate on the player now to look for LastChange notifications."
            )
            await asyncio.sleep(wait_seconds)
        print(f"UPnP events received: {event_monitor.received}")
        if event_monitor.received == 0:
            print(
                "  No NOTIFY received. If SUBSCRIBE succeeded, verify that the callback address is "
                "reachable from the player; WSL/Docker users may need --callback-address."
            )
        await event_monitor.close()
        print()

'''
if end_anchor not in text:
    raise SystemExit("interpretation anchor not found")
text = text.replace(end_anchor, event_wait + end_anchor, 1)

text = text.replace(
    '    print("- If CERS only reports status=disc while AVTransport is idle, neither API exposes physical-disc timing.")\n',
    '    print("- GENA LastChange notifications can expose transport variables not returned by polling getters.")\n'
    '    print("- If GENA also reports NONE/NO_MEDIA_PRESENT while CERS reports status=disc, UPnP is network-renderer only.")\n'
    '    print("- If CERS only reports status=disc while AVTransport is idle, neither polling API exposes physical-disc timing.")\n',
)

path.write_text(text)
