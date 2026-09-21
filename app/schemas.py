"""请求/响应模型。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PointIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="纬度")
    lon: float = Field(..., ge=-180, le=180, description="经度")
    timestamp: Optional[float] = Field(None, description="Unix 秒，也接受 ISO8601 字符串")
    speed: Optional[float] = Field(None, ge=0, description="瞬时速度（米/秒），可选")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_ts(cls, v):
        if v is None or isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        raise ValueError("timestamp 必须是 Unix 秒或 ISO8601 字符串")


class MatchParamsIn(BaseModel):
    """匹配参数覆盖；缺省字段使用服务端配置。"""

    candidate_radius_m: Optional[float] = Field(None, gt=0, le=500)
    max_candidates: Optional[int] = Field(None, ge=1, le=32)
    sigma_m: Optional[float] = Field(None, gt=0)
    beta_m: Optional[float] = Field(None, gt=0)
    max_speed_mps: Optional[float] = Field(None, gt=0)
    speed_weight: Optional[float] = Field(None, ge=0)


class TrajectoryIn(BaseModel):
    name: str = Field("trajectory", max_length=200)
    points: list[PointIn] = Field(..., min_length=2)
    auto_match: bool = Field(True, description="提交后立即执行匹配")
    params: Optional[MatchParamsIn] = None
