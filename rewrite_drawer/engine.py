from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np

SEED_TYPES = {
    "path3": "Three-node path",
    "star3": "Three-arm star",
    "triangle": "Triangle",
    "bowtie": "Bowtie",
    "square": "Square",
}

EVENT_SELECTIONS = {
    "random": "Random weighted wedge",
    "hub_first": "Highest-degree center",
    "newest_bias": "Newest-edge bias",
}

DRAW_MODES = {
    "all": "All edges",
    "short": "Shortest edges only",
    "recent": "Most recent edges",
}

GCODE_PROFILES = {
    "": "SVG only",
    "gcodemm": "Generic G-code (mm)",
    "gcode": "Generic G-code (in)",
    "step_motor": "Bundled step motor profile",
    "step_motor_relative": "Bundled step motor relative profile",
    "ninja": "Bundled Ninja profile",
}


@dataclass(frozen=True)
class SimulationParams:
    seed_type: str = "path3"
    frames: int = 24
    events_per_frame: int = 30
    event_selection: str = "random"
    random_seed: int = 13
    layout_iterations: int = 60
    layout_spread: float = 1.15
    spawn_jitter: float = 0.08


@dataclass(frozen=True)
class EdgeRecord:
    edge_id: int
    u: int
    v: int
    created_event: int

    def other(self, node_id: int) -> int:
        return self.v if self.u == node_id else self.u


@dataclass
class GraphState:
    active_edges: dict[int, EdgeRecord]
    next_node_id: int
    next_edge_id: int
    last_births: dict[int, tuple[int, int, int]] = field(default_factory=dict)

    @classmethod
    def from_seed(cls, seed_type: str) -> "GraphState":
        edges = _build_seed_edges(seed_type)
        active_edges = {}
        next_edge_id = 1

        for u, v in edges:
            active_edges[next_edge_id] = EdgeRecord(
                edge_id=next_edge_id,
                u=min(u, v),
                v=max(u, v),
                created_event=0,
            )
            next_edge_id += 1

        next_node_id = max(max(u, v) for u, v in edges) + 1
        return cls(active_edges=active_edges, next_node_id=next_node_id, next_edge_id=next_edge_id)

    def node_ids(self) -> list[int]:
        return list(range(1, self.next_node_id))

    def add_edge(self, u: int, v: int, created_event: int) -> None:
        edge = EdgeRecord(
            edge_id=self.next_edge_id,
            u=min(u, v),
            v=max(u, v),
            created_event=created_event,
        )
        self.active_edges[self.next_edge_id] = edge
        self.next_edge_id += 1

    def build_incidence(self) -> dict[int, list[int]]:
        incidence: dict[int, list[int]] = {node_id: [] for node_id in self.node_ids()}
        for edge in self.active_edges.values():
            incidence[edge.u].append(edge.edge_id)
            incidence[edge.v].append(edge.edge_id)
        return incidence


def default_options() -> dict[str, Any]:
    return {
        "seed_types": SEED_TYPES,
        "event_selections": EVENT_SELECTIONS,
        "draw_modes": DRAW_MODES,
        "gcode_profiles": GCODE_PROFILES,
        "defaults": SimulationParams().__dict__,
    }


def simulate(params: SimulationParams) -> dict[str, Any]:
    rng = random.Random(params.random_seed)
    state = GraphState.from_seed(params.seed_type)
    frames: list[dict[str, Any]] = []
    positions: dict[int, np.ndarray] | None = None
    total_events = 0

    positions = _layout_state(state, positions, state.last_births, params, rng)
    frames.append(_serialize_frame(state, positions, frame_index=0, total_events=0))

    for frame_index in range(1, params.frames + 1):
        births: dict[int, tuple[int, int, int]] = {}
        applied = 0

        for _ in range(params.events_per_frame):
            event = _apply_event(state, total_events + 1, params.event_selection, rng)
            if event is None:
                break
            applied += 1
            total_events += 1
            births[event["new_node"]] = event["parents"]

        positions = _layout_state(state, positions, births, params, rng)
        frames.append(_serialize_frame(state, positions, frame_index=frame_index, total_events=total_events))

        if applied == 0:
            break

    return {
        "frames": frames,
        "settings": {
            **params.__dict__,
            "actual_frames": len(frames),
            "total_events": total_events,
        },
    }


def filtered_edges(
    frame: dict[str, Any],
    draw_mode: str,
    keep_percent: float,
    recent_window: int,
) -> list[list[int]]:
    if draw_mode == "all":
        return frame["edges"]

    position_map = {int(node_id): (x, y) for node_id, x, y in frame["nodes"]}
    edges = frame["edges"]

    if draw_mode == "recent":
        cutoff = max(0, frame["event_count"] - recent_window)
        return [edge for edge in edges if edge[2] >= cutoff]

    if draw_mode == "short":
        if not edges:
            return []

        lengths = []
        for edge in edges:
            ax, ay = position_map[edge[0]]
            bx, by = position_map[edge[1]]
            lengths.append(math.dist((ax, ay), (bx, by)))

        threshold = np.percentile(lengths, keep_percent)
        return [
            edge
            for edge, length in zip(edges, lengths, strict=True)
            if length <= threshold
        ]

    return edges


def _build_seed_edges(seed_type: str) -> list[tuple[int, int]]:
    seeds = {
        "path3": [(1, 2), (2, 3)],
        "star3": [(1, 2), (1, 3), (1, 4)],
        "triangle": [(1, 2), (2, 3), (1, 3)],
        "bowtie": [(1, 2), (1, 3), (2, 3), (1, 4), (1, 5), (4, 5)],
        "square": [(1, 2), (2, 3), (3, 4), (1, 4)],
    }

    if seed_type not in seeds:
        raise ValueError(f"Unknown seed type: {seed_type}")
    return seeds[seed_type]


def _apply_event(
    state: GraphState,
    event_number: int,
    selection_mode: str,
    rng: random.Random,
) -> dict[str, Any] | None:
    incidence = state.build_incidence()
    candidate_centers = {node: edge_ids for node, edge_ids in incidence.items() if len(edge_ids) >= 2}

    if not candidate_centers:
        return None

    center = _select_center(candidate_centers, state, selection_mode, rng)
    edge_ids = candidate_centers[center]
    keep_edge_id, rewrite_edge_id = rng.sample(edge_ids, 2)

    keep_edge = state.active_edges[keep_edge_id]
    rewrite_edge = state.active_edges[rewrite_edge_id]

    y = keep_edge.other(center)
    z = rewrite_edge.other(center)
    new_node = state.next_node_id
    state.next_node_id += 1

    del state.active_edges[keep_edge_id]
    del state.active_edges[rewrite_edge_id]

    state.add_edge(center, y, created_event=event_number)
    state.add_edge(center, new_node, created_event=event_number)
    state.add_edge(y, new_node, created_event=event_number)
    state.add_edge(z, new_node, created_event=event_number)

    return {
        "new_node": new_node,
        "parents": (center, y, z),
    }


def _select_center(
    candidate_centers: dict[int, list[int]],
    state: GraphState,
    selection_mode: str,
    rng: random.Random,
) -> int:
    items = list(candidate_centers.items())

    if selection_mode == "hub_first":
        max_degree = max(len(edge_ids) for _, edge_ids in items)
        centers = [node for node, edge_ids in items if len(edge_ids) == max_degree]
        return rng.choice(centers)

    if selection_mode == "newest_bias":
        weighted = []
        for node, edge_ids in items:
            newest = max(state.active_edges[edge_id].created_event for edge_id in edge_ids)
            weighted.append((node, (newest + 1) ** 2))
        return _weighted_choice(weighted, rng)

    weighted = []
    for node, edge_ids in items:
        wedge_count = len(edge_ids) * (len(edge_ids) - 1)
        weighted.append((node, wedge_count))
    return _weighted_choice(weighted, rng)


def _weighted_choice(weighted_items: list[tuple[int, int]], rng: random.Random) -> int:
    total = sum(weight for _, weight in weighted_items)
    target = rng.uniform(0, total)
    running = 0.0
    for item, weight in weighted_items:
        running += weight
        if running >= target:
            return item
    return weighted_items[-1][0]


def _layout_state(
    state: GraphState,
    previous_positions: dict[int, np.ndarray] | None,
    births: dict[int, tuple[int, int, int]],
    params: SimulationParams,
    rng: random.Random,
) -> dict[int, np.ndarray]:
    graph = nx.Graph()
    graph.add_nodes_from(state.node_ids())

    for edge in state.active_edges.values():
        if graph.has_edge(edge.u, edge.v):
            graph[edge.u][edge.v]["weight"] += 1.0
        else:
            graph.add_edge(edge.u, edge.v, weight=1.0)

    initial_positions: dict[int, np.ndarray] | None = None
    if previous_positions:
        initial_positions = {
            node_id: np.array(coords, dtype=float)
            for node_id, coords in previous_positions.items()
            if node_id in graph.nodes
        }

        for node_id in graph.nodes:
            if node_id in initial_positions:
                continue

            anchors = [initial_positions[parent] for parent in births.get(node_id, ()) if parent in initial_positions]
            base = np.mean(anchors, axis=0) if anchors else np.zeros(2)
            jitter = np.array(
                [rng.uniform(-params.spawn_jitter, params.spawn_jitter), rng.uniform(-params.spawn_jitter, params.spawn_jitter)],
                dtype=float,
            )
            initial_positions[node_id] = base + jitter

    k_value = params.layout_spread / math.sqrt(max(graph.number_of_nodes(), 1))
    result = nx.spring_layout(
        graph,
        pos=initial_positions,
        iterations=params.layout_iterations,
        seed=params.random_seed,
        k=k_value,
        weight="weight",
    )
    return {int(node_id): np.array(coords, dtype=float) for node_id, coords in result.items()}


def _serialize_frame(
    state: GraphState,
    positions: dict[int, np.ndarray],
    frame_index: int,
    total_events: int,
) -> dict[str, Any]:
    node_payload = [
        [node_id, float(positions[node_id][0]), float(positions[node_id][1])]
        for node_id in sorted(positions)
    ]
    edge_payload = [
        [edge.u, edge.v, edge.created_event]
        for edge in sorted(state.active_edges.values(), key=lambda item: item.edge_id)
    ]

    xs = [coords[0] for coords in positions.values()]
    ys = [coords[1] for coords in positions.values()]
    unique_edges = {(min(edge[0], edge[1]), max(edge[0], edge[1])) for edge in edge_payload}

    return {
        "frame_index": frame_index,
        "event_count": total_events,
        "node_count": len(node_payload),
        "edge_count": len(edge_payload),
        "duplicate_edges": len(edge_payload) - len(unique_edges),
        "bounds": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))],
        "nodes": node_payload,
        "edges": edge_payload,
    }
