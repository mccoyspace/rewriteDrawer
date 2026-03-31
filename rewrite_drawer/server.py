from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import default_options, simulate
from .exporter import export_artifacts
from .machine import load_machine_defaults
from .remote import build_remote_job, default_remote_options, send_remote_job

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "rewrite_drawer" / "static"
EXPORTS_DIR = ROOT / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)
MACHINE_DEFAULTS = load_machine_defaults()

app = FastAPI(title="Rewrite Drawer")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/exports", StaticFiles(directory=EXPORTS_DIR), name="exports")


class SimulationRequest(BaseModel):
    seed_type: str = Field(default="path3")
    frames: int = Field(default=24, ge=1, le=120)
    events_per_frame: int = Field(default=30, ge=1, le=500)
    event_selection: str = Field(default="random")
    random_seed: int = Field(default=13, ge=0, le=9999999)
    layout_iterations: int = Field(default=60, ge=1, le=300)
    layout_spread: float = Field(default=1.15, ge=0.1, le=5.0)
    spawn_jitter: float = Field(default=0.08, ge=0.0, le=1.0)


class ExportRequest(SimulationRequest):
    frame_index: int = Field(default=0, ge=0)
    draw_mode: str = Field(default="all")
    keep_percent: float = Field(default=65.0, ge=1.0, le=100.0)
    recent_window: int = Field(default=150, ge=1, le=100000)
    page_width_mm: float = Field(default=MACHINE_DEFAULTS.canvas_width_mm, ge=10.0, le=5000.0)
    page_height_mm: float = Field(default=MACHINE_DEFAULTS.canvas_height_mm, ge=10.0, le=5000.0)
    margin_mm: float = Field(default=30.0, ge=0.0, le=500.0)
    stroke_width_mm: float = Field(default=0.5, ge=0.01, le=20.0)
    gcode_profile: str = Field(default="")
    program_preamble: str = Field(default="G21\nG90\nG54")
    program_epilogue: str = Field(default="")
    draw_feed_mm_per_min: float = Field(default=1800.0, ge=1.0, le=50000.0)
    park_after_send: bool = Field(default=False)
    park_x_mm: float = Field(default=0.0, ge=-5000.0, le=5000.0)
    park_y_mm: float = Field(default=0.0, ge=-5000.0, le=5000.0)


class RemoteJobRequest(ExportRequest):
    preview_lines: int = Field(default=80, ge=1, le=500)


class RemoteSendRequest(RemoteJobRequest):
    websocket_url: str = Field(min_length=3)
    enable_remote_mode_before_send: bool = Field(default=True)
    message_pause_ms: int = Field(default=0, ge=0, le=5000)
    receive_timeout_ms: int = Field(default=400, ge=0, le=10000)


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/options")
async def options() -> dict:
    return {
        **default_options(),
        **default_remote_options(),
    }


@app.post("/api/simulate")
async def simulate_endpoint(request: SimulationRequest) -> dict:
    try:
        return simulate(params=request_to_params(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/export")
async def export_endpoint(request: ExportRequest) -> dict:
    try:
        return export_artifacts(request.model_dump(), EXPORTS_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/remote/preview")
async def remote_preview_endpoint(request: RemoteJobRequest) -> dict:
    try:
        return build_remote_job(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/remote/send")
async def remote_send_endpoint(request: RemoteSendRequest) -> dict:
    try:
        payload = request.model_dump()
        job = build_remote_job(payload)
        send_result = await send_remote_job(
            websocket_url=payload["websocket_url"],
            lines=job["lines"],
            enable_remote_mode_before_send=payload["enable_remote_mode_before_send"],
            message_pause_ms=payload["message_pause_ms"],
            receive_timeout_ms=payload["receive_timeout_ms"],
        )
        return {
            "summary": job["summary"],
            "preview_lines": job["preview_lines"],
            **send_result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Remote websocket connection failed: {exc}") from exc


def request_to_params(request: SimulationRequest):
    from .engine import SimulationParams

    return SimulationParams(**request.model_dump())


def run() -> None:
    uvicorn.run("rewrite_drawer.server:app", host="127.0.0.1", port=MACHINE_DEFAULTS.local_app_port, reload=False)
