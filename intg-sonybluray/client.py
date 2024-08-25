#!/usr/bin/env python
# coding: utf-8
import asyncio
from functools import wraps
from typing import Callable, Concatenate, Awaitable, Any, Coroutine, TypeVar, ParamSpec

from asyncio import Lock, CancelledError
import logging
from enum import IntEnum

import ucapi.media_player
from config import DeviceInstance
from pyee import AsyncIOEventEmitter
from ucapi.media_player import Attributes
from sonyapilib.device import SonyDevice, AuthenticationResult
from const import States

_LOGGER = logging.getLogger(__name__)


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


def cmd_wrapper(
        func: Callable[Concatenate[_SonyBlurayDeviceT, _P], Awaitable[ucapi.StatusCodes | list]],
) -> Callable[Concatenate[_SonyBlurayDeviceT, _P], Coroutine[Any, Any, ucapi.StatusCodes | list]]:
    """Catch command exceptions."""

    @wraps(func)
    async def wrapper(obj: _SonyBlurayDeviceT, *args: _P.args, **kwargs: _P.kwargs) -> ucapi.StatusCodes:
        """Wrap all command methods."""
        try:
            res = await func(obj, *args, **kwargs)
            await obj.start_polling()
            if res[0] == 'error':
                return ucapi.StatusCodes.BAD_REQUEST
            return ucapi.StatusCodes.OK
        except Exception as exc:
            # If Kodi is off, we expect calls to fail.
            if obj.state == States.OFF:
                log_function = _LOGGER.debug
            else:
                log_function = _LOGGER.error
            log_function(
                "Error calling %s on entity %s: %r trying to reconnect and send the command next",
                func.__name__,
                obj.id,
                exc,
            )
            # Kodi not connected, launch a connect task but
            # don't wait more than 5 seconds, then process the command if connected
            # else returns error
            connect_task = obj.event_loop.create_task(obj.connect())
            await asyncio.sleep(0)
            try:
                async with asyncio.timeout(5):
                    await connect_task
            except asyncio.TimeoutError:
                log_function(
                    "Timeout for reconnect, command won't be sent"
                )
                pass
            else:
                if not obj._connect_error:
                    try:
                        await func(obj, *args, **kwargs)
                        return ucapi.StatusCodes.OK
                    except Exception as exc:
                        log_function(
                            "Error calling %s on entity %s: %r trying to reconnect",
                            func.__name__,
                            obj.id,
                            exc,
                        )
            return ucapi.StatusCodes.BAD_REQUEST
        except Exception as ex:
            _LOGGER.error(
                "Unknown error %s",
                func.__name__)

    return wrapper


class SonyBlurayDevice(object):
    def __init__(self, device_config: DeviceInstance, timeout=3, refresh_frequency=60):
        from datetime import timedelta
        self._id = device_config.id
        self._name = device_config.name
        self._hostname = device_config.address
        self._device_config = device_config
        self._timeout = timeout
        self.refresh_frequency = timedelta(seconds=refresh_frequency)
        self._state = States.UNKNOWN
        self._event_loop = asyncio.get_event_loop() or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._event_loop)
        self._sony_device: SonyDevice | None = None
        self._media_position = 0
        self._media_duration = 0
        self._update_task = None
        self._update_lock = Lock()

    async def connect(self):
        if self._sony_device:
            # await self._sony_device.close()
            self._sony_device = None

        self._sony_device = SonyDevice(host=self._device_config.address, app_port=self._device_config.app_port,
                                       ircc_port=self._device_config.ircc_port, dmr_port=self._device_config.dmr_port,
                                       psk=self._device_config.password_key, nickname=self._device_config.name)
        self._sony_device.pin = self._device_config.pin_code
        self._sony_device.mac = self._device_config.mac_address
        if self._device_config.pin_code is None:
            register_result = await self._sony_device.register()
            if register_result == AuthenticationResult.PIN_NEEDED:
                raise ConnectionError("PIN code needed")
        self.events.emit(Events.CONNECTED, self.id)
        await self.start_polling()

    async def disconnect(self):
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
        self._reconnect_retry = 0
        while True:
            if not self._device_config.always_on:
                if self.state == States.OFF:
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

    async def update(self):
        if self._update_lock.locked():
            return

        await self._update_lock.acquire()
        # _LOGGER.debug("Refresh Panasonic data")
        if self._sony_device is None:
            await self.connect()
        update_data = {}
        current_state = self.state

        power_status = await self._sony_device.get_power_status()
        if not power_status:
            self._state = States.OFF

        else:
            self._state = States.ON

        try:
            playback_info = await self._sony_device.get_playing_status()
            if playback_info == "PLAYING":
                self._state = States.PLAYING
            elif playback_info == "PAUSED_PLAYBACK":
                self._state = States.PAUSED
        except Exception:
            self._state = States.OFF

        if self.state != current_state:
            update_data[Attributes.STATE] = self.state

        if update_data:
            self.events.emit(
                Events.UPDATE,
                self.id,
                update_data
            )
        self._update_lock.release()

    @property
    def id(self):
        return self._id

    @property
    def state(self) -> States:
        return self._state

    @property
    def name(self):
        return self._name

    @property
    def media_duration(self):
        return self._media_duration

    @property
    def media_position(self):
        return self._media_position

    @property
    def is_on(self):
        return self.state in [States.PAUSED, States.STOPPED, States.PLAYING, States.ON]

    @cmd_wrapper
    async def send_key(self, key):
        return self._sony_device._send_command(key)

    @cmd_wrapper
    async def toggle(self):
        if not self.is_on:
            self._sony_device.power(True)
        else:
            self._sony_device.power(False)

    @cmd_wrapper
    async def turn_on(self):
        if not self.is_on:
            self._sony_device.power(True)

    @cmd_wrapper
    async def turn_off(self):
        if self.is_on:
            self._sony_device.power(False)

    @cmd_wrapper
    async def channel_up(self):
        return self._sony_device.next()

    @cmd_wrapper
    async def channel_down(self):
        return self._sony_device.prev()

    @cmd_wrapper
    async def play_pause(self):
        if self.state == States.PLAYING:
            return self._sony_device.pause()
        else:
            return self._sony_device.play()

    @cmd_wrapper
    async def play(self):
        return self._sony_device.play()

    @cmd_wrapper
    async def pause(self):
        return self._sony_device.pause()

    @cmd_wrapper
    async def stop(self):
        return self._sony_device.stop()

    @cmd_wrapper
    async def eject(self):
        return self._sony_device.eject()

    @cmd_wrapper
    async def fast_forward(self):
        return self._sony_device.forward()

    @cmd_wrapper
    async def rewind(self):
        return self._sony_device.rewind()
