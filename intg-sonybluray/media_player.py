"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from client import SonyBlurayDevice
from config import DeviceInstance, create_entity_id
from ucapi import EntityTypes, MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, Options

from const import SONY_SIMPLE_COMMANDS

_LOG = logging.getLogger(__name__)


class SonyMediaPlayer(MediaPlayer):
    """Representation of a Sony Media Player entity."""

    def __init__(self, config_device: DeviceInstance, device: SonyBlurayDevice):
        """Initialize the class."""
        self._device = device

        entity_id = create_entity_id(config_device.id, EntityTypes.MEDIA_PLAYER)
        features = [
            Features.ON_OFF,
            Features.TOGGLE,
            Features.PLAY_PAUSE,
            Features.DPAD,
            Features.SETTINGS,
            Features.STOP,
            Features.EJECT,
            Features.FAST_FORWARD,
            Features.REWIND,
            Features.MENU,
            Features.CONTEXT_MENU,
            Features.NUMPAD,
            Features.CHANNEL_SWITCHER,
            Features.INFO,
            Features.AUDIO_TRACK,
            Features.SUBTITLE,
            Features.COLOR_BUTTONS,
            Features.HOME,
            Features.PREVIOUS,
            Features.NEXT,
            Features.VOLUME_UP_DOWN,
            Features.MUTE_TOGGLE
        ]
        attributes = {
            Attributes.STATE: device.state,
        }

        options = {
            Options.SIMPLE_COMMANDS: list(SONY_SIMPLE_COMMANDS.keys())
        }
        super().__init__(
            entity_id,
            config_device.name,
            features,
            attributes,
            device_class=DeviceClasses.STREAMING_BOX,
            options=options
        )

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """
        Media-player entity command handler.

        Called by the integration-API if a command is sent to a configured media-player entity.

        :param cmd_id: command
        :param params: optional command parameters
        :return: status code of the command request
        """
        _LOG.info("Got %s command request: %s %s", self.id, cmd_id, params)

        if self._device is None:
            _LOG.warning("No device instance for entity: %s", self.id)
            return StatusCodes.SERVICE_UNAVAILABLE
        elif cmd_id == Commands.ON:
            return await self._device.turn_on()
        elif cmd_id == Commands.OFF:
            return await self._device.turn_off()
        elif cmd_id == Commands.TOGGLE:
            return await self._device.toggle()
        elif cmd_id == Commands.CHANNEL_UP:
            return await self._device.channel_up()
        elif cmd_id == Commands.CHANNEL_DOWN:
            return await self._device.channel_down()
        elif cmd_id == Commands.PLAY_PAUSE:
            return await self._device.play_pause()
        elif cmd_id == Commands.STOP:
            return await self._device.stop()
        elif cmd_id == Commands.EJECT:
            return await self._device.eject()
        elif cmd_id == Commands.FAST_FORWARD:
            return await self._device.fast_forward()
        elif cmd_id == Commands.REWIND:
            return await self._device.rewind()
        elif cmd_id == Commands.CURSOR_UP:
            return await self._device.send_key("Up")
        elif cmd_id == Commands.CURSOR_DOWN:
            return await self._device.send_key("Down")
        elif cmd_id == Commands.CURSOR_LEFT:
            return await self._device.send_key("Left")
        elif cmd_id == Commands.CURSOR_RIGHT:
            return await self._device.send_key("Right")
        elif cmd_id == Commands.CURSOR_ENTER:
            return await self._device.send_key("Confirm")
        elif cmd_id == Commands.BACK:
            return await self._device.send_key("Return")
        elif cmd_id == Commands.MENU:
            return await self._device.send_key("TopMenu")
        elif cmd_id == Commands.CONTEXT_MENU:
            return await self._device.send_key("PopUpMenu")
        elif cmd_id == Commands.SETTINGS:
            return await self._device.send_key("Options")
        elif cmd_id == Commands.HOME:
            return await self._device.send_key("Home")
        elif cmd_id == Commands.AUDIO_TRACK:
            return await self._device.send_key("Audio")
        elif cmd_id == Commands.SUBTITLE:
            return await self._device.send_key("SubTitle")  # CLOSED_CAPTION?
        elif cmd_id == Commands.DIGIT_0:
            return await self._device.send_key("Num0")
        elif cmd_id == Commands.DIGIT_1:
            return await self._device.send_key("Num1")
        elif cmd_id == Commands.DIGIT_2:
            return await self._device.send_key("Num2")
        elif cmd_id == Commands.DIGIT_3:
            return await self._device.send_key("Num3")
        elif cmd_id == Commands.DIGIT_4:
            return await self._device.send_key("Num4")
        elif cmd_id == Commands.DIGIT_5:
            return await self._device.send_key("Num5")
        elif cmd_id == Commands.DIGIT_6:
            return await self._device.send_key("Num6")
        elif cmd_id == Commands.DIGIT_7:
            return await self._device.send_key("Num7")
        elif cmd_id == Commands.DIGIT_8:
            return await self._device.send_key("Num8")
        elif cmd_id == Commands.DIGIT_9:
            return await self._device.send_key("Num9")
        elif cmd_id == Commands.INFO:
            return await self._device.send_key("Display")
        elif cmd_id == Commands.FUNCTION_RED:
            return await self._device.send_key("Red")
        elif cmd_id == Commands.FUNCTION_BLUE:
            return await self._device.send_key("Blue")
        elif cmd_id == Commands.FUNCTION_YELLOW:
            return await self._device.send_key("Yellow")
        elif cmd_id == Commands.FUNCTION_GREEN:
            return await self._device.send_key("Green")
        elif cmd_id == Commands.NEXT:
            return await self._device.send_key("Next")
        elif cmd_id == Commands.PREVIOUS:
            return await self._device.send_key("Prev")
        elif cmd_id == Commands.VOLUME_UP:
            return await self._device.send_key("VolumeUp")
        elif cmd_id == Commands.VOLUME_DOWN:
            return await self._device.send_key("VolumeDown")
        elif cmd_id == Commands.MUTE_TOGGLE:
            return await self._device.send_key("Mute")
        elif cmd_id == "POWER":
            return await self._device.toggle()
        elif cmd_id in self.options[Options.SIMPLE_COMMANDS]:
            return await self._device.send_key(SONY_SIMPLE_COMMANDS[cmd_id])
        else:
            return StatusCodes.NOT_IMPLEMENTED

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        attributes = {}

        if Attributes.STATE in update:
            state = update[Attributes.STATE]
            attributes = self._key_update_helper(Attributes.STATE, state, attributes)

        _LOG.debug("MediaPlayer update attributes %s -> %s", update, attributes)
        return attributes

    def _key_update_helper(self, key: str, value: str | None, attributes):
        if value is None:
            return attributes

        if key in self.attributes:
            if self.attributes[key] != value:
                attributes[key] = value
        else:
            attributes[key] = value

        return attributes

