from __future__ import annotations

import asyncio
import json
import math
from typing import Any
from urllib.parse import urlparse, urlunparse

import websockets

from .machine import load_machine_defaults, machine_defaults_payload
from .geometry import project_frame_segments, resolve_simulation_frame

MACHINE_DEFAULTS = load_machine_defaults()

REMOTE_DEFAULTS = {
    "program_preamble": "G21\nG90\nG54",
    "program_epilogue": "",
    "draw_feed_mm_per_min": 1800.0,
    "park_after_send": False,
    "park_x_mm": 0.0,
    "park_y_mm": 0.0,
    "enable_remote_mode_before_send": True,
    "message_pause_ms": 0,
    "receive_timeout_ms": 400,
    "preview_lines": 80,
}


def default_remote_options() -> dict[str, Any]:
    return {
        "remote_defaults": {
            **REMOTE_DEFAULTS,
            "websocket_url": MACHINE_DEFAULTS.remote_ws_url,
        },
        "machine_defaults": machine_defaults_payload(MACHINE_DEFAULTS),
    }


def build_remote_job(export_request: dict[str, Any]) -> dict[str, Any]:
    simulation, frame = resolve_simulation_frame(export_request)
    projected = project_frame_segments(
        frame=frame,
        draw_mode=export_request["draw_mode"],
        keep_percent=export_request["keep_percent"],
        recent_window=export_request["recent_window"],
        page_width_mm=export_request["page_width_mm"],
        page_height_mm=export_request["page_height_mm"],
        margin_mm=export_request["margin_mm"],
    )
    ordered_segments = _order_segments(projected["segments"])
    strokes = _segments_to_strokes(ordered_segments)

    lines = _split_commands(export_request.get("program_preamble", REMOTE_DEFAULTS["program_preamble"]))
    draw_feed = float(export_request.get("draw_feed_mm_per_min", REMOTE_DEFAULTS["draw_feed_mm_per_min"]))
    lines.append(f"F{draw_feed:.3f}")
    lines.append("M5")

    draw_distance = 0.0
    travel_distance = 0.0
    current = (0.0, 0.0)

    for stroke in strokes:
        start = stroke["start"]
        travel_distance += math.dist(current, start)
        if math.dist(current, start) > 1e-9:
            lines.append(_format_move("G0", start))
        lines.append("M3 S1")

        cursor = start
        for point in stroke["points"]:
            draw_distance += math.dist(cursor, point)
            lines.append(_format_move("G1", point))
            cursor = point

        lines.append("M5")
        current = cursor

    if export_request.get("park_after_send"):
        park_machine_x = float(export_request["park_x_mm"])
        park_machine_y = float(export_request["park_y_mm"])
        park_canvas = (park_machine_y, park_machine_x)
        if math.dist(current, park_canvas) > 1e-9:
            travel_distance += math.dist(current, park_canvas)
            lines.append(_format_move("G0", park_canvas))
            current = park_canvas

    lines.extend(_split_commands(export_request.get("program_epilogue", "")))

    line_count = len(lines)
    preview_limit = int(export_request.get("preview_lines", REMOTE_DEFAULTS["preview_lines"]))

    return {
        "lines": lines,
        "program_text": "\n".join(lines) + "\n",
        "preview_lines": lines[:preview_limit],
        "summary": {
            "frame_index": frame["frame_index"],
            "line_count": line_count,
            "stroke_count": len(strokes),
            "segment_count": len(ordered_segments),
            "draw_distance_mm": round(draw_distance, 3),
            "travel_distance_mm": round(travel_distance, 3),
            "canvas_size_mm": {
                "width": projected["canvas_width_mm"],
                "height": projected["canvas_height_mm"],
            },
            "canvas_bounds_mm": projected["canvas_bounds_mm"],
            "machine_bounds_mm": projected["machine_bounds_mm"],
            "machine_axes": {
                "x": "vertical",
                "y": "horizontal",
            },
            "unit_assumption": "1 canvas unit = 1 mm",
            "server_protocol": "GRBL plotter JSON websocket protocol on /ws using toggle_remote_mode and external_command messages.",
            "simulation_settings": simulation["settings"],
        },
    }


async def send_remote_job(
    websocket_url: str,
    lines: list[str],
    enable_remote_mode_before_send: bool = True,
    message_pause_ms: int = 0,
    receive_timeout_ms: int = 400,
) -> dict[str, Any]:
    websocket_url = _normalize_websocket_url(websocket_url)
    sent_messages = 0
    responses: list[dict[str, Any] | str] = []

    async with websockets.connect(websocket_url) as socket:
        responses.extend(await _drain_messages(socket, receive_timeout_ms))

        if enable_remote_mode_before_send:
            await _send_json(socket, {"type": "toggle_remote_mode", "enabled": True})
            sent_messages += 1
            if message_pause_ms > 0:
                await asyncio.sleep(message_pause_ms / 1000)

        for line in lines:
            await _send_json(socket, {"type": "external_command", "command": line})
            sent_messages += 1
            if message_pause_ms > 0:
                await asyncio.sleep(message_pause_ms / 1000)

        await _send_json(socket, {"type": "get_status"})
        sent_messages += 1

        responses.extend(await _drain_messages(socket, receive_timeout_ms))

    return {
        "websocket_url": websocket_url,
        "sent_messages": sent_messages,
        "responses": responses,
    }


def _split_commands(raw: str) -> list[str]:
    if not raw:
        return []

    normalized = raw.replace("\\n", "\n").replace("|", "\n").replace(";", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _format_move(command: str, point: tuple[float, float]) -> str:
    horizontal_mm, vertical_mm = point
    return f"{command} X{vertical_mm:.3f} Y{horizontal_mm:.3f}"


def _order_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = [
        {
            "start": tuple(segment["start"]),
            "end": tuple(segment["end"]),
            "created_event": segment["created_event"],
        }
        for segment in segments
    ]
    if not remaining:
        return []

    ordered: list[dict[str, Any]] = []
    cursor = (0.0, 0.0)

    while remaining:
        best_index = 0
        best_reverse = False
        best_distance = float("inf")

        for index, segment in enumerate(remaining):
            start_distance = math.dist(cursor, segment["start"])
            if start_distance < best_distance:
                best_distance = start_distance
                best_index = index
                best_reverse = False

            end_distance = math.dist(cursor, segment["end"])
            if end_distance < best_distance:
                best_distance = end_distance
                best_index = index
                best_reverse = True

        chosen = remaining.pop(best_index)
        if best_reverse:
            start = chosen["end"]
            end = chosen["start"]
            chosen = {
                **chosen,
                "start": start,
                "end": end,
            }
        ordered.append(chosen)
        cursor = chosen["end"]

    return ordered


def _segments_to_strokes(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strokes = []
    current_stroke: dict[str, Any] | None = None

    for segment in segments:
        if current_stroke is None:
            current_stroke = {"start": segment["start"], "points": [segment["end"]]}
            continue

        previous_end = current_stroke["points"][-1]
        if math.dist(previous_end, segment["start"]) <= 1e-6:
            current_stroke["points"].append(segment["end"])
            continue

        strokes.append(current_stroke)
        current_stroke = {"start": segment["start"], "points": [segment["end"]]}

    if current_stroke is not None:
        strokes.append(current_stroke)

    return strokes


def _stringify_response(message: Any) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")
    return str(message)


async def _send_json(socket: Any, payload: dict[str, Any]) -> None:
    await socket.send(json.dumps(payload))


async def _drain_messages(socket: Any, receive_timeout_ms: int) -> list[dict[str, Any] | str]:
    messages: list[dict[str, Any] | str] = []
    if receive_timeout_ms <= 0:
        return messages

    while True:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=receive_timeout_ms / 1000)
        except asyncio.TimeoutError:
            break
        except websockets.exceptions.ConnectionClosedOK:
            break
        messages.append(_decode_message(raw))
    return messages


def _decode_message(message: Any) -> dict[str, Any] | str:
    text = _stringify_response(message)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _normalize_websocket_url(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        raise ValueError("WebSocket URL is required")

    if "://" not in candidate:
        candidate = f"ws://{candidate}"
    elif candidate.startswith("http://"):
        candidate = "ws://" + candidate[len("http://") :]
    elif candidate.startswith("https://"):
        candidate = "wss://" + candidate[len("https://") :]

    parsed = urlparse(candidate)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("WebSocket URL must start with ws://, wss://, http://, or https://")

    path = parsed.path or ""
    if not path or path == "/":
        path = "/ws"

    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))
