# GPS 轨迹路网匹配 API

基于 **FastAPI + SQLite** 的可解释 GPS 轨迹路网匹配（Map Matching）服务。
核心算法为**候选道路投影 + 隐马尔可夫模型（HMM）Viterbi 解码**：对每个定位点按半径筛选候选道路投影，综合**投影距离（发射概率）**、**道路连通性（最短路径转移概率）**与**速度转移代价**选出全局最优路径；无可行路径时返回断段位置与原因。

内置一份示例城市道路网（`app/data/roads.geojson`，7x7 网格 + 对角快速路 + 环路），启动时自动加载，无需任何外部数据即可开箱试用。

## 快速开始

### Docker Compose（推荐）

```bash
docker compose up --build
```

服务监听 **8000** 端口：

- 交互式文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

SQLite 数据库持久化在命名卷 `mapmatch-data`（容器内 `/data/mapmatch.db`）。

### 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 重新生成内置路网（可选）

```bash
python3 scripts/gen_roads.py   # 重新生成 app/data/roads.geojson 与 examples/*.json
```

## 配置

全部通过环境变量覆盖（`docker-compose.yml` 中已列出常用项）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `MM_DB_PATH` | `./mapmatch.db`（容器内 `/data/mapmatch.db`） | SQLite 数据库路径 |
| `MM_ROADS_GEOJSON` | `app/data/roads.geojson` | 道路网 GeoJSON（LineString/MultiLineString，支持 `name`、`maxspeed` 属性） |
| `MM_CANDIDATE_RADIUS_M` | `50` | 候选道路搜索半径（米） |
| `MM_MAX_CANDIDATES` | `8` | 每个观测点最多保留的候选数 |
| `MM_SIGMA_M` | `15` | 发射概率高斯噪声 σ（米），越大越容忍偏离道路的观测 |
| `MM_BETA_M` | `30` | 转移概率指数衰减 β（米），越大越容忍绕行 |
| `MM_MAX_SPEED_MPS` | `60` | 可行速度上限（米/秒），超过则转移被剪枝 |
| `MM_SPEED_WEIGHT` | `1.0` | 速度代价权重（推算速度与观测速度偏差的惩罚力度） |
| `MM_GRID_CELL_M` | `100` | 空间网格索引单元大小（米） |

单次匹配也可在请求体中临时覆盖上述参数（见下文 `POST /api/trajectories/{id}/match`）。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/config` | 当前匹配参数 |
| `GET` | `/api/roads/geojson` | 查看已加载的道路网 |
| `GET` | `/api/break-reasons` | 断段原因码说明 |
| `POST` | `/api/trajectories` | 提交轨迹（默认立即匹配，`auto_match=false` 可关闭） |
| `GET` | `/api/trajectories` | 轨迹列表 |
| `GET` | `/api/trajectories/{id}` | 轨迹详情（含原始点） |
| `POST` | `/api/trajectories/{id}/match` | 执行/重新执行匹配，可临时覆盖参数 |
| `GET` | `/api/trajectories/{id}/match` | 最近一次匹配结果（点级 + 路段级明细） |
| `GET` | `/api/trajectories/{id}/match/geojson` | 导出 GeoJSON（原始轨迹、匹配路径、逐点匹配位置） |

### 轨迹点字段

```json
{
  "lon": 116.378, "lat": 39.902,
  "timestamp": 1758500000,          // 可选，Unix 秒或 ISO8601 字符串；提供后启用速度约束
  "speed": 12.5                     // 可选，瞬时速度（米/秒）
}
```

### 示例

```bash
# 提交轨迹并匹配（examples/trajectory.json 为内置示例）
curl -X POST http://localhost:8000/api/trajectories \
  -H 'Content-Type: application/json' \
  -d @examples/trajectory.json

# 查询匹配结果
curl http://localhost:8000/api/trajectories/1/match

# 导出 GeoJSON（可直接拖入 geojson.io 查看）
curl http://localhost:8000/api/trajectories/1/match/geojson -o matched.geojson

# 用更大搜索半径重新匹配
curl -X POST http://localhost:8000/api/trajectories/1/match \
  -H 'Content-Type: application/json' \
  -d '{"candidate_radius_m": 100, "sigma_m": 25}'
```

`examples/trajectory_break.json` 演示断段：轨迹中途驶离路网，返回：

```json
{
  "status": "partial",
  "matched_points": 5,
  "total_points": 9,
  "break_reason": "no_candidates_within_radius",
  "break_seq": 5
}
```

## 算法说明

1. **候选投影**：对每个观测点，用网格空间索引找出半径内的道路线段，计算点到线段的垂直投影，得到候选集 `(edge, fraction, dist)`。
2. **发射概率**：观测点到候选投影的距离服从零均值高斯分布 `N(0, σ)`。
3. **转移概率**：对相邻观测点的候选对，用多源 Dijkstra 计算沿路网的最短路径距离 `route`，与两点直线距离 `linear` 的差服从指数分布 `exp(-|route-linear|/β)`；若提供了时间戳，则计算推算速度 `route/Δt`——超过 `MM_MAX_SPEED_MPS` 的转移直接剪枝，与观测速度的偏差按 `MM_SPEED_WEIGHT` 加权计入代价。
4. **Viterbi 解码**：在候选格图上求全局最优状态序列，再沿路网重建匹配路径几何。

### 断段原因码

| 原因码 | 含义 |
|---|---|
| `no_candidates_within_radius` | 该点半径内没有任何候选道路（点偏离路网或半径过小） |
| `no_route_between_candidates` | 相邻点的候选道路在路网中不连通 |
| `speed_limit_exceeded` | 可达但推算速度超过上限，转移被剪枝 |
| `no_feasible_transition` | 不连通与超速并存 |

匹配状态：`matched`（全部匹配）/ `partial`（最长可行前缀 + 断段信息）/ `failed`（首点即失败）。

## 目录结构

```
app/
  main.py              # FastAPI 入口，启动时加载路网、初始化 SQLite
  config.py            # 环境变量配置
  database.py          # SQLite 持久化（轨迹/观测点/匹配结果）
  schemas.py           # 请求模型
  mapmatch/
    geo.py             # haversine、局部平面投影、点到线段投影
    network.py         # 路网图、网格空间索引、多源 Dijkstra
    hmm.py             # 候选生成、发射/转移概率、Viterbi 解码
  routers/
    trajectories.py    # 轨迹提交与查询
    matching.py        # 匹配执行、结果查询、GeoJSON 导出
  data/roads.geojson   # 内置示例道路网
scripts/gen_roads.py   # 路网与示例轨迹生成脚本
examples/              # 示例轨迹（正常 / 断段）
Dockerfile
docker-compose.yml
```
