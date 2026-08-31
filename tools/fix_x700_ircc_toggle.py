from pathlib import Path

path = Path("src/client.py")
text = path.read_text()
old = '''    @cmd_wrapper
    async def play_pause(self):
        """Toggle play/pause."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        if self.state == States.PLAYING:
            return await self._sony_device.pause()
        return await self._sony_device.play()
'''
new = '''    @cmd_wrapper
    async def play_pause(self):
        """Toggle play/pause using the protocol semantics of the device."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())

        sony_device = self._require_device()
        # Legacy Sony IRCC players expose a dedicated Pause remote key which
        # behaves as the physical Play/Pause toggle. Their CERS status is not
        # reliable enough to choose between Play and Pause (notably UBP-X700
        # with UHD Blu-ray discs, where getStatus only reports status=disc).
        if self.capabilities.ircc:
            return await sony_device.pause()

        # DLNA-only players expose separate UPnP Play and Pause actions, so a
        # state-based toggle is required for that transport.
        if self.state == States.PLAYING:
            return await sony_device.pause()
        return await sony_device.play()
'''
if old not in text:
    raise SystemExit("play_pause block not found")
path.write_text(text.replace(old, new))
