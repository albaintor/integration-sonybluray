from pathlib import Path

# Device capability semantics: DLNA on mixed IRCC players is an auxiliary
# network renderer, not the primary physical-disc transport.
path = Path("src/sonyapilib/device.py")
text = path.read_text()
anchor = '''    @property
    def backend(self) -> str:
'''
insert = '''    @property
    def primary_dlna_transport(self) -> bool:
        """Return whether DLNA AVTransport is the primary playback transport."""
        return self.dlna and not self.ircc

    @property
    def media_timing(self) -> bool:
        """Return whether static media position/duration features are reliable."""
        return self.primary_dlna_transport

'''
if insert not in text:
    if anchor not in text:
        raise SystemExit("device capability anchor not found")
    text = text.replace(anchor, insert + anchor, 1)
path.write_text(text)

# Only advertise seek/timing when DLNA is the primary transport. IRCC players
# still use IRCC for physical-disc transport controls and may expose a separate
# DLNA renderer that does not represent the disc.
path = Path("src/media_player.py")
text = path.read_text()
old = '''    if capabilities.dlna:
        features.append(Features.SEEK)
    if capabilities.media_state:
        features.extend([Features.MEDIA_DURATION, Features.MEDIA_POSITION])
'''
new = '''    if capabilities.primary_dlna_transport:
        features.append(Features.SEEK)
    if capabilities.media_timing:
        features.extend([Features.MEDIA_DURATION, Features.MEDIA_POSITION])
'''
if old not in text:
    raise SystemExit("media_player feature block not found")
text = text.replace(old, new, 1)
path.write_text(text)

# Regression coverage for mixed IRCC+CERS+DLNA players versus DLNA-only devices.
path = Path("tests/test_protocols.py")
text = path.read_text()
old_import = '''from sonyapilib.device import DeviceCapabilities, DeviceState, SonyDevice, XmlApiObject  # noqa: E402
'''
new_import = '''from media_player import features_for  # noqa: E402
from sonyapilib.device import DeviceCapabilities, DeviceState, SonyDevice, XmlApiObject  # noqa: E402
from ucapi.media_player import Features  # noqa: E402
'''
if old_import not in text:
    raise SystemExit("test import anchor not found")
text = text.replace(old_import, new_import, 1)

anchor = '''    def test_parse_cers_playback_info(self):
'''
tests = '''    def test_mixed_ircc_dlna_does_not_advertise_renderer_timing_or_seek(self):
        capabilities = DeviceCapabilities(ircc=True, cers=True, dlna=True, wol=True)
        features = features_for(capabilities)
        self.assertFalse(capabilities.primary_dlna_transport)
        self.assertFalse(capabilities.media_timing)
        self.assertNotIn(Features.SEEK, features)
        self.assertNotIn(Features.MEDIA_POSITION, features)
        self.assertNotIn(Features.MEDIA_DURATION, features)
        self.assertIn(Features.PLAY_PAUSE, features)
        self.assertIn(Features.MEDIA_TITLE, features)

    def test_dlna_only_advertises_renderer_timing_and_seek(self):
        capabilities = DeviceCapabilities(dlna=True)
        features = features_for(capabilities)
        self.assertTrue(capabilities.primary_dlna_transport)
        self.assertTrue(capabilities.media_timing)
        self.assertIn(Features.SEEK, features)
        self.assertIn(Features.MEDIA_POSITION, features)
        self.assertIn(Features.MEDIA_DURATION, features)
        self.assertIn(Features.PLAY_PAUSE, features)

'''
if tests not in text:
    if anchor not in text:
        raise SystemExit("test insertion anchor not found")
    text = text.replace(anchor, tests + anchor, 1)
path.write_text(text)
