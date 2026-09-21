"""生成项目内置道路网 app/data/roads.geojson 与示例轨迹 examples/*.json。

路网：7x7 城市网格（交叉口共享节点）+ 一条对角快速路 + 一条矩形环路。
运行：python3 scripts/gen_roads.py
"""
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

LONS = [round(116.370 + 0.008 * k, 6) for k in range(7)]   # 116.370 .. 116.418
LATS = [round(39.890 + 0.006 * j, 6) for j in range(7)]    # 39.890 .. 39.926

EW_NAMES = ["南纬一路", "南纬二路", "长安街", "平安大街", "北纬一路", "北纬二路", "北三环辅路"]
NS_NAMES = ["西二环路", "西直门外大街", "王府井大街", "东单北大街", "东二环路", "工体东路", "东三环路"]


def feature(name, coords, maxspeed, kind):
    return {
        "type": "Feature",
        "properties": {"name": name, "maxspeed": maxspeed, "kind": kind},
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def build_roads() -> dict:
    features = []
    # 东西向道路：顶点落在每条南北向道路处，交叉口共享节点
    for j, lat in enumerate(LATS):
        coords = [[lon, lat] for lon in LONS]
        features.append(feature(EW_NAMES[j], coords, 60, "arterial"))
    # 南北向道路
    for k, lon in enumerate(LONS):
        coords = [[lon, lat] for lat in LATS]
        features.append(feature(NS_NAMES[k], coords, 60, "arterial"))
    # 对角快速路：恰好穿过网格交叉口
    diag = [[LONS[k], LATS[k]] for k in range(7)]
    features.append(feature("机场快速路", diag, 80, "expressway"))
    # 矩形环路（内圈）
    ring = (
        [[LONS[k], LATS[1]] for k in range(1, 6)]
        + [[LONS[5], LATS[j]] for j in range(2, 6)]
        + [[LONS[k], LATS[5]] for k in range(4, 0, -1)]
        + [[LONS[1], LATS[j]] for j in range(4, 0, -1)]
        + [[LONS[1], LATS[1]]]
    )
    features.append(feature("二环路", ring, 80, "expressway"))
    return {"type": "FeatureCollection", "features": features}


def build_demo_trajectory() -> dict:
    """沿长安街（lat=39.908）由西向东，叠加约 ±8m 高斯噪声。"""
    rng = random.Random(42)
    lat0 = LATS[2]  # 39.902 -> 长安街在 index 2? 取 LATS[2]=39.902 对应"长安街"
    points = []
    t0 = 1758500000.0
    for i in range(13):
        lon = LONS[0] + 0.004 * i
        noisy_lon = lon + rng.gauss(0, 8 / 111320 / 0.77)   # ~8m
        noisy_lat = lat0 + rng.gauss(0, 8 / 111320)
        points.append({
            "lon": round(noisy_lon, 7),
            "lat": round(noisy_lat, 7),
            "timestamp": t0 + 25 * i,
            "speed": round(rng.uniform(11, 14), 1),
        })
    return {"name": "demo-长安街由西向东", "points": points}


def build_break_trajectory() -> dict:
    """前 5 个点在路网内，之后跳到远离路网的位置，用于演示断段原因。"""
    rng = random.Random(7)
    lat0 = LATS[2]
    points = []
    t0 = 1758500000.0
    for i in range(5):
        points.append({
            "lon": round(LONS[0] + 0.004 * i + rng.gauss(0, 5 / 111320), 7),
            "lat": round(lat0 + rng.gauss(0, 5 / 111320), 7),
            "timestamp": t0 + 25 * i,
            "speed": 12.0,
        })
    for i in range(5, 9):  # 跳到路网西南约 5km 外
        points.append({
            "lon": round(116.320 + 0.002 * (i - 5), 7),
            "lat": round(39.850, 7),
            "timestamp": t0 + 25 * i,
            "speed": 12.0,
        })
    return {"name": "demo-中途驶离路网", "points": points}


def main():
    (BASE / "app" / "data").mkdir(parents=True, exist_ok=True)
    (BASE / "examples").mkdir(exist_ok=True)
    roads = build_roads()
    (BASE / "app" / "data" / "roads.geojson").write_text(
        json.dumps(roads, ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "examples" / "trajectory.json").write_text(
        json.dumps(build_demo_trajectory(), ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "examples" / "trajectory_break.json").write_text(
        json.dumps(build_break_trajectory(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"roads: {len(roads['features'])} features")


if __name__ == "__main__":
    main()
