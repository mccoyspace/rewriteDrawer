from __future__ import annotations

from typing import Any

from .engine import SimulationParams, filtered_edges, simulate


def resolve_simulation_frame(export_request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    params = SimulationParams(
        seed_type=export_request["seed_type"],
        frames=export_request["frames"],
        events_per_frame=export_request["events_per_frame"],
        event_selection=export_request["event_selection"],
        random_seed=export_request["random_seed"],
        layout_iterations=export_request["layout_iterations"],
        layout_spread=export_request["layout_spread"],
        spawn_jitter=export_request["spawn_jitter"],
    )

    simulation = simulate(params)
    frame_index = min(max(export_request["frame_index"], 0), len(simulation["frames"]) - 1)
    return simulation, simulation["frames"][frame_index]


def project_frame_segments(
    frame: dict[str, Any],
    draw_mode: str,
    keep_percent: float,
    recent_window: int,
    page_width_mm: float,
    page_height_mm: float,
    margin_mm: float,
) -> dict[str, Any]:
    nodes = {int(node_id): (x, y) for node_id, x, y in frame["nodes"]}
    edges = filtered_edges(frame, draw_mode, keep_percent, recent_window)

    min_x, min_y, max_x, max_y = frame["bounds"]
    usable_width = max(page_width_mm - (margin_mm * 2), 1.0)
    usable_height = max(page_height_mm - (margin_mm * 2), 1.0)
    width = max(max_x - min_x, 1e-6)
    height = max(max_y - min_y, 1e-6)
    scale = min(usable_width / width, usable_height / height)

    offset_x = margin_mm + (usable_width - (width * scale)) / 2
    offset_y = margin_mm + (usable_height - (height * scale)) / 2

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        mapped_horizontal = offset_x + ((x - min_x) * scale)
        mapped_vertical = offset_y + ((max_y - y) * scale)
        return mapped_horizontal, mapped_vertical

    segments = []
    for edge in edges:
        start = transform(nodes[edge[0]])
        end = transform(nodes[edge[1]])
        segments.append(
            {
                "start": start,
                "end": end,
                "created_event": edge[2],
                "source": [edge[0], edge[1]],
            }
        )

    return {
        "segments": segments,
        "canvas_bounds_mm": [offset_x, offset_y, offset_x + (width * scale), offset_y + (height * scale)],
        "canvas_width_mm": page_width_mm,
        "canvas_height_mm": page_height_mm,
        "machine_bounds_mm": {
            "x_vertical": [offset_y, offset_y + (height * scale)],
            "y_horizontal": [offset_x, offset_x + (width * scale)],
        },
        "scale_factor": scale,
    }
