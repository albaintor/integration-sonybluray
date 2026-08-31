from pathlib import Path

path = Path("src/sonyapilib/device.py")
text = path.read_text()

anchor = '''    @classmethod
    def _parse_dlna_playback_info(cls, transport_response: str | None, position_response: str | None) -> PlaybackInfo:
'''
helper = '''    @staticmethod
    def _parse_cers_content_info(response: str) -> dict[str, str]:
        """Extract optional content metadata fields from CERS getContentInformation."""
        root = xml.etree.ElementTree.fromstring(response)
        fields: dict[str, str] = {}
        for element in root.iter():
            field = element.attrib.get("field")
            value = element.attrib.get("value")
            if field and value is not None:
                fields[field.casefold()] = value
        return fields

    @classmethod
    def _parse_dlna_playback_info(cls, transport_response: str | None, position_response: str | None) -> PlaybackInfo:
'''
if anchor not in text:
    raise SystemExit("DLNA parser anchor not found")
text = text.replace(anchor, helper, 1)

anchor = '''        dlna_info = None
        if self.capabilities.dlna:
'''
addition = '''        if cers_info and "getContentInformation" in self.actions and (cers_info.source is None or cers_info.title is None):
            try:
                content_response = await self._send_http(
                    self._get_action("getContentInformation").url,
                    method=HttpMethod.GET,
                )
                if content_response:
                    content_fields = self._parse_cers_content_info(content_response)
                    cers_info.source = cers_info.source or content_fields.get("source")
                    cers_info.title = cers_info.title or content_fields.get("title")
            # Content metadata is optional and must never break state polling.
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("Cannot read CERS content information from %s: %s", self.host, ex)

        dlna_info = None
        if self.capabilities.dlna:
'''
if anchor not in text:
    raise SystemExit("get_playback_info anchor not found")
text = text.replace(anchor, addition, 1)
path.write_text(text)

path = Path("tests/test_protocols.py")
text = path.read_text()
anchor = '''    def test_parse_dlna_playback_info(self):
'''
test = '''    def test_parse_cers_content_info(self):
        response = """<response>
            <infoItem field="class" value="video" />
            <infoItem field="source" value="BD" />
            <infoItem field="mediaType" value="BD-ROM" />
            <infoItem field="mediaFormat" value="UHD" />
        </response>"""
        fields = SonyDevice._parse_cers_content_info(response)
        self.assertEqual(fields["source"], "BD")
        self.assertEqual(fields["mediatype"], "BD-ROM")
        self.assertEqual(fields["mediaformat"], "UHD")

    def test_parse_dlna_playback_info(self):
'''
if anchor not in text:
    raise SystemExit("test anchor not found")
text = text.replace(anchor, test, 1)
path.write_text(text)
