from pathlib import Path

path = Path("src/playback_probe.py")
text = path.read_text()

text = text.replace(
    'AVTRANSPORT_ACTIONS = ("GetTransportInfo", "GetPositionInfo", "GetMediaInfo")\n',
    'AVTRANSPORT_ACTIONS = ("GetTransportInfo", "GetPositionInfo", "GetMediaInfo")\n'
    'EXTRA_CERS_ACTIONS = ("getHistoryList", "getText")\n',
)

anchor = '''async def read_cers_content(device: SonyDevice) -> tuple[str | None, str | None]:
    """Read the optional CERS content metadata response."""
    action = device.actions.get("getContentInformation")
    if action is None or action.url is None:
        return None, "getContentInformation not advertised"
    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        return response, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"{type(exc).__name__}: {exc}"


'''
addition = anchor + '''async def read_cers_action(device: SonyDevice, action_name: str) -> tuple[str | None, str | None]:
    """Read one optional CERS action without interpreting its firmware-specific payload."""
    action = device.actions.get(action_name)
    if action is None or action.url is None:
        return None, f"{action_name} not advertised"
    try:
        response = await device._send_http(action.url, method=HttpMethod.GET, raise_errors=True)
        return response, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"{type(exc).__name__}: {exc}"


'''
if anchor not in text:
    raise SystemExit("read_cers_content anchor not found")
text = text.replace(anchor, addition)

anchor = '''    print()

    previous_position: int | None = None
'''
addition = '''    print()

    for action_name in EXTRA_CERS_ACTIONS:
        action_response, action_error = await read_cers_action(device, action_name)
        if action_error:
            print(f"{action_name}: {action_error}")
            continue
        print(f"{action_name} fields:")
        summary = summarize_xml(action_response)
        for record in summary:
            print(f"  {record}")
        if args.raw and action_response:
            print(f"--- RAW {action_name} ---")
            print(action_response)
            print(f"--- END RAW {action_name} ---")
        print()

    previous_position: int | None = None
'''
if anchor not in text:
    raise SystemExit("run_probe anchor not found")
text = text.replace(anchor, addition)

path.write_text(text)
