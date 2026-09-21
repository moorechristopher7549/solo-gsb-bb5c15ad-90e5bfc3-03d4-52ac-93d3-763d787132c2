"""全局配置：全部可通过环境变量覆盖（前缀 MM_）。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Settings:
    # 存储与数据
    db_path: str = os.environ.get("MM_DB_PATH", str(BASE_DIR.parent / "mapmatch.db"))
    roads_geojson: str = os.environ.get(
        "MM_ROADS_GEOJSON", str(BASE_DIR / "data" / "roads.geojson")
    )

    # 候选道路投影
    candidate_radius_m: float = _float("MM_CANDIDATE_RADIUS_M", 50.0)  # 候选搜索半径（米）
    max_candidates: int = _int("MM_MAX_CANDIDATES", 8)                 # 每点最多候选数
    grid_cell_m: float = _float("MM_GRID_CELL_M", 100.0)               # 空间网格索引单元（米）

    # 隐马尔可夫模型
    sigma_m: float = _float("MM_SIGMA_M", 15.0)        # 发射概率高斯噪声 σ（米）
    beta_m: float = _float("MM_BETA_M", 30.0)          # 转移概率指数衰减 β（米）
    max_speed_mps: float = _float("MM_MAX_SPEED_MPS", 60.0)  # 可行速度上限（米/秒）
    speed_weight: float = _float("MM_SPEED_WEIGHT", 1.0)     # 速度代价权重


settings = Settings()
