"""Pathfinding over optional navigation nodes.

When no node graph is loaded, paths are straight XZ segments. A* is used as
soon as nodes/edges exist so later map nav data does not require a planner rewrite.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field


@dataclass
class NavNode:
    x: float
    y: float
    z: float
    id: str = ""


@dataclass
class NavGraph:
    nodes: list[NavNode] = field(default_factory=list)
    # adjacency: index -> list[(other_index, cost)]
    adj: list[list[tuple[int, float]]] = field(default_factory=list)

    def clear(self):
        self.nodes.clear()
        self.adj.clear()

    def load(self, nodes: list[NavNode], edges: list[tuple[int, int, float]] | None = None):
        self.nodes = list(nodes)
        self.adj = [[] for _ in self.nodes]
        if edges:
            for a, b, cost in edges:
                if 0 <= a < len(self.nodes) and 0 <= b < len(self.nodes):
                    self.adj[a].append((b, cost))
                    self.adj[b].append((a, cost))
            return
        # Fully connect nearby nodes so a dumped waypoint set still routes.
        limit = 40.0
        limit2 = limit * limit
        for i, a in enumerate(self.nodes):
            for j in range(i + 1, len(self.nodes)):
                b = self.nodes[j]
                d2 = (a.x - b.x) ** 2 + (a.z - b.z) ** 2
                if d2 <= limit2:
                    cost = math.sqrt(d2) + abs(a.y - b.y) * 8.0
                    self.adj[i].append((j, cost))
                    self.adj[j].append((i, cost))

    def xz_distance(self, ax: float, az: float, bx: float, bz: float) -> float:
        return math.hypot(ax - bx, az - bz)

    def travel_cost(
        self,
        ax: float,
        ay: float,
        az: float,
        bx: float,
        by: float,
        bz: float,
    ) -> float:
        return math.hypot(ax - bx, az - bz) + abs(ay - by) * 8.0

    def path(
        self,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
    ) -> list[tuple[float, float, float]]:
        """Return waypoints from start to goal (inclusive)."""
        if not self.nodes or not self.adj:
            return [start, goal]
        si = self._nearest(start)
        gi = self._nearest(goal)
        if si < 0 or gi < 0:
            return [start, goal]
        chain = self._astar(si, gi)
        if not chain:
            return [start, goal]
        pts = [start]
        for idx in chain:
            n = self.nodes[idx]
            pts.append((n.x, n.y, n.z))
        pts.append(goal)
        return _dedupe_pts(pts)

    def path_length(self, waypoints: list[tuple[float, float, float]]) -> float:
        total = 0.0
        for a, b in zip(waypoints, waypoints[1:]):
            total += self.travel_cost(a[0], a[1], a[2], b[0], b[1], b[2])
        return total

    def _nearest(self, pt: tuple[float, float, float]) -> int:
        best = -1
        best_d = 1e18
        x, y, z = pt
        for i, n in enumerate(self.nodes):
            d = (n.x - x) ** 2 + (n.z - z) ** 2 + (n.y - y) ** 2 * 4.0
            if d < best_d:
                best_d = d
                best = i
        return best

    def _astar(self, start: int, goal: int) -> list[int]:
        if start == goal:
            return [start]
        h = lambda i: self.xz_distance(
            self.nodes[i].x, self.nodes[i].z, self.nodes[goal].x, self.nodes[goal].z
        )
        open_h: list[tuple[float, int]] = [(h(start), start)]
        came: dict[int, int] = {}
        gscore = {start: 0.0}
        seen: set[int] = set()
        while open_h:
            _, cur = heapq.heappop(open_h)
            if cur in seen:
                continue
            seen.add(cur)
            if cur == goal:
                chain = [cur]
                while cur in came:
                    cur = came[cur]
                    chain.append(cur)
                chain.reverse()
                return chain
            for nxt, cost in self.adj[cur]:
                tentative = gscore[cur] + cost
                if tentative < gscore.get(nxt, 1e18):
                    came[nxt] = cur
                    gscore[nxt] = tentative
                    heapq.heappush(open_h, (tentative + h(nxt), nxt))
        return []


def _dedupe_pts(
    pts: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[2] - out[-1][2]) > 0.4:
            out.append(p)
    return out
