"""路网匹配：执行、查询结果、GeoJSON 导出。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import database as db
from ..config import settings
from ..mapmatch.hmm import BREAK_REASONS, HMMMatcher, MatchParams
from ..schemas import MatchParamsIn

router = APIRouter(prefix="/api/trajectories", tags=["matching"])


def build_params(override: MatchParamsIn | None) -> MatchParams:
    p = MatchParams(
        candidate_radius_m=settings.candidate_radius_m,
        max_candidates=settings.max_candidates,
        sigma_m=settings.sigma_m,
        beta_m=settings.beta_m,
        max_speed_mps=settings.max_speed_mps,
        speed_weight=settings.speed_weight,
    )
    if override:
        for f in ("candidate_radius_m", "max_candidates", "sigma_m",
                  "beta_m", "max_speed_mps", "speed_weight"):
            v = getattr(override, f)
            if v is not None:
                setattr(p, f, v)
    return p


def run_match(matcher: HMMMatcher, tid: int, points: list[dict],
              override: MatchParamsIn | None):
    params = build_params(override)
    result = matcher.match(points, params)
    match_id = db.save_match(tid, result, params.as_dict())
    db.update_status(tid, result.status)
    return result, match_id


@router.post("/{tid}/match", status_code=201, summary="对轨迹执行（或重新执行）路网匹配")
def match_trajectory(tid: int, request: Request, params: MatchParamsIn | None = None):
    traj = db.get_trajectory(tid)
    if traj is None:
        raise HTTPException(404, f"轨迹 {tid} 不存在")
    points = [{"lon": p["lon"], "lat": p["lat"], "ts": p["ts"], "speed": p["speed"]}
              for p in traj["points"]]
    result, match_id = run_match(request.app.state.matcher, tid, points, params)
    return {
        "match_id": match_id,
        "trajectory_id": tid,
        "status": result.status,
        "matched_points": result.matched_points,
        "total_points": result.total_points,
        "break_reason": result.break_reason,
        "break_reason_text": BREAK_REASONS.get(result.break_reason),
        "break_seq": result.break_seq,
        "params": result.params,
    }


@router.get("/{tid}/match", summary="查询最近一次匹配结果（点级 + 路段级）")
def get_match(tid: int):
    match = db.get_latest_match(tid)
    if match is None:
        raise HTTPException(404, f"轨迹 {tid} 尚无匹配结果")
    match["break_reason_text"] = BREAK_REASONS.get(match["break_reason"])
    return match


@router.get("/{tid}/match/geojson", summary="导出匹配结果 GeoJSON")
def get_match_geojson(tid: int):
    traj = db.get_trajectory(tid)
    if traj is None:
        raise HTTPException(404, f"轨迹 {tid} 不存在")
    match = db.get_latest_match(tid)
    if match is None:
        raise HTTPException(404, f"轨迹 {tid} 尚无匹配结果")

    features = []

    # 原始轨迹
    raw_coords = [[p["lon"], p["lat"]] for p in traj["points"]]
    features.append({
        "type": "Feature",
        "properties": {"kind": "raw_trace", "trajectory_id": tid},
        "geometry": {"type": "LineString", "coordinates": raw_coords},
    })

    # 匹配路径（拼接各路段）
    matched_coords: list = []
    for seg in match["segments"]:
        for c in seg["coords"]:
            if not matched_coords or c != matched_coords[-1]:
                matched_coords.append(c)
    if matched_coords:
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "matched_path",
                "trajectory_id": tid,
                "match_id": match["id"],
                "status": match["status"],
                "matched_points": match["matched_points"],
                "total_points": match["total_points"],
                "break_reason": match["break_reason"],
            },
            "geometry": {"type": "LineString", "coordinates": matched_coords},
        })

    # 每个观测点的匹配位置
    for p in match["points"]:
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "matched_point",
                "seq": p["seq"],
                "road": p["road"],
                "dist_m": p["dist_m"],
                "orig_lon": p["orig_lon"],
                "orig_lat": p["orig_lat"],
            },
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
        })

    fc = {"type": "FeatureCollection", "features": features}
    return JSONResponse(content=fc, media_type="application/geo+json")
