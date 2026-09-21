"""轨迹提交与查询。"""
from fastapi import APIRouter, HTTPException, Request

from .. import database as db
from ..mapmatch.hmm import BREAK_REASONS
from ..schemas import TrajectoryIn
from .matching import build_params, run_match

router = APIRouter(prefix="/api/trajectories", tags=["trajectories"])


@router.post("", status_code=201, summary="提交轨迹（默认立即执行路网匹配）")
def submit_trajectory(payload: TrajectoryIn, request: Request):
    points = [
        {"lon": p.lon, "lat": p.lat, "ts": p.timestamp, "speed": p.speed}
        for p in payload.points
    ]
    tid = db.create_trajectory(payload.name, points)
    resp = {"trajectory_id": tid, "point_count": len(points), "status": "pending"}
    if payload.auto_match:
        result, match_id = run_match(request.app.state.matcher, tid, points, payload.params)
        resp["status"] = result.status
        resp["match"] = {
            "match_id": match_id,
            "status": result.status,
            "matched_points": result.matched_points,
            "total_points": result.total_points,
            "break_reason": result.break_reason,
            "break_reason_text": BREAK_REASONS.get(result.break_reason),
            "break_seq": result.break_seq,
        }
    return resp


@router.get("", summary="轨迹列表")
def list_trajectories():
    return {"trajectories": db.list_trajectories()}


@router.get("/{tid}", summary="轨迹详情（含原始观测点）")
def get_trajectory(tid: int):
    traj = db.get_trajectory(tid)
    if traj is None:
        raise HTTPException(404, f"轨迹 {tid} 不存在")
    return traj
