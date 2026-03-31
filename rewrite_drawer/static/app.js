const state = {
  options: null,
  simulation: null,
  currentFrameIndex: 0,
  playing: false,
  playTimer: null,
  remoteJob: null,
};

const canvas = document.getElementById("previewCanvas");
const ctx = canvas.getContext("2d");

const els = {
  seedType: document.getElementById("seedType"),
  frames: document.getElementById("frames"),
  eventsPerFrame: document.getElementById("eventsPerFrame"),
  eventSelection: document.getElementById("eventSelection"),
  randomSeed: document.getElementById("randomSeed"),
  layoutIterations: document.getElementById("layoutIterations"),
  layoutSpread: document.getElementById("layoutSpread"),
  spawnJitter: document.getElementById("spawnJitter"),
  drawMode: document.getElementById("drawMode"),
  keepPercent: document.getElementById("keepPercent"),
  recentWindow: document.getElementById("recentWindow"),
  pageWidth: document.getElementById("pageWidth"),
  pageHeight: document.getElementById("pageHeight"),
  margin: document.getElementById("margin"),
  strokeWidth: document.getElementById("strokeWidth"),
  gcodeProfile: document.getElementById("gcodeProfile"),
  remoteUrl: document.getElementById("remoteUrl"),
  programPreamble: document.getElementById("programPreamble"),
  programEpilogue: document.getElementById("programEpilogue"),
  drawFeed: document.getElementById("drawFeed"),
  enableRemoteModeBeforeSend: document.getElementById("enableRemoteModeBeforeSend"),
  parkAfterSend: document.getElementById("parkAfterSend"),
  parkX: document.getElementById("parkX"),
  parkY: document.getElementById("parkY"),
  messagePause: document.getElementById("messagePause"),
  receiveTimeout: document.getElementById("receiveTimeout"),
  generateButton: document.getElementById("generateButton"),
  playButton: document.getElementById("playButton"),
  exportButton: document.getElementById("exportButton"),
  previewRemoteButton: document.getElementById("previewRemoteButton"),
  sendRemoteButton: document.getElementById("sendRemoteButton"),
  frameSlider: document.getElementById("frameSlider"),
  statusText: document.getElementById("statusText"),
  frameSummary: document.getElementById("frameSummary"),
  exportLinks: document.getElementById("exportLinks"),
  remoteSummary: document.getElementById("remoteSummary"),
  remotePreview: document.getElementById("remotePreview"),
  remoteResponses: document.getElementById("remoteResponses"),
};

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  renderFrame();
}

function populateSelect(select, values) {
  select.innerHTML = "";
  Object.entries(values).forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  });
}

function simulationPayload() {
  return {
    seed_type: els.seedType.value,
    frames: Number(els.frames.value),
    events_per_frame: Number(els.eventsPerFrame.value),
    event_selection: els.eventSelection.value,
    random_seed: Number(els.randomSeed.value),
    layout_iterations: Number(els.layoutIterations.value),
    layout_spread: Number(els.layoutSpread.value),
    spawn_jitter: Number(els.spawnJitter.value),
  };
}

function exportPayload() {
  return {
    ...simulationPayload(),
    frame_index: state.currentFrameIndex,
    draw_mode: els.drawMode.value,
    keep_percent: Number(els.keepPercent.value),
    recent_window: Number(els.recentWindow.value),
    page_width_mm: Number(els.pageWidth.value),
    page_height_mm: Number(els.pageHeight.value),
    margin_mm: Number(els.margin.value),
    stroke_width_mm: Number(els.strokeWidth.value),
    gcode_profile: els.gcodeProfile.value,
    program_preamble: els.programPreamble.value,
    program_epilogue: els.programEpilogue.value,
    draw_feed_mm_per_min: Number(els.drawFeed.value),
    park_after_send: els.parkAfterSend.checked,
    park_x_mm: Number(els.parkX.value),
    park_y_mm: Number(els.parkY.value),
  };
}

function remotePayload(includeSocket = false) {
  const payload = {
    ...exportPayload(),
    preview_lines: 80,
  };

  if (includeSocket) {
    payload.websocket_url = els.remoteUrl.value.trim();
    payload.enable_remote_mode_before_send = els.enableRemoteModeBeforeSend.checked;
    payload.message_pause_ms = Number(els.messagePause.value);
    payload.receive_timeout_ms = Number(els.receiveTimeout.value);
  }

  return payload;
}

function currentFrame() {
  if (!state.simulation) {
    return null;
  }
  return state.simulation.frames[state.currentFrameIndex] || null;
}

function filteredEdges(frame) {
  if (!frame) {
    return [];
  }
  const nodes = new Map(frame.nodes.map(([id, x, y]) => [id, { x, y }]));
  const edges = frame.edges.slice();
  const drawMode = els.drawMode.value;

  if (drawMode === "all") {
    return edges;
  }

  if (drawMode === "recent") {
    const cutoff = Math.max(0, frame.event_count - Number(els.recentWindow.value));
    return edges.filter((edge) => edge[2] >= cutoff);
  }

  if (drawMode === "short") {
    const lengths = edges.map((edge) => {
      const a = nodes.get(edge[0]);
      const b = nodes.get(edge[1]);
      return Math.hypot(a.x - b.x, a.y - b.y);
    });

    if (lengths.length === 0) {
      return [];
    }

    const sorted = lengths.slice().sort((a, b) => a - b);
    const percentile = Math.max(1, Math.min(100, Number(els.keepPercent.value)));
    const index = Math.max(0, Math.min(sorted.length - 1, Math.floor((percentile / 100) * (sorted.length - 1))));
    const threshold = sorted[index];
    return edges.filter((edge, idx) => lengths[idx] <= threshold);
  }

  return edges;
}

function fitToCanvas(frame) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const padding = Math.min(width, height) * 0.08;
  const [minX, minY, maxX, maxY] = frame.bounds;
  const worldWidth = Math.max(maxX - minX, 1e-6);
  const worldHeight = Math.max(maxY - minY, 1e-6);
  const scale = Math.min((width - (padding * 2)) / worldWidth, (height - (padding * 2)) / worldHeight);
  const offsetX = (width - (worldWidth * scale)) / 2;
  const offsetY = (height - (worldHeight * scale)) / 2;

  return (point) => ({
    x: offsetX + ((point.x - minX) * scale),
    y: height - (offsetY + ((point.y - minY) * scale)),
  });
}

function projectToMachinePage(frame) {
  const pageWidth = Number(els.pageWidth.value);
  const pageHeight = Number(els.pageHeight.value);
  const margin = Number(els.margin.value);
  const [minX, minY, maxX, maxY] = frame.bounds;
  const worldWidth = Math.max(maxX - minX, 1e-6);
  const worldHeight = Math.max(maxY - minY, 1e-6);
  const usableWidth = Math.max(pageWidth - (margin * 2), 1);
  const usableHeight = Math.max(pageHeight - (margin * 2), 1);
  const scale = Math.min(usableWidth / worldWidth, usableHeight / worldHeight);
  const offsetX = margin + ((usableWidth - (worldWidth * scale)) / 2);
  const offsetY = margin + ((usableHeight - (worldHeight * scale)) / 2);

  return {
    pageWidth,
    pageHeight,
    margin,
    transform(point) {
      return {
        x: offsetX + ((point.x - minX) * scale),
        y: offsetY + ((maxY - point.y) * scale),
      };
    },
  };
}

function fitPageToViewport(pageWidth, pageHeight) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const padding = Math.min(width, height) * 0.06;
  const scale = Math.min((width - (padding * 2)) / pageWidth, (height - (padding * 2)) / pageHeight);
  const offsetX = (width - (pageWidth * scale)) / 2;
  const offsetY = (height - (pageHeight * scale)) / 2;

  return {
    scale,
    project(point) {
      return {
        x: offsetX + (point.x * scale),
        y: offsetY + (point.y * scale),
      };
    },
    rect: {
      x: offsetX,
      y: offsetY,
      width: pageWidth * scale,
      height: pageHeight * scale,
    },
  };
}

function renderFrame() {
  const frame = currentFrame();
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f4efe2";
  ctx.fillRect(0, 0, width, height);

  if (!frame) {
    return;
  }

  const pageProjection = projectToMachinePage(frame);
  const viewport = fitPageToViewport(pageProjection.pageWidth, pageProjection.pageHeight);
  const nodes = new Map(frame.nodes.map(([id, x, y]) => [id, { x, y }]));
  const edges = filteredEdges(frame);
  const newestEvent = Math.max(frame.event_count, 1);
  const pageRect = viewport.rect;

  ctx.save();
  ctx.fillStyle = "rgba(255, 255, 255, 0.7)";
  ctx.strokeStyle = "rgba(23, 34, 77, 0.14)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(pageRect.x, pageRect.y, pageRect.width, pageRect.height, 20);
  ctx.fill();
  ctx.stroke();

  const marginInset = Number(els.margin.value) * viewport.scale;
  if (marginInset * 2 < Math.min(pageRect.width, pageRect.height)) {
    ctx.setLineDash([8, 8]);
    ctx.strokeStyle = "rgba(34, 63, 143, 0.2)";
    ctx.beginPath();
    ctx.roundRect(
      pageRect.x + marginInset,
      pageRect.y + marginInset,
      pageRect.width - (marginInset * 2),
      pageRect.height - (marginInset * 2),
      12,
    );
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();

  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#233a82";
  ctx.lineWidth = 1;

  edges.forEach((edge) => {
    const start = viewport.project(pageProjection.transform(nodes.get(edge[0])));
    const end = viewport.project(pageProjection.transform(nodes.get(edge[1])));
    const age = newestEvent - edge[2];
    const alpha = Math.max(0.12, 0.86 - (age / Math.max(newestEvent, 1)) * 0.65);
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
  });

  ctx.restore();
  drawMachineOrientationOverlay(viewport, pageProjection);

  els.frameSummary.textContent = `Frame ${frame.frame_index} · events ${frame.event_count} · nodes ${frame.node_count} · edges ${frame.edge_count} · duplicates ${frame.duplicate_edges}`;
}

function drawMachineOrientationOverlay(viewport, pageProjection) {
  const panelWidth = 232;
  const panelHeight = 112;
  const pageOrigin = viewport.project({ x: 0, y: 0 });
  const panelX = Math.min(Math.max(pageOrigin.x + 18, 18), canvas.clientWidth - panelWidth - 18);
  const panelY = Math.min(Math.max(pageOrigin.y + 18, 18), canvas.clientHeight - panelHeight - 18);

  ctx.save();
  ctx.fillStyle = "rgba(20, 31, 65, 0.88)";
  ctx.beginPath();
  ctx.roundRect(panelX, panelY, panelWidth, panelHeight, 18);
  ctx.fill();

  ctx.fillStyle = "#f4efe2";
  ctx.font = '600 13px "SFMono-Regular", Consolas, monospace';
  ctx.fillText(`Canvas ${Math.round(pageProjection.pageWidth)} × ${Math.round(pageProjection.pageHeight)} mm`, panelX + 16, panelY + 24);
  ctx.font = '12px "SFMono-Regular", Consolas, monospace';
  ctx.fillStyle = "rgba(244, 239, 226, 0.8)";
  ctx.fillText("1 canvas unit = 1 mm", panelX + 16, panelY + 44);

  const originX = panelX + 44;
  const originY = panelY + 76;

  ctx.strokeStyle = "#f4efe2";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(originX, originY);
  ctx.lineTo(originX + 62, originY);
  ctx.moveTo(originX, originY);
  ctx.lineTo(originX, originY + 24);
  ctx.stroke();

  drawArrowHead(originX + 62, originY, "right");
  drawArrowHead(originX, originY + 24, "down");

  ctx.fillStyle = "#9db4ff";
  ctx.font = '600 12px "SFMono-Regular", Consolas, monospace';
  ctx.fillText("Y+ horizontal", originX + 72, originY + 4);
  ctx.fillText("X+ vertical", originX + 14, originY + 38);

  ctx.fillStyle = "#f4efe2";
  ctx.beginPath();
  ctx.arc(originX, originY, 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillText("origin", originX - 18, originY - 10);
  ctx.restore();
}

function drawArrowHead(x, y, direction) {
  ctx.beginPath();
  if (direction === "right") {
    ctx.moveTo(x, y);
    ctx.lineTo(x - 9, y - 5);
    ctx.lineTo(x - 9, y + 5);
  } else {
    ctx.moveTo(x, y);
    ctx.lineTo(x - 5, y - 9);
    ctx.lineTo(x + 5, y - 9);
  }
  ctx.closePath();
  ctx.fillStyle = "#f4efe2";
  ctx.fill();
}

async function loadOptions() {
  const response = await fetch("/api/options");
  const payload = await response.json();
  state.options = payload;

  populateSelect(els.seedType, payload.seed_types);
  populateSelect(els.eventSelection, payload.event_selections);
  populateSelect(els.drawMode, payload.draw_modes);
  populateSelect(els.gcodeProfile, payload.gcode_profiles);

  const defaults = payload.defaults;
  const remoteDefaults = payload.remote_defaults;
  const machineDefaults = payload.machine_defaults;
  els.seedType.value = defaults.seed_type;
  els.frames.value = defaults.frames;
  els.eventsPerFrame.value = defaults.events_per_frame;
  els.eventSelection.value = defaults.event_selection;
  els.randomSeed.value = defaults.random_seed;
  els.layoutIterations.value = defaults.layout_iterations;
  els.layoutSpread.value = defaults.layout_spread;
  els.spawnJitter.value = defaults.spawn_jitter;
  els.drawMode.value = "all";
  els.gcodeProfile.value = "";
  els.pageWidth.value = machineDefaults.canvas_width_mm;
  els.pageHeight.value = machineDefaults.canvas_height_mm;
  els.programPreamble.value = remoteDefaults.program_preamble;
  els.programEpilogue.value = remoteDefaults.program_epilogue;
  els.drawFeed.value = remoteDefaults.draw_feed_mm_per_min;
  els.enableRemoteModeBeforeSend.checked = remoteDefaults.enable_remote_mode_before_send;
  els.parkAfterSend.checked = remoteDefaults.park_after_send;
  els.parkX.value = remoteDefaults.park_x_mm;
  els.parkY.value = remoteDefaults.park_y_mm;
  els.messagePause.value = remoteDefaults.message_pause_ms;
  els.receiveTimeout.value = remoteDefaults.receive_timeout_ms;
  if (!els.remoteUrl.value.trim()) {
    els.remoteUrl.value = remoteDefaults.websocket_url;
  }
}

async function generate() {
  stopPlayback();
  clearRemotePreview();
  els.statusText.textContent = "Simulating...";
  els.generateButton.disabled = true;

  try {
    const response = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(simulationPayload()),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Simulation failed");
    }

    state.simulation = await response.json();
    state.currentFrameIndex = Math.min(1, state.simulation.frames.length - 1);
    els.frameSlider.max = String(state.simulation.frames.length - 1);
    els.frameSlider.value = String(state.currentFrameIndex);
    els.statusText.textContent = `Simulated ${state.simulation.settings.total_events} events across ${state.simulation.settings.actual_frames} visible frames.`;
    renderFrame();
  } catch (error) {
    els.statusText.textContent = error.message;
  } finally {
    els.generateButton.disabled = false;
  }
}

function clearRemotePreview() {
  state.remoteJob = null;
  els.remoteSummary.textContent = "No remote job preview yet.";
  els.remotePreview.value = "";
  els.remoteResponses.innerHTML = "<p>No remote responses yet.</p>";
}

function stepFrame(direction) {
  if (!state.simulation) {
    return;
  }
  const max = state.simulation.frames.length - 1;
  state.currentFrameIndex = Math.max(0, Math.min(max, state.currentFrameIndex + direction));
  els.frameSlider.value = String(state.currentFrameIndex);
  renderFrame();
}

function startPlayback() {
  if (!state.simulation || state.playing) {
    return;
  }

  state.playing = true;
  els.playButton.textContent = "Pause";
  state.playTimer = window.setInterval(() => {
    if (!state.simulation) {
      stopPlayback();
      return;
    }

    if (state.currentFrameIndex >= state.simulation.frames.length - 1) {
      state.currentFrameIndex = 0;
    } else {
      state.currentFrameIndex += 1;
    }
    els.frameSlider.value = String(state.currentFrameIndex);
    renderFrame();
  }, 280);
}

function stopPlayback() {
  state.playing = false;
  els.playButton.textContent = "Play";
  if (state.playTimer) {
    window.clearInterval(state.playTimer);
    state.playTimer = null;
  }
}

async function exportCurrentFrame() {
  if (!state.simulation) {
    els.statusText.textContent = "Generate a simulation before exporting.";
    return;
  }

  els.exportButton.disabled = true;
  els.statusText.textContent = "Exporting files...";

  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exportPayload()),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Export failed");
    }

    const payload = await response.json();
    renderExportLinks(payload);
    if (payload.messages.length) {
      els.statusText.textContent = payload.messages.join(" | ");
    } else {
      els.statusText.textContent = `Exported to ${payload.folder}`;
    }
  } catch (error) {
    els.statusText.textContent = error.message;
  } finally {
    els.exportButton.disabled = false;
  }
}

function renderRemotePreview(payload) {
  state.remoteJob = payload;
  const summary = payload.summary;
  els.remoteSummary.textContent =
    `Frame ${summary.frame_index} · canvas ${summary.canvas_size_mm.width}×${summary.canvas_size_mm.height} mm · strokes ${summary.stroke_count} · segments ${summary.segment_count} · lines ${summary.line_count} · draw ${summary.draw_distance_mm} mm · travel ${summary.travel_distance_mm} mm · machine X=vertical, Y=horizontal`;
  els.remotePreview.value = payload.preview_lines.join("\n");
}

function renderRemoteResponses(responses) {
  els.remoteResponses.innerHTML = "";

  if (!responses || responses.length === 0) {
    els.remoteResponses.innerHTML = "<p>No remote responses received within the timeout window.</p>";
    return;
  }

  responses.forEach((response) => {
    const p = document.createElement("p");
    p.className = "warning";
    if (typeof response === "string") {
      p.textContent = response;
    } else {
      const type = response.type || "message";
      const detail = response.message || response.reason || response.command || JSON.stringify(response);
      p.textContent = `${type}: ${detail}`;
    }
    els.remoteResponses.appendChild(p);
  });
}

async function previewRemoteJob() {
  if (!state.simulation) {
    els.statusText.textContent = "Generate a simulation before previewing a remote job.";
    return;
  }

  els.previewRemoteButton.disabled = true;
  els.statusText.textContent = "Building remote program...";

  try {
    const response = await fetch("/api/remote/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(remotePayload(false)),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Remote preview failed");
    }

    const payload = await response.json();
    renderRemotePreview(payload);
    renderRemoteResponses([]);
    els.statusText.textContent = payload.summary.server_protocol;
  } catch (error) {
    els.statusText.textContent = error.message;
  } finally {
    els.previewRemoteButton.disabled = false;
  }
}

async function sendRemoteJob() {
  if (!state.simulation) {
    els.statusText.textContent = "Generate a simulation before sending a remote job.";
    return;
  }

  if (!els.remoteUrl.value.trim()) {
    els.statusText.textContent = "Enter the plotter server websocket URL first.";
    return;
  }

  els.sendRemoteButton.disabled = true;
  els.statusText.textContent = "Sending remote job...";

  try {
    const response = await fetch("/api/remote/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(remotePayload(true)),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Remote send failed");
    }

    const payload = await response.json();
    renderRemotePreview(payload);
    renderRemoteResponses(payload.responses);
    els.statusText.textContent = `Queued ${payload.summary.line_count} G-code lines via ${payload.websocket_url}. Sent ${payload.sent_messages} JSON websocket message(s).`;
  } catch (error) {
    els.statusText.textContent = error.message;
  } finally {
    els.sendRemoteButton.disabled = false;
  }
}

function renderExportLinks(payload) {
  els.exportLinks.innerHTML = "";

  payload.files.forEach((file) => {
    const link = document.createElement("a");
    link.href = file.path;
    link.textContent = file.label;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.className = "download-link";
    els.exportLinks.appendChild(link);
  });

  if (payload.messages.length) {
    payload.messages.forEach((message) => {
      const p = document.createElement("p");
      p.className = "warning";
      p.textContent = message;
      els.exportLinks.appendChild(p);
    });
  }
}

els.generateButton.addEventListener("click", generate);
els.playButton.addEventListener("click", () => {
  if (state.playing) {
    stopPlayback();
  } else {
    startPlayback();
  }
});
els.exportButton.addEventListener("click", exportCurrentFrame);
els.previewRemoteButton.addEventListener("click", previewRemoteJob);
els.sendRemoteButton.addEventListener("click", sendRemoteJob);
els.frameSlider.addEventListener("input", () => {
  stopPlayback();
  state.currentFrameIndex = Number(els.frameSlider.value);
  clearRemotePreview();
  renderFrame();
});
els.drawMode.addEventListener("change", () => {
  clearRemotePreview();
  renderFrame();
});
els.keepPercent.addEventListener("input", () => {
  clearRemotePreview();
  renderFrame();
});
els.recentWindow.addEventListener("input", () => {
  clearRemotePreview();
  renderFrame();
});
els.pageWidth.addEventListener("input", () => {
  clearRemotePreview();
  renderFrame();
});
els.pageHeight.addEventListener("input", () => {
  clearRemotePreview();
  renderFrame();
});
els.margin.addEventListener("input", () => {
  clearRemotePreview();
  renderFrame();
});
window.addEventListener("resize", resizeCanvas);

loadOptions()
  .then(generate)
  .finally(() => {
    resizeCanvas();
  });
