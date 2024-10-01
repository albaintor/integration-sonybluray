"""
Setup flow for Sony Bluray integration.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import socket
from enum import IntEnum

from sonyapilib.device import SonyDevice, AuthenticationResult

import config
from discover import async_identify_sonybluray_devices
from config import DeviceInstance
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)

from const import IRCC_PORT, DMR_PORT, APP_PORT

_LOG = logging.getLogger(__name__)


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    CONFIGURATION_MODE = 1
    DISCOVER = 2
    DEVICE_CHOICE = 3
    PAIRING_MODE = 4


_setup_step = SetupSteps.INIT
_discovered_devices: list[dict] = []
_cfg_add_device: bool = False
_sony_device: SonyDevice | None = None
_device_name = "Sony Bluray"
_always_on = False
_polling = False
_client_name = "Sony Bluray"
_user_input_discovery = RequestUserInput(
    {"en": "Setup mode", "de": "Setup Modus"},
    [
        {
            "field": {"text": {"value": ""}},
            "id": "address",
            "label": {"en": "Address", "de": "Adresse", "fr": "Adresse"},
        },
        {
            "id": "info",
            "label": {"en": ""},
            "field": {
                "label": {
                    "value": {
                        "en": "Leave blank to use auto-discovery.",
                        "de": "Leer lassen, um automatische Erkennung zu verwenden.",
                        "fr": "Laissez le champ vide pour utiliser la découverte automatique.",
                    }
                }
            },
        },
    ],
)


async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Dispatch driver setup requests to corresponding handlers.

    Either start the setup process or handle the selected AVR device.

    :param msg: the setup driver request object, either DriverSetupRequest or UserDataResponse
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        _cfg_add_device = False
        return await handle_driver_setup(msg)
    if isinstance(msg, UserDataResponse):
        _LOG.debug(msg)
        if _setup_step == SetupSteps.CONFIGURATION_MODE and "action" in msg.input_values:
            return await handle_configuration_mode(msg)
        if _setup_step == SetupSteps.DISCOVER and "address" in msg.input_values:
            return await _handle_discovery(msg)
        if _setup_step == SetupSteps.DEVICE_CHOICE and "choice" in msg.input_values:
            return await handle_device_choice(msg)
        if _setup_step == SetupSteps.PAIRING_MODE:
            return await handle_pairing(msg)
        _LOG.error("No or invalid user response was received: %s", msg)
    elif isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup was aborted with code: %s", msg.error)
        _setup_step = SetupSteps.INIT

    # user confirmation not used in setup process
    # if isinstance(msg, UserConfirmationResponse):
    #     return handle_user_confirmation(msg)

    return SetupError()


async def handle_driver_setup(_msg: DriverSetupRequest) -> RequestUserInput | SetupError:
    """
    Start driver setup.

    Initiated by Remote Two to set up the driver.
    Ask user to enter ip-address for manual configuration, otherwise auto-discovery is used.

    :param _msg: not used, we don't have any input fields in the first setup screen.
    :return: the setup action on how to continue
    """
    global _setup_step

    reconfigure = _msg.reconfigure
    _LOG.debug("Starting driver setup, reconfigure=%s", reconfigure)
    if reconfigure:
        _setup_step = SetupSteps.CONFIGURATION_MODE

        # get all configured devices for the user to choose from
        dropdown_devices = []
        for device in config.devices.all():
            dropdown_devices.append({"id": device.id, "label": {"en": f"{device.name} ({device.id})"}})

        # TODO #12 externalize language texts
        # build user actions, based on available devices
        dropdown_actions = [
            {
                "id": "add",
                "label": {
                    "en": "Add a new device",
                    "de": "Neues Gerät hinzufügen",
                    "fr": "Ajouter un nouvel appareil",
                },
            },
        ]

        # add remove & reset actions if there's at least one configured device
        if dropdown_devices:
            dropdown_actions.append(
                {
                    "id": "remove",
                    "label": {
                        "en": "Delete selected device",
                        "de": "Selektiertes Gerät löschen",
                        "fr": "Supprimer l'appareil sélectionné",
                    },
                },
            )
            dropdown_actions.append(
                {
                    "id": "reset",
                    "label": {
                        "en": "Reset configuration and reconfigure",
                        "de": "Konfiguration zurücksetzen und neu konfigurieren",
                        "fr": "Réinitialiser la configuration et reconfigurer",
                    },
                },
            )
        else:
            # dummy entry if no devices are available
            dropdown_devices.append({"id": "", "label": {"en": "---"}})

        return RequestUserInput(
            {"en": "Configuration mode", "de": "Konfigurations-Modus"},
            [
                {
                    "field": {"dropdown": {"value": dropdown_devices[0]["id"], "items": dropdown_devices}},
                    "id": "choice",
                    "label": {
                        "en": "Configured devices",
                        "de": "Konfigurierte Geräte",
                        "fr": "Appareils configurés",
                    },
                },
                {
                    "field": {"dropdown": {"value": dropdown_actions[0]["id"], "items": dropdown_actions}},
                    "id": "action",
                    "label": {
                        "en": "Action",
                        "de": "Aktion",
                        "fr": "Appareils configurés",
                    },
                },
            ],
        )

    # Initial setup, make sure we have a clean configuration
    config.devices.clear()  # triggers device instance removal
    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def handle_configuration_mode(msg: UserDataResponse) -> RequestUserInput | SetupComplete | SetupError:
    """
    Process user data response in a setup process.

    If ``address`` field is set by the user: try connecting to device and retrieve model information.
    Otherwise, start Android TV discovery and present the found devices to the user to choose from.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device

    action = msg.input_values["action"]

    # workaround for web-configurator not picking up first response
    await asyncio.sleep(1)

    match action:
        case "add":
            _cfg_add_device = True
        case "remove":
            choice = msg.input_values["choice"]
            if not config.devices.remove(choice):
                _LOG.warning("Could not remove device from configuration: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            config.devices.store()
            return SetupComplete()
        case "reset":
            config.devices.clear()  # triggers device instance removal
        case _:
            _LOG.error("Invalid configuration action: %s", action)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def _handle_discovery(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data response in a setup process.

    If ``address`` field is set by the user: try connecting to device and retrieve model information.
    Otherwise, start discovery and present the found devices to the user to choose from.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue
    """
    global _setup_step
    global _discovered_devices

    _discovered_devices = []

    dropdown_items = []
    address = msg.input_values["address"]

    if address:
        _LOG.debug("Starting manual driver setup for %s", address)
        dropdown_items.append({"id": address, "label": {"en": f"Sony [{address}]"}})
    else:
        _LOG.debug("Starting auto-discovery driver setup")
        devices = await async_identify_sonybluray_devices()
        _LOG.debug("Discovered Sony devices %s", devices)
        _discovered_devices = devices
        for device in devices:
            avr_data = {
                "id": device.get("host"),
                "label": {"en": f"{device.get('manufacturer')} {device.get('friendlyName')} [{device.get('host')}]"},
            }
            dropdown_items.append(avr_data)

    if not dropdown_items:
        _LOG.warning("No Sony device found")
        return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    _setup_step = SetupSteps.DEVICE_CHOICE
    return RequestUserInput(
        {
            "en": "Please choose your Sony device",
            "fr": "Sélectionnez votre lecteur Sony",
        },
        [
            {
                "field": {"dropdown": {"value": dropdown_items[0]["id"], "items": dropdown_items}},
                "id": "choice",
                "label": {
                    "en": "Please choose your Sony device",
                    "fr": "Sélectionnez votre lecteur Sony",
                },
            },
            {
                "id": "ircc_port",
                "label": {
                    "en": "IRCC port number",
                    "fr": "Numéro de port IRCC",
                },
                "field": {
                    "number": {"value": IRCC_PORT, "min": 1, "max": 65535, "steps": 1, "decimals": 0}
                },
            },
            {
                "id": "dmr_port",
                "label": {
                    "en": "DMR port number",
                    "fr": "Numéro de port DMR",
                },
                "field": {
                    "number": {"value": DMR_PORT, "min": 1, "max": 65535, "steps": 1, "decimals": 0}
                },
            },
            {
                "id": "app_port",
                "label": {
                    "en": "Application port number",
                    "fr": "Numéro de port application",
                },
                "field": {
                    "number": {"value": APP_PORT, "min": 1, "max": 65535, "steps": 1, "decimals": 0}
                },
            },
            {
                "field": {"text": {"value": ""}},
                "id": "password_key",
                "label": {"en": "Password key (leave blank if unknown)",
                          "fr": "Clé du mot de passe (laisser vide si inconnu)"},
            },
            {
                "id": "always_on",
                "label": {
                    "en": "Keep connection alive (faster initialization, but consumes more battery)",
                    "fr": "Conserver la connexion active (lancement plus rapide, mais consomme plus de batterie)",
                },
                "field": {"checkbox": {"value": False}},
            },
            {
                "id": "polling",
                "label": {
                    "en": "Enable polling of media state (stopped/playing) (consumes more battery)",
                    "fr": "Activer la mise à jour du statut de lecture (consomme plus de batterie)",
                },
                "field": {"checkbox": {"value": False}},
            },
        ],
    )


async def handle_device_choice(msg: UserDataResponse) -> RequestUserInput | SetupComplete | SetupError:
    """
    Process user data response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue: SetupComplete if a valid AVR device was chosen.
    """
    global _discovered_devices
    global _sony_device
    global _always_on
    global _setup_step
    global _device_name
    global _client_name
    global _polling

    _host = msg.input_values["choice"]
    _password_key = msg.input_values.get("password_key", None)
    _always_on = msg.input_values.get("always_on") == "true"
    _polling = msg.input_values.get("polling") == "true"

    try:
        _ircc_port = int(msg.input_values.get("ircc_port", IRCC_PORT))
        _dmr_port = int(msg.input_values.get("dmr_port", DMR_PORT))
        _app_port = int(msg.input_values.get("app_port", APP_PORT))
    except ValueError:
        return SetupError(error_type=IntegrationSetupError.OTHER)

    _device_name = "Sony Bluray"
    if _discovered_devices:
        for _sony_device in _discovered_devices:
            if _sony_device.get('host') == _host:
                _device_name = f"Sony {_sony_device.get('friendlyName')}"

    _LOG.debug(f"Chosen Sony Bluray: {_device_name} {_host}. Trying to connect and retrieve device information...")
    try:
        # simple connection check
        _client_name = os.getenv("UC_CLIENT_NAME", socket.gethostname().split(".", 1)[0])
        if _client_name is None:
            _client_name = "Sony"
        _sony_device = SonyDevice(host=_host, nickname=_client_name, ircc_port=_ircc_port, dmr_port=_dmr_port,
                                  app_port=_app_port,
                                  psk=_password_key)

        await _sony_device.init_device()
        register_result = await _sony_device.register()
        if register_result == AuthenticationResult.PIN_NEEDED:
            _setup_step = SetupSteps.PAIRING_MODE
            return RequestUserInput(
                {
                    "en": "Please enter the displayed PIN code",
                    "fr": "Entrez le code PIN affiché à l'écran",
                },
                [
                    {
                        "field": {"text": {"value": "0000"}},
                        "id": "pin_code",
                        "label": {"en": "PIN code", "fr": "Code PIN"},
                    },
                ],
            )
        elif register_result == AuthenticationResult.ERROR:
            _LOG.error("Cannot connect the device %s", _host)
            return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)

        identifier = _sony_device.mac
        if not identifier:
            identifier = _host

    except Exception as ex:
        _LOG.error("Cannot connect to %s: %s", _host, ex)
        return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)

    assert _sony_device
    assert identifier

    unique_id = identifier

    if unique_id is None:
        _LOG.error("Could not get mac address of host %s: required to create a unique device", _host)
        return SetupError(error_type=IntegrationSetupError.OTHER)

    config.devices.add(
        DeviceInstance(id=unique_id, name=_device_name, address=_host, always_on=_always_on, mac_address=_sony_device.mac,
                       password_key=_password_key, ircc_port=_ircc_port, dmr_port=_dmr_port, app_port=_app_port,
                       pin_code=None, client_name=_client_name, polling=_polling)
    )  # triggers Sony BR instance creation
    config.devices.store()

    # AVR device connection will be triggered with subscribe_entities request

    await asyncio.sleep(1)

    _LOG.info("Setup successfully completed for %s (%s)", identifier, unique_id)
    return SetupComplete()


async def handle_pairing(msg: UserDataResponse) -> SetupComplete | SetupError:
    """
    Process user data response in a setup process.

    Driver setup callback to provide requested user data during the setup process.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue: SetupComplete if a valid AVR device was chosen.
    """
    global _discovered_devices
    global _sony_device
    global _always_on
    global _device_name
    global _client_name
    global _polling
    pin_code = msg.input_values.get("pin_code", None)

    _LOG.debug(f"Registering device with pin code: {_sony_device.host} {pin_code}...")
    try:
        if not await _sony_device.send_authentication(pin_code):
            _LOG.error("Wrong pin code, cannot connect the device %s", _sony_device.host)
            return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)
        identifier = _sony_device.mac
        if not identifier:
            identifier = _sony_device.host
    except Exception as ex:
        _LOG.error("Cannot connect to %s: %s", _sony_device.host, ex)
        return SetupError(error_type=IntegrationSetupError.CONNECTION_REFUSED)

    assert _sony_device
    assert identifier

    unique_id = identifier

    if unique_id is None:
        _LOG.error("Could not get mac address of host %s: required to create a unique device", _sony_device.host)
        return SetupError(error_type=IntegrationSetupError.OTHER)

    _LOG.error("Device registered successfully %s (%s)", _sony_device.host, _sony_device.mac)

    config.devices.add(
        DeviceInstance(id=unique_id, name=_device_name, address=_sony_device.host,
                       always_on=_always_on, mac_address=_sony_device.mac,
                       password_key=_sony_device.psk, ircc_port=_sony_device.ircc_port, dmr_port=_sony_device.dmr_port,
                       app_port=_sony_device.app_port,
                       pin_code=pin_code, client_name=_client_name, polling=_polling)
    )  # triggers Sony BR instance creation
    config.devices.store()

    # AVR device connection will be triggered with subscribe_entities request

    await asyncio.sleep(1)

    _LOG.info("Setup successfully completed for %s (%s)", identifier, unique_id)
    return SetupComplete()
