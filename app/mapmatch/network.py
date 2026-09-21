"""路网模型：从 GeoJSON 加载道路，构建图结构与网格空间索引，提供候选投影与最短路径。"""
from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

from .geo import LocalProjection, haversine, project_to_segment


class Edge:
    """一条道路线段（相邻两个顶点之间），无向。"""

    __slots__ = ("id", "road", "u", "v", "length", "u_lon", "u_lat", "v_lon", "v_lat", "maxspeed")

    def __init__(self, eid, road, u, v, length, u_lon, u_lat, v_lon, v_lat, maxspeed):
        self.id = eid
        self.road = road
        self.u = u
        self.v = v
        self.length = length
        self.u_lon = u_lon
        self.u_lat = u_lat
        self.v_lon = v_lon
        self.v_lat = v_lat
        self.maxspeed = maxspeed


class RoadNetwork:
    def __init__(self):
        self.edges: list[Edge] = []
        self.nodes: list[tuple[float, float]] = []          # node_id -> (lon, lat)
        self.adj: dict[int, list[tuple[int, int]]] = defaultdict(list)  # node -> [(nbr, edge_id)]
        self._node_id: dict[tuple[float, float], int] = {}
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)  # 网格 -> edge_id 列表
        self.cell_deg = 0.001
        self.raw_geojson: dict = {}

    # ---------- 加载 ----------

    @classmethod
    def from_geojson(cls, path: str, cell_m: float = 100.0) -> "RoadNetwork":
        net = cls()
        net.cell_deg = cell_m / 111320.0
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        net.raw_geojson = data
        for feat in data.get("features", []):
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            if gtype == "LineString":
                lines = [coords]
            elif gtype == "MultiLineString":
                lines = coords
            else:
                continue
            name = props.get("name", "未知道路")
            maxspeed = props.get("maxspeed")
            for line in lines:
                net._add_line(line, name, maxspeed)
        net._build_grid()
        return net

    def _node(self, lon: float, lat: float) -> int:
        key = (round(lon, 6), round(lat, 6))  # ~0.1m 精度去重，交叉口共享节点
        nid = self._node_id.get(key)
        if nid is None:
            nid = len(self.nodes)
            self._node_id[key] = nid
            self.nodes.append(key)
        return nid

    def _add_line(self, coords: list, road: str, maxspeed) -> None:
        for i in range(len(coords) - 1):
            (alon, alat), (blon, blat) = coords[i][:2], coords[i + 1][:2]
            length = haversine(alon, alat, blon, blat)
            if length < 1e-6:
                continue
            u, v = self._node(alon, alat), self._node(blon, blat)
            eid = len(self.edges)
            self.edges.append(Edge(eid, road, u, v, length, alon, alat, blon, blat, maxspeed))
            self.adj[u].append((v, eid))
            self.adj[v].append((u, eid))

    def _build_grid(self) -> None:
        for e in self.edges:
            minx = min(e.u_lon, e.v_lon) - 1e-9
            maxx = max(e.u_lon, e.v_lon) + 1e-9
            miny = min(e.u_lat, e.v_lat) - 1e-9
            maxy = max(e.u_lat, e.v_lat) + 1e-9
            cx0, cx1 = int(minx // self.cell_deg), int(maxx // self.cell_deg)
            cy0, cy1 = int(miny // self.cell_deg), int(maxy // self.cell_deg)
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    self.grid[(cx, cy)].append(e.id)

    # ---------- 查询 ----------

    def node_coord(self, nid: int) -> tuple[float, float]:
        return self.nodes[nid]

    def candidates(self, lon: float, lat: float, radius_m: float, max_n: int):
        """返回半径内的候选投影：[(edge_id, fraction, dist_m, proj_lon, proj_lat)]，按距离升序。"""
        proj = LocalProjection(lat)
        dlat = radius_m / 111320.0
        dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
        cx0 = int((lon - dlon) // self.cell_deg)
        cx1 = int((lon + dlon) // self.cell_deg)
        cy0 = int((lat - dlat) // self.cell_deg)
        cy1 = int((lat + dlat) // self.cell_deg)
        seen: set[int] = set()
        out = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for eid in self.grid.get((cx, cy), ()):
                    if eid in seen:
                        continue
                    seen.add(eid)
                    e = self.edges[eid]
                    frac, dist, plon, plat = project_to_segment(
                        lon, lat, e.u_lon, e.u_lat, e.v_lon, e.v_lat, proj
                    )
                    if dist <= radius_m:
                        out.append((eid, frac, dist, plon, plat))
        out.sort(key=lambda t: t[2])
        return out[:max_n]

    def shortest_paths(self, sources: dict[int, float], cutoff: float = math.inf):
        """多源 Dijkstra。sources: {node: 初始距离}。

        返回 (dist, prev)：dist[node] 为最短距离，prev[node] = (前驱node, edge_id)，源点为 None。
        """
        dist: dict[int, float] = {}
        prev: dict[int, tuple | None] = {}
        pq: list[tuple[float, int]] = []
        for nd, d0 in sources.items():
            if d0 < dist.get(nd, math.inf):
                dist[nd] = d0
                prev[nd] = None
                heapq.heappush(pq, (d0, nd))
        while pq:
            d, nd = heapq.heappop(pq)
            if d > dist[nd]:
                continue
            if d > cutoff:
                break
            for nbr, eid in self.adj.get(nd, ()):
                ndist = d + self.edges[eid].length
                if ndist > cutoff:
                    continue
                if ndist < dist.get(nbr, math.inf):
                    dist[nbr] = ndist
                    prev[nbr] = (nd, eid)
                    heapq.heappush(pq, (ndist, nbr))
        return dist, prev
