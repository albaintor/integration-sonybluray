# pylint: skip-file
# flake8: noqa
import asyncio
import logging
import sys
from typing import Any

from rich import print_json

from client import SonyBlurayDevice, Events
from config import DeviceInstance
from discover import async_identify_sonybluray_devices
from sonyapilib.device import AuthenticationResult, SonyDevice

# flake8: noqa
# pylint: disable=all
_LOGGER = logging.getLogger(__name__)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

async def on_device_update(device_id: str, update: dict[str, Any] | None) -> None:
    print_json(data=update)

async def discover():
    devices = await async_identify_sonybluray_devices()
    for device in devices:
        _LOGGER.info(device)
    # ssdp = SSDPDiscovery()
    # devices = ssdp.discover(timeout=5)
    # devices = ssdp.discover()
    # print(devices)


async def main():
    logging.basicConfig(level=logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    # ch.setFormatter(formatter)
    _LOGGER.addHandler(ch)
    # await discover()
    # exit(0)
    # devices = await async_identify_sonybluray_devices()
    # for device in devices:
    #     _LOGGER.info(device.get("host"))
    # ssdp = SSDPDiscovery()
    # # devices = ssdp.discover(timeout=5)
    # devices = ssdp.discover()
    # print(devices)
    _device_config = DeviceInstance(
        id="38-18-4c-31-5a-45",
        name="Sony UBP-X700",
        client_name="Damien-PC",
        address="192.168.1.117",
        always_on=False,
        password_key="",
        app_port=50202,
        dmr_port=52323,
        ircc_port=50001,
        mac_address="38-18-4c-31-5a-45",
        pin_code="4624"
    )
    client = SonyBlurayDevice(
        device_config=_device_config
    )
    client.events.on(Events.UPDATE, on_device_update)
    await client.connect()
    await client.turn_on()
    await client.send_key("Right")
    await asyncio.sleep(100)


    # _sony_device = SonyDevice(
    #     host=_device_config.get("address"),
    #     app_port=_device_config.get("app_port"),
    #     ircc_port=_device_config.get("ircc_port"),
    #     dmr_port=_device_config.get("dmr_port"),
    #     psk=_device_config.get("password_key"),
    #     nickname=_device_config.get("client_name"),
    # )
    # _sony_device.pin = _device_config.get("pin_code")
    # _sony_device.mac = _device_config.get("mac_address")
    # try:
    #     await _sony_device.init_device()
    # except Exception:
    #     print("Exception")
    # register_result = await _sony_device.register()
    # if register_result == AuthenticationResult.PIN_NEEDED:
    #     print("PIN NEEDED")
    # else:
    #     print("NO PIN NEEDED")
    # status = await _sony_device.get_power_status(timeout=2)
    # if status:
    #     print("ON")
    # else:
    #     print("OFF")
    # _sony_device.power(True)


if __name__ == "__main__":
    _LOG = logging.getLogger(__name__)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logging.basicConfig(handlers=[ch])
    logging.getLogger("client").setLevel(logging.DEBUG)
    logging.getLogger("media_player").setLevel(logging.DEBUG)
    logging.getLogger("remote").setLevel(logging.DEBUG)
    logging.getLogger("sonyapilib.device").setLevel(logging.DEBUG)
    logging.getLogger(__name__).setLevel(logging.DEBUG)
    _LOOP.run_until_complete(main())
    _LOOP.run_forever()
    asyncio.run(main())
