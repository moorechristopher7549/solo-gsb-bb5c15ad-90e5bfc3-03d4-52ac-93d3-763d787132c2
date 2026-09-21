"""地理计算工具：haversine 距离、局部平面投影、点到线段投影。"""
import math

EARTH_R_M = 6371000.0
M_PER_DEG_LAT = 111320.0


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两经纬度点之间的大圆距离（米）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


class LocalProjection:
    """以某纬度为参考的等距圆柱近似，把小范围经纬度当作平面坐标（米）。"""

    def __init__(self, lat0: float):
        self.lat0 = lat0
        self.kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return lon * self.kx, lat * M_PER_DEG_LAT

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        return x / self.kx, y / M_PER_DEG_LAT


def project_to_segment(
    lon: float, lat: float,
    alon: float, alat: float,
    blon: float, blat: float,
    proj: LocalProjection,
) -> tuple[float, float, float, float]:
    """点到线段的最短投影。

    返回 (fraction, dist_m, proj_lon, proj_lat)：
    fraction 为投影点在线段 a->b 上的比例（0..1）。
    """
    px, py = proj.xy(lon, lat)
    ax, ay = proj.xy(alon, alat)
    bx, by = proj.xy(blon, blat)
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    qx, qy = ax + t * dx, ay + t * dy
    qlon, qlat = proj.lonlat(qx, qy)
    return t, math.hypot(px - qx, py - qy), qlon, qlat
