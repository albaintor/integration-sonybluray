#!/usr/bin/env python
"""
Client handling of the integration driver.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from asyncio import AbstractEventLoop, CancelledError, Lock
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, Coroutine, ParamSpec, TypeVar

import ucapi.media_player
from aiohttp import ClientOSError
from pyee.asyncio import AsyncIOEventEmitter
from ucapi.media_player import Attributes, States

from config import DeviceInstance
from sonyapilib.device import AuthenticationResult, DeviceCapabilities, DeviceState, SonyDevice

_LOGGER = logging.getLogger(__name__)
ERROR_OS_WAIT = 0.5


class Events(IntEnum):
    """Internal driver events."""

    CONNECTED = 0
    ERROR = 1
    UPDATE = 2
    IP_ADDRESS_CHANGED = 3
    DISCONNECTED = 4


_SonyBlurayDeviceT = TypeVar("_SonyBlurayDeviceT", bound="SonyBlurayDevice")
_P = ParamSpec("_P")

CONNECTION_RETRIES = 10


# pylint: disable=W0212
# noqa: D202
def cmd_wrapper(
    func: Callable[Concatenate[_SonyBlurayDeviceT, _P], Awaitable[Any]],
) -> Callable[Concatenate[_SonyBlurayDeviceT, _P], Coroutine[Any, Any, ucapi.StatusCodes]]:
    """Catch command exceptions."""

    @wraps(func)
    async def wrapper(obj: _SonyBlurayDeviceT, *args: _P.args, **kwargs: _P.kwargs) -> ucapi.StatusCodes:
        """Wrap all command methods."""
        try:
            await func(obj, *args, **kwargs)
            if obj._device_config.polling:
                await obj.start_polling()
            return ucapi.StatusCodes.OK
        # pylint: disable=W0718
        except Exception as ex:
            # If device is off, we expect calls to fail.
            if obj.state == States.OFF:
                log_function = _LOGGER.debug
            else:
                log_function = _LOGGER.error
            log_function(
                "Error calling %s on entity %s: %r trying to reconnect and send the command next",
                func.__name__,
                obj.id,
                ex,
            )
            # Device not connected, launch a connect task but
            # don't wait more than 5 seconds, then process the command if connected
            # else returns error
            connect_task = obj._event_loop.create_task(obj.connect())
            await asyncio.sleep(0)
            try:
                async with asyncio.timeout(5):
                    await connect_task
            except asyncio.TimeoutError:
                log_function("Timeout for reconnect, command won't be sent")
            else:
                try:
                    await func(obj, *args, **kwargs)
                    return ucapi.StatusCodes.OK
                # pylint: disable=W0718
                except Exception as exc:
                    log_function(
                        "Error calling %s on entity %s: %r trying to reconnect",
                        func.__name__,
                        obj.id,
                        exc,
                    )
            return ucapi.StatusCodes.BAD_REQUEST

    return wrapper


class SonyBlurayDevice:
    """Sony client device."""

    def __init__(self, device_config: DeviceInstance, timeout=3, refresh_frequency=60):
        """Create device instance."""
        self._id = device_config.id
        self._name = device_config.name
        self._hostname = device_config.address
        self._device_config = device_config
        self._timeout = timeout
        self.refresh_frequency = timedelta(seconds=refresh_frequency)
        self._state = States.UNKNOWN
        if device_config.protocols:
            self._capabilities = DeviceCapabilities.from_protocols(device_config.protocols)
        else:
            self._capabilities = DeviceCapabilities.legacy_defaults()
        self._event_loop: AbstractEventLoop = asyncio.get_event_loop() or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._event_loop)
        self._sony_device: SonyDevice | None = None
        self._media_position = 0
        self._media_duration = 0
        self._media_source: str | None = None
        self._media_title: str | None = None
        self._update_task = None
        self._update_lock = Lock()
        self._connected = False
        self._reconnect_retry = 0

    async def connect(self):
        """Connect to the device."""
        if self._sony_device:
            # await self._sony_device.close()
            self._sony_device = None
            self._connected = False
            self._state = States.OFF

        if self._device_config.password_key == "":
            self._device_config.password_key = None
        sony_device = SonyDevice(
            host=self._device_config.address,
            app_port=self._device_config.app_port,
            ircc_port=self._device_config.ircc_port,
            dmr_port=self._device_config.dmr_port,
            psk=self._device_config.password_key,
            nickname=self._device_config.client_name,
        )
        self._sony_device = sony_device
        sony_device.pin = self._device_config.pin_code
        sony_device.mac = self._device_config.mac_address
        try:
            _LOGGER.debug("Init device")
            initialized = await sony_device.init_device()
            if not initialized:
                _LOGGER.debug("Sony device initialization error, retry in case where network was not ready")
                await asyncio.sleep(ERROR_OS_WAIT)
                initialized = await sony_device.init_device()

            if initialized:
                self._capabilities = sony_device.capabilities
                self._device_config.protocols = self._capabilities.protocols
                if (
                    self._capabilities.ircc
                    and self._device_config.pin_code is None
                    and "register" in sony_device.actions
                ):
                    register_result = await sony_device.register()
                    if register_result == AuthenticationResult.PIN_NEEDED:
                        raise ConnectionError("PIN code needed")
        # pylint: disable=W0718
        except Exception as ex:
            _LOGGER.debug("Sony device connection error, waiting next call %s", ex)

        self.events.emit(Events.CONNECTED, self.id)
        if self._device_config.polling:
            await self.start_polling()

    async def disconnect(self):
        """Disconnect from the device."""
        if self._sony_device:
            self._sony_device = None

    async def start_polling(self):
        """Start polling task."""
        if self._update_task is not None:
            return
        _LOGGER.debug("Start polling task for device %s", self.id)
        self._update_task = self._event_loop.create_task(self._background_update_task())

    async def stop_polling(self):
        """Stop polling task."""
        if self._update_task:
            try:
                self._update_task.cancel()
            except CancelledError:
                pass
            self._update_task = None

    async def _background_update_task(self):
        """Update data in background."""
        self._reconnect_retry = 0
        while True:
            if not self._device_config.always_on:
                if self.state in [States.OFF, States.UNKNOWN]:
                    self._reconnect_retry += 1
                    if self._reconnect_retry > CONNECTION_RETRIES:
                        _LOGGER.debug("Stopping update task as the device %s is off", self.id)
                        break
                    _LOGGER.debug("Device %s is off, retry %s", self.id, self._reconnect_retry)
                elif self._reconnect_retry > 0:
                    self._reconnect_retry = 0
                    _LOGGER.debug("Device %s is on again", self.id)
            await self.update()
            await asyncio.sleep(10)

        self._update_task = None

    async def update(self, deferred_update=0):
        # pylint: disable=too-many-statements
        """Update data."""
        if deferred_update > 0:
            await asyncio.sleep(deferred_update)
        if self._update_lock.locked():
            return

        update_data: dict[str, Any] = {}
        current_state = self.state
        current_position = self._media_position
        current_duration = self._media_duration
        current_source = self._media_source
        current_title = self._media_title

        async with self._update_lock:
            try:
                if self.state in [States.OFF, States.UNKNOWN]:
                    await self.connect()

                sony_device = self._require_device()
                power_status = await sony_device.get_power_status()
                if not power_status:
                    self._state = States.OFF
                    self._media_position = 0
                    self._media_duration = 0
                    self._media_source = None
                    self._media_title = None
                else:
                    playback_info = await sony_device.get_playback_info()
                    if playback_info.state == DeviceState.OFF:
                        self._state = States.OFF
                    elif playback_info.state == DeviceState.PLAYING:
                        self._state = States.PLAYING
                    elif playback_info.state == DeviceState.PAUSED:
                        self._state = States.PAUSED
                    else:
                        self._state = States.ON

                    self._media_position = playback_info.position or 0
                    self._media_duration = playback_info.duration or 0
                    self._media_source = playback_info.source
                    self._media_title = playback_info.title
            # pylint: disable=W0718
            except Exception as ex:
                _LOGGER.debug("Cannot update Sony device %s: %s", self.id, ex)
                self._state = States.OFF

        if self.state != current_state:
            update_data[Attributes.STATE] = self.state
        if self._media_position != current_position:
            update_data[Attributes.MEDIA_POSITION] = self._media_position
            update_data[Attributes.MEDIA_POSITION_UPDATED_AT] = datetime.now(timezone.utc).isoformat()
        if self._media_duration != current_duration:
            update_data[Attributes.MEDIA_DURATION] = self._media_duration
        if self._media_source != current_source and self._media_source is not None:
            update_data[Attributes.SOURCE] = self._media_source
        if self._media_title != current_title and self._media_title is not None:
            update_data[Attributes.MEDIA_TITLE] = self._media_title

        if update_data:
            self.events.emit(Events.UPDATE, self.id, update_data)

    @property
    def attributes(self) -> dict[str, Any]:
        """Return the device attributes."""
        updated_data = {
            Attributes.STATE: self.state,
            Attributes.MEDIA_POSITION: self.media_position,
            Attributes.MEDIA_DURATION: self.media_duration,
        }
        if self._media_source is not None:
            updated_data[Attributes.SOURCE] = self._media_source
        if self._media_title is not None:
            updated_data[Attributes.MEDIA_TITLE] = self._media_title
        return updated_data

    @property
    def id(self):
        """Return the identifier of the device."""
        return self._id

    @property
    def state(self) -> States:
        """Return the device state."""
        return self._state

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def has_media_state(self):
        """Return true if polling is enabled (state available)."""
        return self._device_config.polling and self.capabilities.media_state

    @property
    def capabilities(self) -> DeviceCapabilities:
        """Return configured or detected network capabilities."""
        return self._capabilities

    def _require_device(self) -> SonyDevice:
        """Return the active protocol client or fail with an explicit connection error."""
        if self._sony_device is None:
            raise ConnectionError("Sony device is not connected")
        return self._sony_device

    @property
    def media_duration(self):
        """Return media duration."""
        return self._media_duration

    @property
    def media_position(self):
        """Return media position."""
        return self._media_position

    @property
    def is_on(self):
        """Return True if the device is on."""
        return self.state in [States.PAUSED, States.PLAYING, States.ON]

    @cmd_wrapper
    async def send_key(self, key):
        """Send key command."""
        try:
            await self._sony_device.send_command(key)
        except ClientOSError:
            _LOGGER.warning("[%s] OS error, waiting %ss", self._device_config.address, ERROR_OS_WAIT)
            await asyncio.sleep(ERROR_OS_WAIT)
            await self._sony_device.send_command(key)

    @cmd_wrapper
    async def toggle(self):
        """Toggle device power."""
        if not self._device_config.polling:
            if self._sony_device.initialized:
                power_status = await self._sony_device.get_power_status(timeout=2)
                if not power_status:
                    await self._sony_device.power(True)
                else:
                    await self._sony_device.power(False)
            else:
                await self._sony_device.power(True)
            self._event_loop.create_task(self.update(10))
            self._event_loop.create_task(self.update(20))
            return

        if not self.is_on:
            await self._sony_device.power(True)
        else:
            await self._sony_device.power(False)

    async def _deferred_wakeonlan(self, delay: float):
        await asyncio.sleep(delay)
        self._require_device().wakeonlan()

    async def turn_on(self) -> ucapi.StatusCodes:
        """Turn on the device."""
        _LOGGER.debug("Turn on (state %s)", self.state)
        try:
            sony_device = self._require_device()
            await sony_device.power(True)
            if sony_device.mac:
                asyncio.create_task(self._deferred_wakeonlan(ERROR_OS_WAIT))
            if not self._device_config.polling:
                self._event_loop.create_task(self.update(10))
                self._event_loop.create_task(self.update(20))
            return ucapi.StatusCodes.OK
        # pylint: disable=W0718
        except Exception as ex:
            _LOGGER.debug("Error turn on %s", ex)
            return ucapi.StatusCodes.SERVER_ERROR

    @cmd_wrapper
    async def turn_off(self):
        """Turn off the device."""
        if not self._device_config.polling:
            power_status = await self._sony_device.get_power_status(timeout=2)
            if power_status:
                await self._sony_device.power(False)
            self._event_loop.create_task(self.update(10))
            return

        if self.is_on:
            await self._sony_device.power(False)

    @cmd_wrapper
    async def channel_up(self):
        """Next channel."""
        return await self._sony_device.next()

    @cmd_wrapper
    async def channel_down(self):
        """Previous channel."""
        return await self._sony_device.prev()

    @cmd_wrapper
    async def play_pause(self):
        """Toggle play/pause."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        if self.state == States.PLAYING:
            return await self._sony_device.pause()
        return await self._sony_device.play()

    @cmd_wrapper
    async def play(self):
        """Play command."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        await self._sony_device.play()

    @cmd_wrapper
    async def pause(self):
        """Pause command."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        await self._sony_device.pause()

    @cmd_wrapper
    async def stop(self):
        """Stop command."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        await self._sony_device.stop()

    @cmd_wrapper
    async def seek(self, position: int):
        """Seek DLNA playback to a position in seconds."""
        await self._sony_device.seek(position)

    @cmd_wrapper
    async def eject(self):
        """Eject command."""
        if not self._device_config.polling:
            self._event_loop.create_task(self.update())
        await self._sony_device.eject()

    @cmd_wrapper
    async def fast_forward(self):
        """Fast forward command."""
        await self._sony_device.forward()

    @cmd_wrapper
    async def rewind(self):
        """Rewind command."""
        await self._sony_device.rewind()
