from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected block not found in {path}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/sonyapilib/device.py",
    '''    @property\n    def media_state(self) -> bool:\n        """Return whether playback state and timing information can be queried."""\n        return self.cers or self.dlna\n\n    @property\n    def primary_dlna_transport(self) -> bool:\n        """Return whether DLNA AVTransport is the primary playback transport."""\n        return self.dlna and not self.ircc\n\n    @property\n    def media_timing(self) -> bool:\n        """Return whether static media position/duration features are reliable."""\n        return self.primary_dlna_transport\n''',
    '''    @property\n    def media_state(self) -> bool:\n        """Return whether physical-media playback state can be queried."""\n        # Sony DMR AVTransport represents the network renderer, not the\n        # physical Blu-ray/DVD/USB transport (e.g. UBP-X800M2).\n        return self.cers\n\n    @property\n    def primary_dlna_transport(self) -> bool:\n        """Return whether DLNA can be used as the physical-media transport."""\n        return False\n\n    @property\n    def media_timing(self) -> bool:\n        """Return whether physical-media position/duration are reliable."""\n        return False\n''',
)

replace_once(
    "src/sonyapilib/device.py",
    '''    @property\n    def transport_protocol(self) -> str | None:\n        """Return the protocol used for play, pause, stop, next and previous."""\n        if self.ircc:\n            return ControlProtocol.IRCC.value\n        if self.dlna:\n            return ControlProtocol.DLNA.value\n        return None\n''',
    '''    @property\n    def transport_protocol(self) -> str | None:\n        """Return the protocol used for physical-media transport commands."""\n        if self.ircc:\n            return ControlProtocol.IRCC.value\n        return None\n''',
)

replace_once(
    "src/media_player.py",
    '''    if capabilities.ircc or capabilities.dlna:\n        features.extend(\n            [\n                Features.PLAY_PAUSE,\n                Features.STOP,\n                Features.PREVIOUS,\n                Features.NEXT,\n            ]\n        )\n''',
    '''    if capabilities.ircc:\n        features.extend(\n            [\n                Features.PLAY_PAUSE,\n                Features.STOP,\n                Features.PREVIOUS,\n                Features.NEXT,\n            ]\n        )\n''',
)

replace_once(
    "tests/test_protocols.py",
    '''    def test_dlna_only_advertises_renderer_timing_and_seek(self):\n        capabilities = DeviceCapabilities(dlna=True)\n        features = features_for(capabilities)\n        self.assertTrue(capabilities.primary_dlna_transport)\n        self.assertTrue(capabilities.media_timing)\n        self.assertIn(Features.SEEK, features)\n        self.assertIn(Features.MEDIA_POSITION, features)\n        self.assertIn(Features.MEDIA_DURATION, features)\n        self.assertIn(Features.PLAY_PAUSE, features)\n''',
    '''    def test_dlna_only_does_not_advertise_physical_media_controls(self):\n        capabilities = DeviceCapabilities(dlna=True)\n        features = features_for(capabilities)\n        self.assertFalse(capabilities.primary_dlna_transport)\n        self.assertFalse(capabilities.media_state)\n        self.assertFalse(capabilities.media_timing)\n        self.assertIsNone(capabilities.transport_protocol)\n        self.assertNotIn(Features.SEEK, features)\n        self.assertNotIn(Features.MEDIA_POSITION, features)\n        self.assertNotIn(Features.MEDIA_DURATION, features)\n        self.assertNotIn(Features.PLAY_PAUSE, features)\n        self.assertNotIn(Features.STOP, features)\n        self.assertNotIn(Features.PREVIOUS, features)\n        self.assertNotIn(Features.NEXT, features)\n''',
)
