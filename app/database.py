"""SQLite 持久化：轨迹、观测点、匹配结果（点级 + 路段级）。"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS points (
    trajectory_id INTEGER NOT NULL,
    seq           INTEGER NOT NULL,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    ts            REAL,
    speed         REAL,
    PRIMARY KEY (trajectory_id, seq)
);
CREATE TABLE IF NOT EXISTS matches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory_id  INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL,
    break_reason   TEXT,
    break_seq      INTEGER,
    matched_points INTEGER NOT NULL,
    total_points   INTEGER NOT NULL,
    params         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_points (
    match_id   INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    orig_lat   REAL NOT NULL,
    orig_lon   REAL NOT NULL,
    road       TEXT,
    edge_id    INTEGER,
    dist_m     REAL,
    emission   REAL,
    transition REAL
);
CREATE TABLE IF NOT EXISTS match_segments (
    match_id INTEGER NOT NULL,
    seq      INTEGER NOT NULL,
    road     TEXT,
    length_m REAL,
    coords   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.executescript(SCHEMA)


# ---------- 轨迹 ----------

def create_trajectory(name: str, points: list[dict]) -> int:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO trajectories(name, created_at, point_count) VALUES (?,?,?)",
            (name, _now(), len(points)),
        )
        tid = cur.lastrowid
        conn.executemany(
            "INSERT INTO points(trajectory_id, seq, lat, lon, ts, speed) VALUES (?,?,?,?,?,?)",
            [(tid, i, p["lat"], p["lon"], p.get("ts"), p.get("speed"))
             for i, p in enumerate(points)],
        )
    return tid


def list_trajectories() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, point_count, status FROM trajectories ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_trajectory(tid: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, name, created_at, point_count, status FROM trajectories WHERE id=?",
            (tid,),
        ).fetchone()
        if row is None:
            return None
        pts = conn.execute(
            "SELECT seq, lat, lon, ts, speed FROM points WHERE trajectory_id=? ORDER BY seq",
            (tid,),
        ).fetchall()
    out = dict(row)
    out["points"] = [dict(p) for p in pts]
    return out


def update_status(tid: int, status: str) -> None:
    with _lock, _conn() as conn:
        conn.execute("UPDATE trajectories SET status=? WHERE id=?", (status, tid))


# ---------- 匹配结果 ----------

def save_match(tid: int, result, params: dict) -> int:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO matches(trajectory_id, created_at, status, break_reason, break_seq,"
            " matched_points, total_points, params) VALUES (?,?,?,?,?,?,?,?)",
            (tid, _now(), result.status, result.break_reason, result.break_seq,
             result.matched_points, result.total_points, json.dumps(params)),
        )
        mid = cur.lastrowid
        conn.executemany(
            "INSERT INTO match_points(match_id, seq, lat, lon, orig_lat, orig_lon,"
            " road, edge_id, dist_m, emission, transition) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(mid, p["seq"], p["matched"]["lat"], p["matched"]["lon"],
              p["orig"]["lat"], p["orig"]["lon"], p["road"], p["edge_id"],
              p["dist_m"], p["emission_logp"], p.get("transition_logp"))
             for p in result.points],
        )
        conn.executemany(
            "INSERT INTO match_segments(match_id, seq, road, length_m, coords) VALUES (?,?,?,?,?)",
            [(mid, s["seq"], s["road"], s["length_m"], json.dumps(s["coords"]))
             for s in result.segments],
        )
    return mid


def get_latest_match(tid: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM matches WHERE trajectory_id=? ORDER BY id DESC LIMIT 1", (tid,)
        ).fetchone()
        if row is None:
            return None
        match = dict(row)
        match["params"] = json.loads(match["params"])
        pts = conn.execute(
            "SELECT seq, lat, lon, orig_lat, orig_lon, road, edge_id, dist_m, emission, transition"
            " FROM match_points WHERE match_id=? ORDER BY seq", (match["id"],)
        ).fetchall()
        segs = conn.execute(
            "SELECT seq, road, length_m, coords FROM match_segments WHERE match_id=? ORDER BY seq",
            (match["id"],),
        ).fetchall()
    match["points"] = [dict(p) for p in pts]
    match["segments"] = [
        {**{k: s[k] for k in ("seq", "road", "length_m")}, "coords": json.loads(s["coords"])}
        for s in segs
    ]
    return match
