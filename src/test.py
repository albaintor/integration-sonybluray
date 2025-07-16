import asyncio
import logging

from discover import async_identify_sonybluray_devices
from sonyapilib.device import AuthenticationResult, SonyDevice
# flake8: noqa
# pylint: disable=all
_LOGGER = logging.getLogger(__name__)


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
    await discover()
    exit(0)
    # devices = await async_identify_sonybluray_devices()
    # for device in devices:
    #     _LOGGER.info(device.get("host"))
    # ssdp = SSDPDiscovery()
    # # devices = ssdp.discover(timeout=5)
    # devices = ssdp.discover()
    # print(devices)
    _device_config = {
        "id": "38-18-4c-31-5a-45",
        "name": "Sony UBP-X700",
        "client_name": "Damien-PC",
        "address": "192.168.1.117",
        "always_on": False,
        "password_key": "",
        "app_port": 50202,
        "dmr_port": 52323,
        "ircc_port": 50001,
        "mac_address": "38-18-4c-31-5a-45",
        "pin_code": "4624",
    }
    _sony_device = SonyDevice(
        host=_device_config.get("address"),
        app_port=_device_config.get("app_port"),
        ircc_port=_device_config.get("ircc_port"),
        dmr_port=_device_config.get("dmr_port"),
        psk=_device_config.get("password_key"),
        nickname=_device_config.get("client_name"),
    )
    _sony_device.pin = _device_config.get("pin_code")
    _sony_device.mac = _device_config.get("mac_address")
    try:
        await _sony_device.init_device()
    except Exception:
        print("Exception")
    register_result = await _sony_device.register()
    if register_result == AuthenticationResult.PIN_NEEDED:
        print("PIN NEEDED")
    else:
        print("NO PIN NEEDED")
    status = await _sony_device.get_power_status(timeout=2)
    if status:
        print("ON")
    else:
        print("OFF")
    # _sony_device.power(True)


if __name__ == "__main__":
    asyncio.run(main())
