"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from asyncio import shield
from typing import Any

from ucapi import EntityTypes, Remote, StatusCodes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes, Commands, Features, Options
from ucapi.remote import States as RemoteStates

from client import SonyBlurayDevice
from config import DeviceInstance, create_entity_id
from const import KEYS, SONY_REMOTE_BUTTONS_MAPPING, SONY_REMOTE_UI_PAGES, SONY_SIMPLE_COMMANDS

_LOG = logging.getLogger(__name__)

SONY_REMOTE_STATE_MAPPING = {
    MediaStates.UNKNOWN: RemoteStates.UNKNOWN,
    MediaStates.UNAVAILABLE: RemoteStates.UNAVAILABLE,
    MediaStates.OFF: RemoteStates.OFF,
    MediaStates.ON: RemoteStates.ON,
    MediaStates.PLAYING: RemoteStates.ON,
    MediaStates.PAUSED: RemoteStates.ON,
    MediaStates.STANDBY: RemoteStates.ON,
}


COMMAND_TIMEOUT = 4.5


def get_int_param(param: str, params: dict[str, Any], default: int):
    """Get parameter in integer format."""
    # TODO bug to be fixed on UC Core : some params are sent as (empty) strings by remote (hold == "")
    value = params.get(param, default)
    if isinstance(value, str) and len(value) > 0:
        return int(float(value))
    return value


class SonyRemote(Remote):
    """Representation of a Kodi Media Player entity."""

    def __init__(self, config_device: DeviceInstance, device: SonyBlurayDevice):
        """Initialize the class."""
        self._device = device
        _LOG.debug("SonyRemote init")
        entity_id = create_entity_id(config_device.id, EntityTypes.REMOTE)
        features = [Features.SEND_CMD, Features.ON_OFF, Features.TOGGLE]
        attributes = {
            Attributes.STATE: SONY_REMOTE_STATE_MAPPING.get(device.state),
        }
        # pylint: disable=R0801
        super().__init__(
            entity_id,
            config_device.name,
            features,
            attributes,
            button_mapping=SONY_REMOTE_BUTTONS_MAPPING,
            ui_pages=SONY_REMOTE_UI_PAGES,
        )

    async def command(
        self,
        cmd_id: str,
        params: dict[str, Any] | None = None,
        *,
        websocket: Any,
    ) -> StatusCodes:
        """
        Execute entity command with the installed command handler.

        Backward compatible:
        - Existing handlers usually accept (entity, cmd_id, params)
        - New handlers may optionally accept websocket as kw-only / kwarg

        Returns NOT_IMPLEMENTED if no command handler is installed.

        :param cmd_id: the command
        :param params: optional command parameters
        :param websocket: optional websocket connection. Allows for directed event
                          callbacks instead of broadcasts.
        :return: command status code to acknowledge to UCR2
        """
        _LOG.info("[%s] Got command request: %s %s", self.id, cmd_id, params)
        if self._device is None:
            _LOG.warning("[%s] No Sony device instance for this remote entity", self.id)
            return StatusCodes.NOT_FOUND
        res = StatusCodes.OK

        if cmd_id == Commands.ON:
            return await self._device.turn_on()
        if cmd_id == Commands.OFF:
            return await self._device.turn_off()
        if cmd_id == Commands.TOGGLE:
            return await self._device.toggle()
        if cmd_id in [Commands.SEND_CMD, Commands.SEND_CMD_SEQUENCE]:
            # If the duration exceeds the remote timeout, keep it running and return immediately
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    res = await shield(self.send_commands(cmd_id, params))
            except asyncio.TimeoutError:
                _LOG.info("[%s] Command request timeout, keep running: %s %s", self.id, cmd_id, params)
        else:
            return StatusCodes.NOT_IMPLEMENTED
        return res

    async def send_commands(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """Handle custom command or commands sequence."""
        # hold = self.get_int_param("hold", params, 0)
        delay = get_int_param("delay", params, 0)
        repeat = get_int_param("repeat", params, 1)
        command = params.get("command", "")
        res = StatusCodes.OK

        for _i in range(0, repeat):
            if cmd_id == Commands.SEND_CMD:
                result = await self.call_command(command)
                if result != StatusCodes.OK:
                    res = result
                if delay > 0:
                    await asyncio.sleep(delay / 1000)
            else:
                commands = params.get("sequence", [])
                for command in commands:
                    result = await self.call_command(command)
                    if result != StatusCodes.OK:
                        res = result
                    if delay > 0:
                        await asyncio.sleep(delay / 1000)
        return res

    async def call_command(self, command: str) -> StatusCodes:
        """Call a single command."""
        # pylint: disable=R0911
        if command == Commands.ON:
            return await self._device.turn_on()
        if command == Commands.OFF:
            return await self._device.turn_off()
        if command == Commands.TOGGLE:
            return await self._device.toggle()
        if command in KEYS:
            return await self._device.send_key(command)
        if command in self.options[Options.SIMPLE_COMMANDS]:
            return await self._device.send_key(SONY_SIMPLE_COMMANDS[command])
        return StatusCodes.NOT_IMPLEMENTED

    def _key_update_helper(self, key: str, value: str | None, attributes):
        """Update given attribute."""
        # pylint: disable=R0801
        if value is None:
            return attributes

        if key in self.attributes:
            if self.attributes[key] != value:
                attributes[key] = value
        else:
            attributes[key] = value

        return attributes

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        # pylint: disable=R0801
        attributes = {}

        if Attributes.STATE in update:
            state = SONY_REMOTE_STATE_MAPPING.get(update[Attributes.STATE])
            attributes = self._key_update_helper(Attributes.STATE, state, attributes)

        _LOG.debug("SonyRemote update attributes %s -> %s", update, attributes)
        return attributes
