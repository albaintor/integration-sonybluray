"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from ucapi import EntityTypes, MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, Options

from client import SonyBlurayDevice
from config import DeviceInstance, create_entity_id
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
            Features.MUTE_TOGGLE,
        ]
        attributes = {
            Attributes.STATE: device.state,
        }

        options = {Options.SIMPLE_COMMANDS: list(SONY_SIMPLE_COMMANDS.keys())}
        # pylint: disable=R0801
        super().__init__(
            entity_id,
            config_device.name,
            features,
            attributes,
            device_class=DeviceClasses.STREAMING_BOX,
            options=options,
        )

    # pylint: disable=R0801,R0911
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
        if cmd_id == Commands.ON:
            return await self._device.turn_on()
        if cmd_id == Commands.OFF:
            return await self._device.turn_off()
        if cmd_id == Commands.TOGGLE:
            return await self._device.toggle()
        if cmd_id == Commands.CHANNEL_UP:
            return await self._device.channel_up()
        if cmd_id == Commands.CHANNEL_DOWN:
            return await self._device.channel_down()
        if cmd_id == Commands.PLAY_PAUSE:
            return await self._device.play_pause()
        if cmd_id == Commands.STOP:
            return await self._device.stop()
        if cmd_id == Commands.EJECT:
            return await self._device.eject()
        if cmd_id == Commands.FAST_FORWARD:
            return await self._device.fast_forward()
        if cmd_id == Commands.REWIND:
            return await self._device.rewind()
        if cmd_id == Commands.CURSOR_UP:
            return await self._device.send_key("Up")
        if cmd_id == Commands.CURSOR_DOWN:
            return await self._device.send_key("Down")
        if cmd_id == Commands.CURSOR_LEFT:
            return await self._device.send_key("Left")
        if cmd_id == Commands.CURSOR_RIGHT:
            return await self._device.send_key("Right")
        if cmd_id == Commands.CURSOR_ENTER:
            return await self._device.send_key("Confirm")
        if cmd_id == Commands.BACK:
            return await self._device.send_key("Return")
        if cmd_id == Commands.MENU:
            return await self._device.send_key("TopMenu")
        if cmd_id == Commands.CONTEXT_MENU:
            return await self._device.send_key("PopUpMenu")
        if cmd_id == Commands.SETTINGS:
            return await self._device.send_key("Options")
        if cmd_id == Commands.HOME:
            return await self._device.send_key("Home")
        if cmd_id == Commands.AUDIO_TRACK:
            return await self._device.send_key("Audio")
        if cmd_id == Commands.SUBTITLE:
            return await self._device.send_key("SubTitle")  # CLOSED_CAPTION?
        if cmd_id == Commands.DIGIT_0:
            return await self._device.send_key("Num0")
        if cmd_id == Commands.DIGIT_1:
            return await self._device.send_key("Num1")
        if cmd_id == Commands.DIGIT_2:
            return await self._device.send_key("Num2")
        if cmd_id == Commands.DIGIT_3:
            return await self._device.send_key("Num3")
        if cmd_id == Commands.DIGIT_4:
            return await self._device.send_key("Num4")
        if cmd_id == Commands.DIGIT_5:
            return await self._device.send_key("Num5")
        if cmd_id == Commands.DIGIT_6:
            return await self._device.send_key("Num6")
        if cmd_id == Commands.DIGIT_7:
            return await self._device.send_key("Num7")
        if cmd_id == Commands.DIGIT_8:
            return await self._device.send_key("Num8")
        if cmd_id == Commands.DIGIT_9:
            return await self._device.send_key("Num9")
        if cmd_id == Commands.INFO:
            return await self._device.send_key("Display")
        if cmd_id == Commands.FUNCTION_RED:
            return await self._device.send_key("Red")
        if cmd_id == Commands.FUNCTION_BLUE:
            return await self._device.send_key("Blue")
        if cmd_id == Commands.FUNCTION_YELLOW:
            return await self._device.send_key("Yellow")
        if cmd_id == Commands.FUNCTION_GREEN:
            return await self._device.send_key("Green")
        if cmd_id == Commands.NEXT:
            return await self._device.send_key("Next")
        if cmd_id == Commands.PREVIOUS:
            return await self._device.send_key("Prev")
        if cmd_id == Commands.VOLUME_UP:
            return await self._device.send_key("VolumeUp")
        if cmd_id == Commands.VOLUME_DOWN:
            return await self._device.send_key("VolumeDown")
        if cmd_id == Commands.MUTE_TOGGLE:
            return await self._device.send_key("Mute")
        if cmd_id == "POWER":
            return await self._device.toggle()
        if cmd_id in self.options[Options.SIMPLE_COMMANDS]:
            return await self._device.send_key(SONY_SIMPLE_COMMANDS[cmd_id])

        return StatusCodes.NOT_IMPLEMENTED

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        # pylint: disable=R0801
        attributes = {}

        if Attributes.STATE in update:
            state = update[Attributes.STATE]
            attributes = self._key_update_helper(Attributes.STATE, state, attributes)

        _LOG.debug("MediaPlayer update attributes %s -> %s", update, attributes)
        return attributes

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
