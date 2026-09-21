"""隐马尔可夫路网匹配：候选投影 + Viterbi 解码。

- 发射概率：观测点到候选道路投影距离的高斯分布 N(0, sigma)。
- 转移概率：最短路径距离与直线距离之差的指数分布 exp(-|route-linear|/beta)，
  叠加速度代价（推算速度与观测速度偏差），并对超速转移做硬剪枝。
- 解码失败时返回断段位置与原因（无候选 / 不连通 / 超速）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geo import haversine
from .network import RoadNetwork

LOG2PI = math.log(2 * math.pi)

BREAK_REASONS = {
    "no_candidates_within_radius": "该观测点在搜索半径内没有任何候选道路（点偏离路网或半径过小）",
    "no_route_between_candidates": "相邻观测点的候选道路之间在路网中不连通，找不到可行路径",
    "speed_limit_exceeded": "相邻观测点间可达，但推算行驶速度超过速度上限，转移被剪枝",
    "no_feasible_transition": "相邻观测点间无可行转移（不连通与超速并存）",
}


@dataclass
class MatchParams:
    candidate_radius_m: float = 50.0
    max_candidates: int = 8
    sigma_m: float = 15.0
    beta_m: float = 30.0
    max_speed_mps: float = 60.0
    speed_weight: float = 1.0

    def as_dict(self) -> dict:
        return {
            "candidate_radius_m": self.candidate_radius_m,
            "max_candidates": self.max_candidates,
            "sigma_m": self.sigma_m,
            "beta_m": self.beta_m,
            "max_speed_mps": self.max_speed_mps,
            "speed_weight": self.speed_weight,
        }


@dataclass
class Candidate:
    edge_id: int
    fraction: float   # 投影点在线段 u->v 上的比例
    dist_m: float     # 观测点到投影点的距离
    lon: float
    lat: float
    emission: float = 0.0


@dataclass
class MatchResult:
    status: str                  # matched / partial / failed
    break_reason: str | None
    break_seq: int | None
    total_points: int
    matched_points: int
    points: list[dict] = field(default_factory=list)    # 每个已匹配点的明细
    segments: list[dict] = field(default_factory=list)  # 相邻匹配点之间的路径几何
    params: dict = field(default_factory=dict)


class HMMMatcher:
    def __init__(self, network: RoadNetwork, params: MatchParams | None = None):
        self.net = network
        self.params = params or MatchParams()

    # ---------- 概率模型 ----------

    @staticmethod
    def _emission_logp(dist_m: float, sigma: float) -> float:
        """发射对数概率：观测噪声服从零均值高斯分布。"""
        return -0.5 * (dist_m / sigma) ** 2 - math.log(sigma) - 0.5 * LOG2PI

    def _candidates(self, lon: float, lat: float, p: MatchParams) -> list[Candidate]:
        raw = self.net.candidates(lon, lat, p.candidate_radius_m, p.max_candidates)
        return [
            Candidate(eid, frac, dist, plon, plat, self._emission_logp(dist, p.sigma_m))
            for eid, frac, dist, plon, plat in raw
        ]

    def _route_distance(self, a: Candidate, b: Candidate, linear: float, p: MatchParams) -> float:
        """两个候选投影点之间沿路网的最短路径距离（米）。不连通返回 inf。"""
        ea, eb = self.net.edges[a.edge_id], self.net.edges[b.edge_id]
        if a.edge_id == b.edge_id:
            return abs(a.fraction - b.fraction) * ea.length
        da_u = a.fraction * ea.length
        da_v = ea.length - da_u
        # 路径总长超过 linear + 10*beta 时转移概率已可忽略，据此剪枝 Dijkstra
        cutoff = linear + 10.0 * p.beta_m + 100.0
        dist, _ = self.net.shortest_paths({ea.u: da_u, ea.v: da_v}, cutoff)
        db_u = b.fraction * eb.length
        db_v = eb.length - db_u
        best = math.inf
        du = dist.get(eb.u)
        if du is not None:
            best = min(best, du + db_u)
        dv = dist.get(eb.v)
        if dv is not None:
            best = min(best, dv + db_v)
        return best

    def _transition_logp(self, a: Candidate, b: Candidate, pt_prev: dict, pt_cur: dict,
                         linear: float, p: MatchParams):
        """返回 (logp, 不可行原因或 None, route_dist)。"""
        route = self._route_distance(a, b, linear, p)
        if math.isinf(route):
            return -math.inf, "unreachable", route
        speed_cost = 0.0
        t0, t1 = pt_prev.get("ts"), pt_cur.get("ts")
        if t0 is not None and t1 is not None and t1 > t0:
            dt = t1 - t0
            implied = route / dt  # 推算速度（米/秒）
            if implied > p.max_speed_mps:
                return -math.inf, "speed", route
            obs = pt_cur.get("speed")
            if obs is None:
                obs = pt_prev.get("speed")
            if obs is not None:
                speed_cost = abs(implied - obs) / max(obs, 5.0)
        logp = -math.log(p.beta_m) - abs(route - linear) / p.beta_m - p.speed_weight * speed_cost
        return logp, None, route

    # ---------- 主流程 ----------

    def match(self, points: list[dict], params: MatchParams | None = None) -> MatchResult:
        """points: [{"lon":..,"lat":..,"ts":epoch秒|None,"speed":m/s|None}, ...]"""
        p = params or self.params
        n = len(points)
        cand_lists = [self._candidates(pt["lon"], pt["lat"], p) for pt in points]

        # 第一个没有候选的点即为断点
        end = n
        break_reason = None
        break_seq = None
        for i, cands in enumerate(cand_lists):
            if not cands:
                end = i
                break_reason = "no_candidates_within_radius"
                break_seq = i
                break

        # Viterbi 前向递推（对数概率）
        logp_cols: list[dict[int, float]] = []
        backptrs: list[dict[int, int]] = []
        trans_logps: list[dict[int, float]] = []  # 每个状态最优转移的转移对数概率
        if end > 0:
            logp_cols.append({j: c.emission for j, c in enumerate(cand_lists[0])})
        for i in range(1, end):
            linear = haversine(points[i - 1]["lon"], points[i - 1]["lat"],
                               points[i]["lon"], points[i]["lat"])
            cur: dict[int, float] = {}
            bptr: dict[int, int] = {}
            tptr: dict[int, float] = {}
            reasons: set[str] = set()
            for ja, a in enumerate(cand_lists[i - 1]):
                if logp_cols[-1].get(ja, -math.inf) == -math.inf:
                    continue
                for jb, b in enumerate(cand_lists[i]):
                    tlogp, why, _ = self._transition_logp(
                        a, b, points[i - 1], points[i], linear, p)
                    if why:
                        reasons.add(why)
                    if tlogp == -math.inf:
                        continue
                    v = logp_cols[-1][ja] + tlogp + b.emission
                    if jb not in cur or v > cur[jb]:
                        cur[jb] = v
                        bptr[jb] = ja
                        tptr[jb] = tlogp
            if not cur:
                end = i
                break_reason = self._classify_break(reasons)
                break_seq = i
                break
            logp_cols.append(cur)
            backptrs.append(bptr)
            trans_logps.append(tptr)

        # 回溯最优路径
        winners: list[Candidate] = []
        if logp_cols:
            last = max(logp_cols[-1], key=logp_cols[-1].get)
            idx = [last]
            for bptr in reversed(backptrs):
                idx.append(bptr[idx[-1]])
            idx.reverse()
            winners = [cand_lists[i][j] for i, j in enumerate(idx)]

        matched_points = len(winners)
        if break_reason is None:
            status = "matched"
        elif matched_points >= 2:
            status = "partial"
        else:
            status = "failed"

        pts_out = []
        for i, c in enumerate(winners):
            e = self.net.edges[c.edge_id]
            tlogp = None
            if i > 0:
                tlogp = round(trans_logps[i - 1][idx[i]], 4)
            pts_out.append({
                "seq": i,
                "orig": {"lon": points[i]["lon"], "lat": points[i]["lat"]},
                "matched": {"lon": round(c.lon, 7), "lat": round(c.lat, 7)},
                "road": e.road,
                "edge_id": c.edge_id,
                "dist_m": round(c.dist_m, 2),
                "emission_logp": round(c.emission, 4),
                "transition_logp": tlogp,
            })
        segments = []
        for i in range(1, len(winners)):
            coords, length, road = self._route_geometry(winners[i - 1], winners[i])
            segments.append({
                "seq": i,
                "road": road,
                "length_m": round(length, 2),
                "coords": [[round(x, 7), round(y, 7)] for x, y in coords],
            })

        return MatchResult(
            status=status,
            break_reason=break_reason,
            break_seq=break_seq,
            total_points=n,
            matched_points=matched_points,
            points=pts_out,
            segments=segments,
            params=p.as_dict(),
        )

    @staticmethod
    def _classify_break(reasons: set[str]) -> str:
        if reasons == {"unreachable"}:
            return "no_route_between_candidates"
        if reasons == {"speed"}:
            return "speed_limit_exceeded"
        return "no_feasible_transition"

    def _route_geometry(self, a: Candidate, b: Candidate):
        """重建两个相邻匹配点之间沿路网的最短路径几何。返回 (coords, length_m, road)。"""
        ea, eb = self.net.edges[a.edge_id], self.net.edges[b.edge_id]
        pa, pb = (a.lon, a.lat), (b.lon, b.lat)
        if a.edge_id == b.edge_id:
            length = abs(a.fraction - b.fraction) * ea.length
            coords = [pa] if pa == pb else [pa, pb]
            return coords, length, ea.road
        da_u = a.fraction * ea.length
        da_v = ea.length - da_u
        dist, prev = self.net.shortest_paths({ea.u: da_u, ea.v: da_v})
        db_u = b.fraction * eb.length
        db_v = eb.length - db_u
        best_total, best_end = math.inf, None
        for node, extra in ((eb.u, db_u), (eb.v, db_v)):
            d = dist.get(node)
            if d is not None and d + extra < best_total:
                best_total, best_end = d + extra, node
        if best_end is None:
            return [pa, pb], math.inf, eb.road
        nodes = [best_end]
        while prev[nodes[-1]] is not None:
            nodes.append(prev[nodes[-1]][0])
        nodes.reverse()
        coords = [pa] + [self.net.node_coord(nd) for nd in nodes] + [pb]
        out = [coords[0]]
        for c in coords[1:]:
            if c != out[-1]:
                out.append(c)
        return out, best_total, eb.road
