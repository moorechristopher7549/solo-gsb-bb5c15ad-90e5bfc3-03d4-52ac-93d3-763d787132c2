"""FastAPI 应用入口：启动时加载内置道路 GeoJSON 并初始化 SQLite。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import database as db
from .config import settings
from .mapmatch.hmm import BREAK_REASONS
from .mapmatch.network import RoadNetwork
from .routers import matching, trajectories


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    network = RoadNetwork.from_geojson(settings.roads_geojson, settings.grid_cell_m)
    app.state.network = network
    from .mapmatch.hmm import HMMMatcher
    app.state.matcher = HMMMatcher(network)
    yield


app = FastAPI(
    title="GPS 轨迹路网匹配 API",
    version="1.0.0",
    description="基于候选道路投影 + 隐马尔可夫 Viterbi 解码的可解释路网匹配服务",
    lifespan=lifespan,
)
app.include_router(trajectories.router)
app.include_router(matching.router)


@app.get("/health", summary="健康检查")
def health():
    return {"status": "ok"}


@app.get("/api/config", summary="当前匹配参数配置")
def get_config():
    return {
        "candidate_radius_m": settings.candidate_radius_m,
        "max_candidates": settings.max_candidates,
        "sigma_m": settings.sigma_m,
        "beta_m": settings.beta_m,
        "max_speed_mps": settings.max_speed_mps,
        "speed_weight": settings.speed_weight,
        "grid_cell_m": settings.grid_cell_m,
        "db_path": settings.db_path,
        "roads_geojson": settings.roads_geojson,
    }


@app.get("/api/roads/geojson", summary="查看已加载的道路网 GeoJSON")
def roads_geojson():
    from fastapi.responses import JSONResponse
    return JSONResponse(content=app.state.network.raw_geojson,
                        media_type="application/geo+json")


@app.get("/api/break-reasons", summary="断段原因码说明")
def break_reasons():
    return BREAK_REASONS
