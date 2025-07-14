from datetime import timedelta

__version__ = "1.0.0"

import ucapi
from ucapi.ui import DeviceButtonMapping, Buttons, UiPage

IRCC_PORT = 50001
DMR_PORT = 52323
APP_PORT = 50202


SCAN_INTERVAL = timedelta(seconds=10)
MIN_TIME_BETWEEN_SCANS = SCAN_INTERVAL
MIN_TIME_BETWEEN_FORCED_SCANS = timedelta(seconds=1)

# Known key commands
KEYS = ['Num1',
        'Num2',
        'Num3',
        'Num4',
        'Num5',
        'Num6',
        'Num7',
        'Num8',
        'Num9',
        'Num0',
        'Power',
        'Eject',
        'Stop',
        'Pause',
        'Play',
        'Rewind',
        'Forward',
        'PopUpMenu',
        'TopMenu',
        'Up',
        'Down',
        'Left',
        'Right',
        'Confirm',
        'Options',
        'Display',
        'Home',
        'Return',
        'Karaoke',
        'Netflix',
        'Mode3D',
        'Next',
        'Prev',
        'Favorites',
        'SubTitle',
        'Audio',
        'Angle',
        'Blue',
        'Red',
        'Green',
        'Yellow',
        'Advance',
        'Replay',
        'Mute',
        'VolumeUp',
        'VolumeDown'
        ]

SONY_SIMPLE_COMMANDS = {
    "MENU_HOME": "Home",
    "MENU_FAVORITES": "Favorites",
    "MODE_ANGLE": "Angle",
    "MODE_ADVANCE": "Advance",
    "MENU_REPLAY": "Replay"
}


SONY_REMOTE_BUTTONS_MAPPING: [DeviceButtonMapping] = [
    {"button": Buttons.BACK, "short_press": {"cmd_id": "Return"}},
    {"button": Buttons.HOME, "short_press": {"cmd_id": "Home"}},
    {"button": Buttons.CHANNEL_DOWN, "short_press": {"cmd_id": "Prev"}},
    {"button": Buttons.CHANNEL_UP, "short_press": {"cmd_id": "Next"}},
    {"button": Buttons.DPAD_UP, "short_press": {"cmd_id": "Up"}},
    {"button": Buttons.DPAD_DOWN, "short_press": {"cmd_id": "Down"}},
    {"button": Buttons.DPAD_LEFT, "short_press": {"cmd_id": "Left"}},
    {"button": Buttons.DPAD_RIGHT, "short_press": {"cmd_id": "Right"}},
    {"button": Buttons.DPAD_MIDDLE, "short_press": {"cmd_id": "Confirm"}},
    {"button": Buttons.PLAY, "short_press": {"cmd_id": "Pause"}},
    {"button": Buttons.PREV, "short_press": {"cmd_id": "Rewind"}},
    {"button": Buttons.NEXT, "short_press": {"cmd_id": "Forward"}},
    {"button": Buttons.VOLUME_UP, "short_press": {"cmd_id": "VolumeUp"}},
    {"button": Buttons.VOLUME_DOWN, "short_press": {"cmd_id": "VolumeDown"}},
    {"button": Buttons.MUTE, "short_press": {"cmd_id": "Mute"}},
]

SONY_REMOTE_UI_PAGES: [UiPage] = [
    {
        "page_id": "Sony commands",
        "name": "Sony commands",
        "grid": {"width": 4, "height": 6},
        "items": [
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "POWER", "repeat": 1}
                },
                "icon": "uc:power-on",
                "location": {
                    "x": 0,
                    "y": 0
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "Display", "repeat": 1}
                },
                "icon": "uc:info",
                "location": {
                    "x": 1,
                    "y": 0
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "Audio", "repeat": 1}
                },
                "icon": "uc:language",
                "location": {
                    "x": 2,
                    "y": 0
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "SubTitle", "repeat": 1}
                },
                "icon": "uc:cc",
                "location": {
                    "x": 3,
                    "y": 0
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "Mode3D", "repeat": 1}
                },
                "text": "3D",
                "location": {
                    "x": 2,
                    "y": 1
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "text"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "Stop", "repeat": 1}
                },
                "icon": "uc:stop",
                "location": {
                    "x": 0,
                    "y": 1
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "Eject", "repeat": 1}
                },
                "text": "Eject",
                "location": {
                    "x": 1,
                    "y": 1
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "text"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "TopMenu", "repeat": 1}
                },
                "text": "Title",
                "location": {
                    "x": 0,
                    "y": 2
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "text"
            },
            {
                "command": {
                    "cmd_id": "remote.send",
                    "params": {"command": "PopUpMenu", "repeat": 1}
                },
                "icon": "uc:menu",
                "location": {
                    "x": 3,
                    "y": 5
                },
                "size": {
                    "height": 1,
                    "width": 1
                },
                "type": "icon"
            },
        ]
    },
    {
        "page_id": "Sony numbers",
        "name": "Sony numbers",
        "grid": {"height": 4, "width": 3},
        "items": [{
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num1", "repeat": 1}
            },
            "location": {
                "x": 0,
                "y": 0
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "1",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num2", "repeat": 1}
            },
            "location": {
                "x": 1,
                "y": 0
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "2",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num3", "repeat": 1}
            },
            "location": {
                "x": 2,
                "y": 0
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "3",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num4", "repeat": 1}
            },
            "location": {
                "x": 0,
                "y": 1
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "4",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num5", "repeat": 1}
            },
            "location": {
                "x": 1,
                "y": 1
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "5",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num6", "repeat": 1}
            },
            "location": {
                "x": 2,
                "y": 1
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "6",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num7", "repeat": 1}
            },
            "location": {
                "x": 0,
                "y": 2
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "7",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num8", "repeat": 1}
            },
            "location": {
                "x": 1,
                "y": 2
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "8",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num9", "repeat": 1}
            },
            "location": {
                "x": 2,
                "y": 2
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "9",
            "type": "text"
        }, {
            "command": {
                "cmd_id": "remote.send",
                "params": {"command": "Num0", "repeat": 1}
            },
            "location": {
                "x": 1,
                "y": 3
            },
            "size": {
                "height": 1,
                "width": 1
            },
            "text": "0",
            "type": "text"
        }
        ]
    }
]
