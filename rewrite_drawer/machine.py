from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "gcodeServer-config.txt"


@dataclass(frozen=True)
class MachineDefaults:
    bed_x_mm: float = 2400.0
    bed_y_mm: float = 2580.0
    plotter_host: str = "127.0.0.1"
    plotter_port: int = 8000
    local_app_port: int = 8010

    @property
    def canvas_width_mm(self) -> float:
        # Horizontal span maps to machine Y on this machine.
        return self.bed_y_mm

    @property
    def canvas_height_mm(self) -> float:
        # Vertical span maps to machine X on this machine.
        return self.bed_x_mm

    @property
    def remote_ws_url(self) -> str:
        host = self.plotter_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"ws://{host}:{self.plotter_port}/ws"


def load_machine_defaults(config_path: Path = CONFIG_PATH) -> MachineDefaults:
    config = _load_key_value_file(config_path)

    return MachineDefaults(
        bed_x_mm=_float_value(config, "GRBL_PLOTTER_BED_X_MM", 2400.0),
        bed_y_mm=_float_value(config, "GRBL_PLOTTER_BED_Y_MM", 2580.0),
        plotter_host=config.get("GRBL_PLOTTER_HOST", "127.0.0.1"),
        plotter_port=_int_value(config, "GRBL_PLOTTER_PORT", 8000),
    )


def machine_defaults_payload(defaults: MachineDefaults) -> dict:
    return {
        "bed_x_mm": defaults.bed_x_mm,
        "bed_y_mm": defaults.bed_y_mm,
        "canvas_width_mm": defaults.canvas_width_mm,
        "canvas_height_mm": defaults.canvas_height_mm,
        "remote_ws_url": defaults.remote_ws_url,
        "machine_axes": {
            "x": "vertical",
            "y": "horizontal",
        },
        "canvas_axes": {
            "width": "horizontal",
            "height": "vertical",
        },
        "unit_assumption": "1 canvas unit = 1 mm",
    }


def _load_key_value_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _float_value(config: dict[str, str], key: str, fallback: float) -> float:
    try:
        return float(config.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _int_value(config: dict[str, str], key: str, fallback: int) -> int:
    try:
        return int(config.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
