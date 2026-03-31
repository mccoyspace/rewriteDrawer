from __future__ import annotations

import json
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import DRAW_MODES, GCODE_PROFILES
from .geometry import project_frame_segments, resolve_simulation_frame
from .remote import build_remote_job


def export_artifacts(
    export_request: dict[str, Any],
    export_root: Path,
) -> dict[str, Any]:
    simulation, frame = resolve_simulation_frame(export_request)
    frame_index = frame["frame_index"]
    draw_mode = export_request["draw_mode"]
    keep_percent = export_request["keep_percent"]
    recent_window = export_request["recent_window"]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder_name = f"{timestamp}-{export_request['seed_type']}-f{frame_index:02d}"
    output_dir = export_root / folder_name
    output_dir.mkdir(parents=True, exist_ok=False)

    raw_svg_path = output_dir / "raw.svg"
    optimized_svg_path = output_dir / "optimized.svg"
    gcode_path = output_dir / "drawing.gcode"
    remote_gcode_path = output_dir / "remote-vanilla.gcode"
    metadata_path = output_dir / "metadata.json"

    svg_markup = _render_svg(
        frame=frame,
        draw_mode=draw_mode,
        keep_percent=keep_percent,
        recent_window=recent_window,
        page_width_mm=export_request["page_width_mm"],
        page_height_mm=export_request["page_height_mm"],
        margin_mm=export_request["margin_mm"],
        stroke_width_mm=export_request["stroke_width_mm"],
    )
    raw_svg_path.write_text(svg_markup, encoding="utf-8")

    remote_job = build_remote_job(export_request)
    remote_gcode_path.write_text(remote_job["program_text"], encoding="utf-8")

    messages = []
    optimized_ok = False
    gcode_ok = False

    optimize_result = subprocess.run(
        [
            "vpype",
            "read",
            str(raw_svg_path.resolve()),
            "linemerge",
            "linesort",
            "reloop",
            "write",
            str(optimized_svg_path.resolve()),
        ],
        cwd=output_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    if optimize_result.returncode == 0:
        optimized_ok = True
    else:
        messages.append(optimize_result.stderr.strip() or "vpype optimization failed")

    profile = export_request["gcode_profile"]
    if profile:
        source_path = optimized_svg_path if optimized_ok else raw_svg_path
        gcode_result = subprocess.run(
            [
                "vpype",
                "read",
                str(source_path.resolve()),
                "gwrite",
                "-p",
                profile,
                str(gcode_path.resolve()),
            ],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        if gcode_result.returncode == 0:
            remap_messages = _remap_vpype_gcode_for_machine(
                gcode_path=gcode_path,
                profile_name=profile,
                canvas_width_mm=export_request["page_width_mm"],
                canvas_height_mm=export_request["page_height_mm"],
            )
            messages.extend(remap_messages)
            gcode_ok = True
        else:
            messages.append(gcode_result.stderr.strip() or "vpype gwrite failed")

    metadata = {
        "simulation_settings": simulation["settings"],
        "export_request": export_request,
        "frame_summary": {
            "frame_index": frame["frame_index"],
            "event_count": frame["event_count"],
            "node_count": frame["node_count"],
            "edge_count": frame["edge_count"],
            "duplicate_edges": frame["duplicate_edges"],
        },
        "remote_job_summary": remote_job["summary"],
        "unit_assumption": "1 canvas unit = 1 mm",
        "draw_mode_label": DRAW_MODES.get(draw_mode, draw_mode),
        "gcode_profile_label": GCODE_PROFILES.get(profile, profile),
        "messages": messages,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    files = [
        {
            "label": "Raw SVG",
            "path": f"/exports/{folder_name}/raw.svg",
        },
        {
            "label": "Metadata",
            "path": f"/exports/{folder_name}/metadata.json",
        },
        {
            "label": "Machine Remote G-code",
            "path": f"/exports/{folder_name}/remote-vanilla.gcode",
        },
    ]

    if optimized_ok:
        files.insert(
            1,
            {
                "label": "Optimized SVG",
                "path": f"/exports/{folder_name}/optimized.svg",
            },
        )

    if gcode_ok:
        files.append(
            {
                "label": "Machine Plotter G-code",
                "path": f"/exports/{folder_name}/drawing.gcode",
            }
        )

    return {
        "folder": f"/exports/{folder_name}/",
        "files": files,
        "messages": messages,
    }


def _render_svg(
    frame: dict[str, Any],
    draw_mode: str,
    keep_percent: float,
    recent_window: int,
    page_width_mm: float,
    page_height_mm: float,
    margin_mm: float,
    stroke_width_mm: float,
) -> str:
    projected = project_frame_segments(
        frame=frame,
        draw_mode=draw_mode,
        keep_percent=keep_percent,
        recent_window=recent_window,
        page_width_mm=page_width_mm,
        page_height_mm=page_height_mm,
        margin_mm=margin_mm,
    )
    path_elements = []
    for segment in projected["segments"]:
        start = segment["start"]
        end = segment["end"]
        path_elements.append(
            f'<path d="M {start[0]:.3f} {start[1]:.3f} L {end[0]:.3f} {end[1]:.3f}" />'
        )

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width_mm}mm" height="{page_height_mm}mm" '
        f'viewBox="0 0 {page_width_mm} {page_height_mm}">\n'
        f'  <g fill="none" stroke="#142c73" stroke-width="{stroke_width_mm}" '
        f'stroke-linecap="round" stroke-linejoin="round">\n'
        f'    {" ".join(path_elements)}\n'
        f"  </g>\n"
        f"</svg>\n"
    )


AXIS_WORD_RE = re.compile(r"([XY])\s*([-+]?\d*\.?\d+)")


def _remap_vpype_gcode_for_machine(
    gcode_path: Path,
    profile_name: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> list[str]:
    profile = _load_vpype_profile(profile_name)
    if profile is None:
        return [f"Axis remap skipped: vpype profile '{profile_name}' was not found in bundled or ~/.vpype.toml config."]

    unit = str(profile.get("unit", "mm")).strip().lower()
    unit_scale = 25.4 if unit in {"in", "inch", "inches"} else 1.0
    vertical_flip = bool(profile.get("vertical_flip", False))
    uses_relative = _profile_uses_relative_coordinates(profile)
    page_height_units = canvas_height_mm / unit_scale

    current_x = 0.0
    current_y = 0.0
    remapped_lines = []

    for raw_line in gcode_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\n")
        matches = list(AXIS_WORD_RE.finditer(line))

        if not matches:
            remapped_lines.append(line)
            continue

        coords = {match.group(1).upper(): float(match.group(2)) for match in matches}

        if uses_relative:
            old_dx = coords.get("X", 0.0)
            old_dy = coords.get("Y", 0.0)
            if vertical_flip:
                new_x = -old_dy
                new_y = old_dx
            else:
                new_x = old_dy
                new_y = old_dx
        else:
            old_x = coords.get("X", current_x)
            old_y = coords.get("Y", current_y)
            if vertical_flip:
                new_x = page_height_units - old_y
                new_y = old_x
            else:
                new_x = old_y
                new_y = old_x
            current_x = old_x
            current_y = old_y

        remapped_lines.append(_replace_xy_words(line, new_x, new_y))

    gcode_path.write_text("\n".join(remapped_lines) + "\n", encoding="utf-8")
    return []


def _profile_uses_relative_coordinates(profile: dict[str, Any]) -> bool:
    for key in ("segment_first", "segment", "segment_last", "line_start"):
        template = str(profile.get(key, ""))
        if "{dx" in template or "{dy" in template or "{idx" in template or "{idy" in template:
            return True
    document_start = str(profile.get("document_start", ""))
    return "G91" in document_start


def _load_vpype_profile(profile_name: str) -> dict[str, Any] | None:
    config: dict[str, Any] = {}
    bundled = _read_toml(_vpype_bundled_config_path())
    config.update(bundled.get("gwrite", {}))

    user_config_path = Path.home() / ".vpype.toml"
    if user_config_path.exists():
        config.update(_read_toml(user_config_path).get("gwrite", {}))

    profile = config.get(profile_name)
    if isinstance(profile, dict):
        return profile
    return None


def _vpype_bundled_config_path() -> Path:
    import vpype_gcode

    return Path(vpype_gcode.__file__).with_name("bundled_configs.toml")


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _replace_xy_words(line: str, new_x: float, new_y: float) -> str:
    comment_start = _find_comment_start(line)
    if comment_start is None:
        code_part = line
        comment_part = ""
    else:
        code_part = line[:comment_start]
        comment_part = line[comment_start:].rstrip()

    rebuilt_code = AXIS_WORD_RE.sub("", code_part).strip()
    if rebuilt_code:
        rebuilt_code = f"{rebuilt_code} X{new_x:.4f} Y{new_y:.4f}"
    else:
        rebuilt_code = f"X{new_x:.4f} Y{new_y:.4f}"

    if comment_part:
        return f"{rebuilt_code} {comment_part}"
    return rebuilt_code


def _find_comment_start(line: str) -> int | None:
    candidates = [idx for idx in (line.find("("), line.find(";")) if idx >= 0]
    if not candidates:
        return None
    return min(candidates)
