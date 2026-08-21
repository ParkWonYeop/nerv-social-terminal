# -*- coding: utf-8 -*-
"""SQLite 상태 저장소.

모든 행이 player 로 묶이고, 관계 데이터는 char(캐릭터)로도 나뉜다.
재화(LCL)·근무 기록은 캐릭터와 무관한 전역 데이터라 char='' 에 둔다.

훅(짧은 프로세스)과 게임(긴 프로세스)이 동시에 붙으므로 WAL + busy_timeout.
"""
import datetime as _dt
import sqlite3
from contextlib import contextmanager

from . import characters, config, identity

PLAYER = identity.player()

CHAR = "rei"                 # 활성 캐릭터. 게임 시작 시 set_char()로 정한다.
CHARS = characters.IDS       # 존재하는 캐릭터 전부 (훅이 전원에게 반영할 때)

# 캐릭터와 무관한 전역 state 키 — char='' 행에 저장된다.
GLOBAL_KEYS = {"lcl", "total_earned", "fail_streak", "streak_days",
               "last_day", "last_active", "created"}


def set_char(char_id: str) -> None:
    global CHAR
    CHAR = char_id


def _ck(key: str, char=None) -> str:
    """state 키의 char 라우팅. 전역 키는 '' 로 간다."""
    if key in GLOBAL_KEYS:
        return ""
    return char if char is not None else CHAR


SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    player TEXT NOT NULL,
    char   TEXT NOT NULL DEFAULT '',
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    PRIMARY KEY (player, char, key)
);
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player     TEXT NOT NULL,
    char       TEXT NOT NULL DEFAULT '',
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    delta_lcl  INTEGER NOT NULL DEFAULT 0,
    delta_aff  INTEGER NOT NULL DEFAULT 0,
    reason     TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger ON ledger(player, id);
CREATE TABLE IF NOT EXISTS daily (
    player   TEXT NOT NULL,
    day      TEXT NOT NULL,
    tools    INTEGER NOT NULL DEFAULT 0,
    edits    INTEGER NOT NULL DEFAULT 0,
    commits  INTEGER NOT NULL DEFAULT 0,
    fails    INTEGER NOT NULL DEFAULT 0,
    lcl      INTEGER NOT NULL DEFAULT 0,
    stops    INTEGER NOT NULL DEFAULT 0,
    llm      INTEGER NOT NULL DEFAULT 0,
    api      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player, day)
);
CREATE TABLE IF NOT EXISTS dialogue (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    player  TEXT NOT NULL,
    char    TEXT NOT NULL DEFAULT 'rei',
    ts      TEXT NOT NULL,
    role    TEXT NOT NULL,
    text    TEXT NOT NULL,
    emotion TEXT,
    sess    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dialogue ON dialogue(player, char, id);
CREATE TABLE IF NOT EXISTS memory (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    player    TEXT NOT NULL,
    char      TEXT NOT NULL DEFAULT 'rei',
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    text      TEXT NOT NULL,
    weight    INTEGER NOT NULL DEFAULT 1,
    hits      INTEGER NOT NULL DEFAULT 0,
    last_used TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memory ON memory(player, char, id);
CREATE TABLE IF NOT EXISTS owned (
    player TEXT NOT NULL,
    char   TEXT NOT NULL DEFAULT 'rei',
    item   TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    given  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player, char, item)
);
CREATE TABLE IF NOT EXISTS flags (
    player TEXT NOT NULL,
    char   TEXT NOT NULL DEFAULT 'rei',
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    PRIMARY KEY (player, char, key)
);
CREATE TABLE IF NOT EXISTS work_scan (
    player TEXT NOT NULL,
    path   TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    mtime  REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (player, path)
);
CREATE TABLE IF NOT EXISTS work_facts (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    player TEXT NOT NULL,
    day    TEXT NOT NULL,
    ts     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    text   TEXT NOT NULL,
    sid    TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts ON work_facts(player, day, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_uniq
    ON work_facts(player, day, kind, text);
"""

# 전역 기본값 (char='')
GLOBAL_DEFAULTS = {
    "lcl": "0",
    "total_earned": "0",
    "fail_streak": "0",
    "streak_days": "0",
    "last_day": "",
    "last_active": "",
    "created": "",
}

# 캐릭터별 기본값. affection 등 시작 수치는 characters.*.start 가 덮어쓴다.
CHAR_DEFAULTS = {
    "affection": str(config.AFF_START),
    "trust": str(config.TRUST_START),
    "interest": str(config.INTEREST_START),
    "patience": str(config.PATIENCE_START),
    "mood": "flat",
    "impression": "",
    "doubts": "",
    "last_greeting": "",
    "neglect_applied": "0",
    "neglect_total": "0",
    "neglect_notify": "0",
    "neglect_notify_days": "0",
    "met_count": "0",
    "turns": "0",
    "consolidated_upto": "0",
    "patience_ts": "",
}


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ── 마이그레이션 (v1: char 열 없음 → v2) ───────────────────────────────
def _columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _migrate(con) -> None:
    """char 열이 없는 옛 저장소를 승격한다. 기존 데이터는 전부 레이의 것."""
    cols = _columns(con, "state")
    if not cols or "char" in cols:
        return

    con.execute("BEGIN IMMEDIATE")
    try:
        # state: 전역 키는 char='', 나머지는 'rei'
        marks = ",".join("?" * len(GLOBAL_KEYS))
        con.execute("ALTER TABLE state RENAME TO state_v1")
        con.execute("""CREATE TABLE state (
            player TEXT NOT NULL, char TEXT NOT NULL DEFAULT '',
            key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (player, char, key))""")
        con.execute(
            f"INSERT INTO state(player,char,key,value) "
            f"SELECT player, CASE WHEN key IN ({marks}) THEN '' ELSE 'rei' END,"
            f" key, value FROM state_v1", tuple(GLOBAL_KEYS))
        con.execute("DROP TABLE state_v1")

        # owned / flags: 복합 PK 라 재구축
        con.execute("ALTER TABLE owned RENAME TO owned_v1")
        con.execute("""CREATE TABLE owned (
            player TEXT NOT NULL, char TEXT NOT NULL DEFAULT 'rei',
            item TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, given INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player, char, item))""")
        con.execute("INSERT INTO owned(player,char,item,count,given) "
                    "SELECT player,'rei',item,count,given FROM owned_v1")
        con.execute("DROP TABLE owned_v1")

        con.execute("ALTER TABLE flags RENAME TO flags_v1")
        con.execute("""CREATE TABLE flags (
            player TEXT NOT NULL, char TEXT NOT NULL DEFAULT 'rei',
            key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (player, char, key))""")
        con.execute("INSERT INTO flags(player,char,key,value) "
                    "SELECT player,'rei',key,value FROM flags_v1")
        con.execute("DROP TABLE flags_v1")

        # id PK 테이블은 열 추가로 충분 (기존 행은 전부 레이)
        for table in ("ledger", "dialogue", "memory"):
            if "char" not in _columns(con, table):
                con.execute(f"ALTER TABLE {table} "
                            f"ADD COLUMN char TEXT NOT NULL DEFAULT 'rei'")
        con.commit()
    except Exception:
        con.rollback()
        raise


# 나중에 생긴 열. CREATE TABLE IF NOT EXISTS 는 이미 있는 테이블에
# 열을 붙여 주지 않으므로, 옛 저장소를 위해 따로 확인한다.
LATER_COLUMNS = [
    ("daily", "api", "INTEGER NOT NULL DEFAULT 0"),
    ("work_facts", "agent", "TEXT NOT NULL DEFAULT 'claude'"),
]


def _ensure_columns(con) -> None:
    for table, column, decl in LATER_COLUMNS:
        cols = _columns(con, table)
        if cols and column not in cols:
            try:
                con.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass          # 다른 프로세스가 먼저 붙였다


def init(con: sqlite3.Connection) -> None:
    _migrate(con)
    con.executescript(SCHEMA)
    _ensure_columns(con)
    for k, v in GLOBAL_DEFAULTS.items():
        con.execute(
            "INSERT OR IGNORE INTO state(player,char,key,value) VALUES(?,'',?,?)",
            (PLAYER, k, v))
    for cid in CHARS:
        defaults = dict(CHAR_DEFAULTS)
        for k, v in characters.get(cid).start.items():
            defaults[k] = str(v)
        for k, v in defaults.items():
            con.execute(
                "INSERT OR IGNORE INTO state(player,char,key,value) "
                "VALUES(?,?,?,?)", (PLAYER, cid, k, v))
    con.execute("UPDATE state SET value=? "
                "WHERE player=? AND char='' AND key='created' AND value=''",
                (now(), PLAYER))


# ── state ──────────────────────────────────────────────────────────────
def get(con, key: str, default: str = "", char=None) -> str:
    r = con.execute(
        "SELECT value FROM state WHERE player=? AND char=? AND key=?",
        (PLAYER, _ck(key, char), key)).fetchone()
    return r["value"] if r else default


def geti(con, key: str, default: int = 0, char=None) -> int:
    try:
        return int(get(con, key, str(default), char))
    except (TypeError, ValueError):
        return default


def put(con, key: str, value, char=None) -> None:
    con.execute(
        "INSERT INTO state(player,char,key,value) VALUES(?,?,?,?) "
        "ON CONFLICT(player,char,key) DO UPDATE SET value=excluded.value",
        (PLAYER, _ck(key, char), key, str(value)))


def bump(con, key: str, delta: int, lo=None, hi=None, char=None) -> int:
    v = geti(con, key, char=char) + delta
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    put(con, key, v, char=char)
    return v


# ── 일일 집계 (전역 — 근무 기록) ───────────────────────────────────────
def daily_row(con, day: str = None):
    day = day or today()
    con.execute("INSERT OR IGNORE INTO daily(player,day) VALUES(?,?)",
                (PLAYER, day))
    return con.execute("SELECT * FROM daily WHERE player=? AND day=?",
                       (PLAYER, day)).fetchone()


def daily_bump(con, field: str, delta: int = 1, day: str = None) -> None:
    day = day or today()
    con.execute("INSERT OR IGNORE INTO daily(player,day) VALUES(?,?)",
                (PLAYER, day))
    con.execute(f"UPDATE daily SET {field}={field}+? WHERE player=? AND day=?",
                (delta, PLAYER, day))


# ── 기록 ───────────────────────────────────────────────────────────────
def log(con, kind, delta_lcl=0, delta_aff=0, reason="", session_id="",
        char=None):
    if char is None:
        char = CHAR if delta_aff else ""
    con.execute(
        "INSERT INTO ledger(player,char,ts,kind,delta_lcl,delta_aff,reason,"
        "session_id) VALUES(?,?,?,?,?,?,?,?)",
        (PLAYER, char, now(), kind, delta_lcl, delta_aff, reason, session_id))


def say(con, role: str, text: str, emotion: str = "", sess: str = "",
        char=None):
    con.execute(
        "INSERT INTO dialogue(player,char,ts,role,text,emotion,sess) "
        "VALUES(?,?,?,?,?,?,?)",
        (PLAYER, char if char is not None else CHAR,
         now(), role, text, emotion, sess))


def flag(con, key, value=None, char=None):
    c = char if char is not None else CHAR
    if value is None:
        r = con.execute(
            "SELECT value FROM flags WHERE player=? AND char=? AND key=?",
            (PLAYER, c, key)).fetchone()
        return r["value"] if r else ""
    con.execute(
        "INSERT INTO flags(player,char,key,value) VALUES(?,?,?,?) "
        "ON CONFLICT(player,char,key) DO UPDATE SET value=excluded.value",
        (PLAYER, c, key, str(value)))
    return str(value)


def players(con):
    """이 저장소에 기록이 있는 사용자 목록(진단용)."""
    return [r["player"] for r in con.execute(
        "SELECT DISTINCT player FROM state ORDER BY player")]


# ── 초기화 ─────────────────────────────────────────────────────────────
#
# 지우는 범위를 셋으로 나눈다. '전부 지움' 하나만 두면 관계만 다시
# 시작하고 싶은 사람이 근무 기록까지 잃는다.
#
# 어느 것이든 **이 플레이어의 것만** 지운다. 홈을 공유하는 경우에도
# 남의 기록은 건드리지 않는다.

# 캐릭터별로 나뉘는 테이블
CHAR_TABLES = ("dialogue", "memory", "owned", "flags")


def reset_character(con, char_id: str) -> None:
    """한 사람과의 관계를 처음으로. 재화와 근무 기록은 남는다."""
    con.execute("DELETE FROM state WHERE player=? AND char=?",
                (PLAYER, char_id))
    for table in CHAR_TABLES:
        con.execute(f"DELETE FROM {table} WHERE player=? AND char=?",
                    (PLAYER, char_id))
    con.execute("DELETE FROM ledger WHERE player=? AND char=?",
                (PLAYER, char_id))
    _seed_character(con, char_id)
    con.commit()


def reset_relationships(con) -> None:
    """모든 사람과의 관계를 처음으로. 재화와 근무 기록은 남는다."""
    for cid in CHARS:
        reset_character(con, cid)


def reset_everything(con) -> None:
    """전부. 재화·근무 기록·장부까지. 되돌릴 수 없다."""
    for table in ("state", "ledger", "daily", "dialogue", "memory",
                  "owned", "flags", "work_scan", "work_facts"):
        con.execute(f"DELETE FROM {table} WHERE player=?", (PLAYER,))
    con.commit()
    init(con)


def _seed_character(con, char_id: str) -> None:
    defaults = dict(CHAR_DEFAULTS)
    char = characters.get(char_id)
    if char is not None:
        for k, v in char.start.items():
            defaults[k] = str(v)
    for k, v in defaults.items():
        con.execute("INSERT OR REPLACE INTO state(player,char,key,value) "
                    "VALUES(?,?,?,?)", (PLAYER, char_id, k, v))


def counts(con) -> dict:
    """초기화 화면에 '무엇이 얼마나 지워지는가' 를 보여주려고."""
    def one(sql, args=()):
        row = con.execute(sql, args).fetchone()
        return row[0] if row else 0

    return {
        "memory": one("SELECT COUNT(*) FROM memory WHERE player=?", (PLAYER,)),
        "dialogue": one("SELECT COUNT(*) FROM dialogue WHERE player=?",
                        (PLAYER,)),
        "days": one("SELECT COUNT(*) FROM daily WHERE player=?", (PLAYER,)),
        "facts": one("SELECT COUNT(*) FROM work_facts WHERE player=?",
                     (PLAYER,)),
        "earned": geti(con, "total_earned"),
        "lcl": geti(con, "lcl"),
    }
