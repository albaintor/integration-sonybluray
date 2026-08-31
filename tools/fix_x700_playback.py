"""Apply the X700 playback-state regression fix and its tests."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/sonyapilib/device.py",
    '''        viewing_items: dict[str, str] | None = None
        for status in root.iter():
            if status.tag.rsplit("}", 1)[-1] != "status" or status.attrib.get("name") != "viewing":
                continue
            items: dict[str, str] = {}
            for item in status:
                field = item.attrib.get("field")
                value = item.attrib.get("value")
                if field and value is not None:
                    items[field.casefold()] = value
            viewing_items = items
            break
''',
    '''        viewing_items: dict[str, str] | None = None
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
''',
)

replace_once(
    "src/sonyapilib/device.py",
    '''        if cers_info:
            if dlna_info:
                cers_info.position = cers_info.position if cers_info.position is not None else dlna_info.position
                cers_info.duration = cers_info.duration if cers_info.duration is not None else dlna_info.duration
            return cers_info
''',
    '''        if cers_info:
            # AVTransport on Sony players can describe only the DLNA renderer,
            # not the physical Blu-ray transport. A STOPPED 0/0 DLNA response
            # must therefore not overwrite missing CERS timing for a disc.
            if dlna_info and dlna_info.state in {DeviceState.PLAYING, DeviceState.PAUSED}:
                cers_info.position = cers_info.position if cers_info.position is not None else dlna_info.position
                cers_info.duration = cers_info.duration if cers_info.duration is not None else dlna_info.duration
            return cers_info
''',
)

replace_once(
    "tests/test_protocols.py",
    '''        self.assertEqual(info.position, 65)
        self.assertEqual(info.duration, 7200)

    def test_parse_dlna_playback_info(self):
''',
    '''        self.assertEqual(info.position, 65)
        self.assertEqual(info.duration, 7200)

    def test_parse_cers_legacy_viewing_wrapper(self):
        response = """<response><activity name="viewing"><details /></activity></response>"""
        info = SonyDevice._parse_cers_playback_info(response)
        self.assertEqual(info.state, DeviceState.PLAYING)
        self.assertIsNone(info.position)
        self.assertIsNone(info.duration)

    def test_parse_dlna_playback_info(self):
''',
)

replace_once(
    "tests/test_protocols.py",
    '''class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):
''',
    '''class PlaybackFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Verify that DLNA renderer state does not corrupt physical-disc CERS state."""

    async def test_stopped_dlna_zero_timing_does_not_fill_cers_disc_state(self):
        device = SonyDevice("192.0.2.20", "test-client")
        device.capabilities.cers = True
        device.capabilities.dlna = True
        device.actions["getStatus"] = XmlApiObject({"name": "getStatus", "url": "http://example/status"})
        device._send_http = AsyncMock(return_value='<response><activity name="viewing" /></response>')
        device._get_dlna_playback_info = AsyncMock(
            return_value=device._parse_dlna_playback_info(
                "<Envelope><CurrentTransportState>STOPPED</CurrentTransportState><CurrentSpeed>1</CurrentSpeed></Envelope>",
                "<Envelope><RelTime>00:00:00</RelTime><TrackDuration>00:00:00</TrackDuration></Envelope>",
            )
        )

        info = await device.get_playback_info()
        self.assertEqual(info.state, DeviceState.PLAYING)
        self.assertIsNone(info.position)
        self.assertIsNone(info.duration)


class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):
''',
)
