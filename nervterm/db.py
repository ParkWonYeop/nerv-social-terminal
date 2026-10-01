# -*- coding: utf-8 -*-
"""SQLite 상태 저장소.

모든 행이 player 로 묶이고, 관계 데이터는 char(캐릭터)로도 나뉜다.
재화(LCL)·근무 기록은 캐릭터와 무관한 전역 데이터라 char='' 에 둔다.

훅(짧은 프로세스)과 게임(긴 프로세스)이 동시에 붙으므로 WAL + busy_timeout.
"""
import datetime as _dt
import os
import sqlite3
from contextlib import contextmanager

from . import config, identity

PLAYER = identity.player()

CHAR = "rei"                 # 활성 캐릭터. 게임 시작 시 set_char()로 정한다.

# 캐릭터와 무관한 전역 state 키 — char='' 행에 저장된다.
GLOBAL_KEYS = {"lcl", "total_earned", "fail_streak", "streak_days",
               "last_day", "last_active", "created",
               "local_since", "local_repos_at"}


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
CREATE INDEX IF NOT EXISTS idx_ledger_kind ON ledger(player, kind, ts);
-- 근무 사건 — 원시 기록에서 규칙으로 뽑은 '이야깃거리'. (kind, key) 로
-- 중복을 막는다: 새벽 근무는 하루에 한 번, 새 저장소는 이름당 한 번.
CREATE TABLE IF NOT EXISTS work_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    player TEXT NOT NULL,
    ts     TEXT NOT NULL,
    day    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    key    TEXT NOT NULL DEFAULT '',
    text   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_uniq
    ON work_events(player, kind, key);
-- 처음 본 작업 디렉터리. '새 저장소' 사건의 기준선.
CREATE TABLE IF NOT EXISTS projects (
    player   TEXT NOT NULL,
    name     TEXT NOT NULL,
    first_ts TEXT NOT NULL,
    PRIMARY KEY (player, name)
);
-- 남들 눈에 보이는 일 — 누구와 어디에 갔고 무엇을 줬는가.
-- 같은 단말을 보는 다른 캐릭터가 이걸 안다(social.py). 대화와 기억은
-- 여기 오지 않는다 — 그건 여전히 그 사람과만의 것이다.
CREATE TABLE IF NOT EXISTS social (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    player TEXT NOT NULL,
    char   TEXT NOT NULL,
    ts     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    key    TEXT NOT NULL DEFAULT '',
    label  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_social ON social(player, ts);
-- 커밋 해시 — 어디서 만든 커밋인지. 훅(claude·codex)이 적고, 로컬 판독
-- (local.py)이 '그 밖의 곳에서 만든 커밋' 을 가려낼 때 본다.
CREATE TABLE IF NOT EXISTS commits (
    player TEXT NOT NULL,
    hash   TEXT NOT NULL,
    source TEXT NOT NULL,
    ts     TEXT NOT NULL,
    PRIMARY KEY (player, hash)
);
-- 로컬 판독이 지켜보는 git 저장소와, reflog 를 어디까지 읽었나
CREATE TABLE IF NOT EXISTS repos (
    player TEXT NOT NULL,
    path   TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    mtime  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (player, path)
);
"""

# 저장소 판(版). SCHEMA·GLOBAL_DEFAULTS·CHAR_DEFAULTS·LATER_COLUMNS·_migrate
# 를 바꾸면 반드시 이 숫자를 올린다 — init() 은 판이 맞으면 DDL 전체를
# 건너뛰므로(훅 지연 절감), 올리지 않으면 변경이 조용히 적용되지 않는다.
# v1: char 열 없음 / v2: char 분리 / v3: user_version 도입
# v4: 저장된 LLM 텍스트(기억·인상 등)의 대괄호·개행 정화
# v5: 약속 상태(status/target), 캐릭터별 last_seen, 근무 사건·사교 기록
# v6: 커밋으로 쌓인 자동 호감·신뢰 회수(1회), work_scan.skip
# v7: 'root' 유령 플레이어(데몬 아래 getlogin) 를 실제 사용자로 합침,
#     로컬 에이전트(git 커밋·ollama) 판독용 표
SCHEMA_VERSION = 7

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
    # 이 캐릭터를 마지막으로 찾아온 시각. 방치·관심 감소의 기준이다.
    # 예전에는 전역 last_active(훅이 도구 호출마다 갱신)를 썼다 —
    # 매일 코딩만 하면 2주 동안 안 찾아가도 방치가 0 이었다.
    "last_seen": "",
    # 인사에서 이미 꺼낸 근무 사건의 마지막 id — 같은 소재를 또 꺼내지 않게
    "event_mark": "0",
}


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10.0)
    # 저장소에는 사용자의 프롬프트·커밋 메시지가 담긴다 — 남이 읽을
    # 이유가 없다. 공용 서버(umask 022)에서도 0600 을 보장한다.
    # 소유자가 아니면(공용 배치 등) 실패해도 그대로 간다 — 보험이다.
    for _sfx in ("", "-wal", "-shm"):
        try:
            os.chmod(str(path) + _sfx, 0o600)
        except OSError:
            pass
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    # 1.5초. 락은 짧게 실패시키고 훅이 1회 재시도한다(hook.py).
    # 길게 잡으면 락 경합 시 에이전트의 도구 호출이 그만큼 멈춘다.
    con.execute("PRAGMA busy_timeout=1500")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def session(*, write: bool = False):
    """write=True 는 훅 경로용 — 처음부터 쓰기 락(BEGIN IMMEDIATE)을 잡아
    bump() 류 read-modify-write 문장 사이에 다른 프로세스가 끼지 못하게
    한다. 게임처럼 오래 사는 연결에는 쓰지 않는다(락을 오래 쥐게 된다).

    write=False(게임·say·위젯 캐시)는 **자동 커밋**으로 연다. 파이썬
    sqlite3 의 기본 모드는 DML 앞에 몰래 BEGIN 을 넣고 commit() 까지
    쓰기 락을 쥔다. 게임은 오래 살고 사람의 입력과 LLM 응답을 기다린다 —
    그 사이 아무 데서나 INSERT 하나가 끼면 락이 대기 시간 내내 잡혀
    있었다. 실제로 화면을 다시 그릴 때 daily_row() 의 INSERT OR IGNORE
    가 트랜잭션을 열어 두는 바람에, eva 를 켜 둔 동안 모든 훅이 3.5초씩
    멈추고 적립이 버려졌다. 여러 문장을 묶어야 할 때는 tx() 를 쓴다.
    """
    con = connect()
    if not write:
        con.isolation_level = None
    try:
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def tx(con):
    """여러 문장을 하나로 — 자동 커밋 연결에서 원자성이 필요할 때.

    이미 트랜잭션 안이면(훅 경로) 그대로 둔다. 짧게만 쓴다: 안에서
    사람 입력이나 LLM 호출을 기다리면 안 된다.
    """
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.rollback()
        raise
    con.commit()


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
    # 약속: '' 지키는 중 / kept / broken(감점됨, 아직 기억) / forgotten
    ("memory", "status", "TEXT NOT NULL DEFAULT ''"),
    # 약속의 이행 대상: date:<key> / gift:<key> / visit / rest / ''(말로만)
    ("memory", "target", "TEXT NOT NULL DEFAULT ''"),
    # 근무 일지가 읽지 않을 세션 파일 — Codex 의 승인 검토 스레드처럼
    # 사람이 한 일이 아닌 것. 첫 줄(session_meta)을 본 뒤에 표시한다.
    ("work_scan", "skip", "INTEGER NOT NULL DEFAULT 0"),
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


def known_chars(con) -> tuple:
    """이 저장소가 아는 캐릭터 id.

    저장소에 이미 행이 있으면 **그것만 보고** 답한다. 플러그인을 읽지
    않는다 — 이게 중요하다.

    훅은 도구 호출마다 프로세스로 새로 뜬다. 캐릭터 '데이터' 는 하나도
    안 쓰면서 id 목록 하나 때문에 플러그인 발견·tomllib·설정까지 끌고
    들어가면, 그 비용이 모든 도구 호출에 붙는다. 실제로 그렇게 만들었다가
    훅 한 번이 71ms 에서 86ms 가 됐다.

    저장소가 비어 있을 때만(=첫 실행) 플러그인을 읽어 목록을 만든다.
    새 캐릭터 팩을 깐 뒤 게임을 한 번 켜기 전까지는 훅이 그 사람을
    모르는데, 게임을 켜는 순간 채워지므로 저절로 낫는다.
    """
    rows = con.execute(
        "SELECT DISTINCT char FROM state WHERE player=? AND char<>'' "
        "ORDER BY char", (PLAYER,)).fetchall()
    got = tuple(r[0] for r in rows)
    if got:
        return got
    from . import characters              # 첫 실행에서만 여기까지 온다
    return characters.IDS


def seed_characters(con, ids=None) -> None:
    """캐릭터별 기본값을 채운다. 이미 있는 값은 건드리지 않는다."""
    from . import characters
    for cid in (ids if ids is not None else characters.IDS):
        defaults = dict(CHAR_DEFAULTS)
        char = characters.get(cid)
        if char is not None:
            for k, v in char.start.items():
                defaults[k] = str(v)
        for k, v in defaults.items():
            con.execute(
                "INSERT OR IGNORE INTO state(player,char,key,value) "
                "VALUES(?,?,?,?)", (PLAYER, cid, k, v))


def _backup(con, ver: int) -> None:
    """판 승격 직전 1회 백업 — 마이그레이션이 틀렸을 때의 유일한 복구
    수단이다. 실패해도 진행한다(백업은 보험이지 관문이 아니다)."""
    try:
        path = config.db_path()
        if not path.is_file():
            return
        bak = path.with_name(path.name + f".bak-v{ver}")
        if bak.exists():
            return
        if con.in_transaction:
            # session(write=True)이 방금 연 빈 트랜잭션 — checkpoint 는
            # 트랜잭션 안에서 못 돈다. 승격 경로는 어차피 executescript
            # 가 커밋하므로 여기서 닫아도 잃는 것이 없다.
            con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        import shutil
        shutil.copy2(path, bak)
    except Exception:
        pass


def _sanitize_stored_text(con) -> None:
    """v4 — normalize 정화 도입 이전에 저장된 LLM 텍스트를 한 번 씻는다.

    기억·인상은 이후 모든 턴의 시스템 프롬프트에 실리므로, 대괄호가
    남아 있으면 프롬프트 구획 위조가 과거 데이터를 통해 계속된다.
    replace 는 멱등이라 판 승격마다 다시 돌아도 해가 없다.
    """
    strip = ("replace(replace(replace(replace({c},'[',' '),']',' '),"
             "char(10),' '),char(13),' ')")
    con.execute("UPDATE memory SET text=" + strip.format(c="text") +
                " WHERE text GLOB '*[[]*' OR text GLOB '*]*'"
                " OR text LIKE '%' || char(10) || '%'")
    con.execute("UPDATE state SET value=" + strip.format(c="value") +
                " WHERE key IN ('impression','doubts','mood')"
                " AND (value GLOB '*[[]*' OR value GLOB '*]*')")


def _upgrade_v5(con) -> None:
    """v5 — 플래그로 흩어져 있던 약속 상태를 열로 옮기고, 캐릭터별
    last_seen 을 지난 대화에서 채운다. 멱등이다."""
    con.execute(
        "UPDATE memory SET status='forgotten' WHERE kind='promise' "
        "AND status='' AND EXISTS (SELECT 1 FROM flags f WHERE "
        "f.player=memory.player AND f.char=memory.char AND "
        "f.key='promise_done_' || memory.id AND f.value<>'')")
    con.execute(
        "UPDATE memory SET status='broken' WHERE kind='promise' "
        "AND status='' AND EXISTS (SELECT 1 FROM flags f WHERE "
        "f.player=memory.player AND f.char=memory.char AND "
        "f.key='promise_penalized_' || memory.id AND f.value<>'')")
    con.execute(
        "INSERT INTO state(player,char,key,value) "
        "SELECT player, char, 'last_seen', MAX(ts) FROM dialogue "
        "WHERE true GROUP BY player, char "
        "ON CONFLICT(player,char,key) DO UPDATE SET value=excluded.value "
        "WHERE state.value=''")
    # '새 저장소' 의 기준선 — 이미 기록에 있는 곳은 새것이 아니다.
    # 비워 두면 승격 직후 원래 하던 저장소마다 '새 저장소' 가 떴다.
    # 프로젝트 이름은 'name (branch)' 꼴이다.
    con.execute(
        "INSERT OR IGNORE INTO projects(player,name,first_ts) "
        "SELECT player, CASE WHEN instr(text,' (')>0 "
        "THEN substr(text,1,instr(text,' (')-1) ELSE text END, MIN(ts) "
        "FROM work_facts WHERE kind='project' GROUP BY 1, 2")


def _start_values(char_id: str):
    """(시작 호감, 시작 신뢰). 캐릭터 팩을 읽을 수 없으면 기본값."""
    aff, trust = config.AFF_START, config.TRUST_START
    try:
        from . import characters
        char = characters.get(char_id)
        if char is not None and char.id == char_id:
            aff = int(char.start.get("affection", aff))
            trust = int(char.start.get("trust", trust))
    except Exception:                                         # noqa: BLE001
        pass
    return aff, trust


def _upgrade_v6(con) -> None:
    """v6 — 커밋으로 쌓인 자동 호감·신뢰를 한 번 회수한다.

    v5 까지는 커밋 한 번에 모든 캐릭터의 호감 +2·신뢰 +1 이었고 상한이
    없었다. 실제 저장소에서 커밋 868번이 캐릭터마다 호감 +1,736 이 됐고,
    말 한 번 안 건 사람까지 호감·신뢰 100 이 됐다.

    호감은 장부에 전부 남아 있으므로 커밋 몫만 빼고 처음부터 다시 쌓는다
    (0~100 경계도 그때처럼 매번 적용한다). 신뢰는 대화로 움직인 몫이 장부에
    없어서 정확히 되살릴 수 없다 — 새 규칙(커밋한 날 × 하루 상한, 처음
    만난 뒤부터)으로 다시 상한을 건다. 지금 값보다 올리지는 않는다.

    회수한 양은 장부에 kind='correction' 으로 남긴다 — /status 에 뜬다.
    """
    pairs = con.execute(
        "SELECT DISTINCT player, char FROM ledger "
        "WHERE kind='commit' AND char<>''").fetchall()
    for row in pairs:
        player, char_id = row[0], row[1]
        # 한 번만. 옛 코드가 판을 5 로 되돌려 이게 다시 돌아도(켜 둔 게임이
        # 옛 코드로 init 을 부르는 경우) 같은 몫을 두 번 빼지 않는다.
        if con.execute("SELECT 1 FROM ledger WHERE player=? AND char=? "
                       "AND kind='correction' LIMIT 1",
                       (player, char_id)).fetchone():
            continue
        start_aff, start_trust = _start_values(char_id)
        aff = start_aff
        for (kind, delta) in con.execute(
                "SELECT kind, delta_aff FROM ledger WHERE player=? AND char=? "
                "AND delta_aff<>0 ORDER BY id", (player, char_id)):
            if kind in ("commit", "correction"):
                continue
            aff = max(config.AFF_MIN, min(config.AFF_MAX, aff + delta))

        first = con.execute(
            "SELECT MIN(ts) FROM dialogue WHERE player=? AND char=?",
            (player, char_id)).fetchone()[0]
        days = 0
        if first:
            days = con.execute(
                "SELECT COUNT(DISTINCT substr(ts,1,10)) FROM ledger "
                "WHERE player=? AND char=? AND kind='commit' AND ts>=?",
                (player, char_id, first)).fetchone()[0]
        trust_cap = min(100, start_trust
                        + days * config.TRUST_COMMIT_DAILY_MAX)

        def cur(key, default):
            got = con.execute(
                "SELECT value FROM state WHERE player=? AND char=? AND key=?",
                (player, char_id, key)).fetchone()
            try:
                return int(got[0]) if got else default
            except (TypeError, ValueError):
                return default
        now_aff, now_trust = cur("affection", start_aff), cur("trust",
                                                                start_trust)
        new_aff = min(now_aff, aff)
        new_trust = min(now_trust, max(start_trust, trust_cap))
        if new_aff == now_aff and new_trust == now_trust:
            continue
        for key, value in (("affection", new_aff), ("trust", new_trust)):
            con.execute(
                "INSERT INTO state(player,char,key,value) VALUES(?,?,?,?) "
                "ON CONFLICT(player,char,key) DO UPDATE SET "
                "value=excluded.value", (player, char_id, key, str(value)))
        con.execute(
            "INSERT INTO ledger(player,char,ts,kind,delta_lcl,delta_aff,"
            "reason,session_id) VALUES(?,?,?,?,?,?,?,?)",
            (player, char_id, now(), "correction", 0, new_aff - now_aff,
             f"커밋으로 쌓인 자동 호감 회수 · 신뢰 {now_trust}→{new_trust}",
             ""))


PHANTOMS = ("root",)


def _merge_phantoms(con) -> None:
    """v7 — 유령 플레이어의 기록을 실제 사용자에게 합친다(1회).

    tty 없이 데몬 아래에서 도는 훅(Codex 데스크톱)에서 os.getlogin() 이
    'root' 를 돌려줘, 내 계정으로 돈 작업이 'root' 에게 적립됐다.
    identity 는 이제 실행 계정(uid)을 본다. 이미 쌓인 것을 옮긴다.

    합치는 조건 — 셋 다 맞아야 한다:
      · 저장소 파일의 주인이 지금 사용자다 (남의 저장소가 아니다)
      · 지금 사용자는 root 가 아니다
      · 유령은 캐릭터를 한 번도 만난 적이 없다 — 실제로 root 로 놀았으면
        진짜 사람이니 건드리지 않는다
    """
    import pwd
    try:
        owner = config.db_path().stat().st_uid
        owner_name = pwd.getpwuid(owner).pw_name
    except (OSError, KeyError):
        return
    if owner == 0 or owner != os.getuid() or owner_name != PLAYER:
        return
    for ghost in PHANTOMS:
        if ghost == PLAYER:
            continue
        has = con.execute("SELECT 1 FROM state WHERE player=? LIMIT 1",
                          (ghost,)).fetchone()
        if not has:
            continue
        played = con.execute(
            "SELECT 1 FROM state WHERE player=? AND key='met_count' "
            "AND CAST(value AS INTEGER)>0 UNION SELECT 1 FROM dialogue "
            "WHERE player=? LIMIT 1", (ghost, ghost)).fetchone()
        if played:
            continue

        def num(player, key):
            row = con.execute(
                "SELECT value FROM state WHERE player=? AND char='' AND key=?",
                (player, key)).fetchone()
            try:
                return int(row[0]) if row else 0
            except (TypeError, ValueError):
                return 0
        lcl, earned = num(ghost, "lcl"), num(ghost, "total_earned")
        for key, add in (("lcl", lcl), ("total_earned", earned)):
            con.execute(
                "INSERT INTO state(player,char,key,value) VALUES(?,'',?,?) "
                "ON CONFLICT(player,char,key) DO UPDATE SET "
                "value=CAST(CAST(value AS INTEGER)+? AS TEXT)",
                (PLAYER, key, str(add), add))
        # 하루 집계는 날마다 더한다
        for row in con.execute("SELECT * FROM daily WHERE player=?",
                               (ghost,)).fetchall():
            con.execute("INSERT OR IGNORE INTO daily(player,day) VALUES(?,?)",
                        (PLAYER, row["day"]))
            con.execute(
                "UPDATE daily SET " + ", ".join(
                    f"{f}={f}+?" for f in DAILY_FIELDS) +
                " WHERE player=? AND day=?",
                (*[row[f] or 0 for f in DAILY_FIELDS], PLAYER, row["day"]))
        con.execute("UPDATE ledger SET player=? WHERE player=?",
                    (PLAYER, ghost))
        for table in ("work_facts", "work_events", "projects", "commits"):
            con.execute(f"UPDATE OR IGNORE {table} SET player=? "
                        f"WHERE player=?", (PLAYER, ghost))
        # 관계(만난 적 없으니 커밋으로만 생긴 것)와 나머지는 버린다
        for table in ("state", "daily", "work_facts", "work_events",
                      "projects", "commits", "work_scan", "dialogue",
                      "memory", "owned", "flags", "social", "repos"):
            con.execute(f"DELETE FROM {table} WHERE player=?", (ghost,))
        con.execute(
            "INSERT INTO ledger(player,char,ts,kind,delta_lcl,delta_aff,"
            "reason,session_id) VALUES(?,?,?,?,?,?,?,?)",
            (PLAYER, "", now(), "merge", 0, 0,
             f"'{ghost}' 로 잘못 적립된 {lcl:,} 을 합쳤다", ""))


def init(con: sqlite3.Connection, *, with_characters: bool = None) -> None:
    """스키마를 맞추고 기본값을 채운다.

    with_characters=False 면 캐릭터 시딩을 건너뛴다(훅 경로). 저장소가
    아직 비어 있으면 그때는 어쩔 수 없이 채운다 — 안 그러면 훅이 먼저
    돈 사람의 첫 적립이 갈 곳이 없다.
    """
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver == SCHEMA_VERSION and con.execute(
            "SELECT 1 FROM state WHERE player=? AND char='' AND key='created'",
            (PLAYER,)).fetchone():
        # 빠른 경로 — 판이 맞고 이 플레이어의 행도 있다. 매 훅마다
        # DDL 20여 개를 돌릴 이유가 없다(PK 조회 1번으로 끝).
        # executescript 를 타지 않으므로 session(write=True)이 잡은
        # 트랜잭션도 그대로 유지된다.
        return
    if ver != SCHEMA_VERSION and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone():
        # 빈 새 파일은 백업할 것이 없다 — 기존 저장소의 승격만 백업한다.
        _backup(con, ver)
    _migrate(con)
    con.executescript(SCHEMA)      # 주의: 진행 중 트랜잭션을 commit 한다
    _ensure_columns(con)
    _sanitize_stored_text(con)
    _upgrade_v5(con)
    if ver < 6:
        _upgrade_v6(con)          # 한 번만 — 판 승격과 함께
    if ver < 7:
        _merge_phantoms(con)
    for k, v in GLOBAL_DEFAULTS.items():
        con.execute(
            "INSERT OR IGNORE INTO state(player,char,key,value) VALUES(?,'',?,?)",
            (PLAYER, k, v))

    skip_seed = False
    if with_characters is False:
        skip_seed = con.execute(
            "SELECT 1 FROM state WHERE player=? AND char<>'' LIMIT 1",
            (PLAYER,)).fetchone() is not None
    if not skip_seed:
        seed_characters(con)
        con.execute("UPDATE state SET value=? "
                    "WHERE player=? AND char='' AND key='created' AND value=''",
                    (now(), PLAYER))
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


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
    """정수 값을 더한다. **한 문장으로** — 읽고 쓰는 사이에 훅이 끼어도
    적립이 사라지지 않는다(게임 연결은 자동 커밋이라 문장 사이가 열려
    있다)."""
    new = "CAST(value AS INTEGER) + :d"
    start = delta
    if lo is not None:
        new, start = f"MAX({new}, :lo)", max(lo, start)
    if hi is not None:
        new, start = f"MIN({new}, :hi)", min(hi, start)
    con.execute(
        "INSERT INTO state(player,char,key,value) VALUES(:p,:c,:k,:s) "
        "ON CONFLICT(player,char,key) DO UPDATE SET "
        f"value=CAST({new} AS TEXT)",
        {"p": PLAYER, "c": _ck(key, char), "k": key, "s": str(start),
         "d": delta, "lo": lo, "hi": hi})
    return geti(con, key, char=char)


# ── 일일 집계 (전역 — 근무 기록) ───────────────────────────────────────
DAILY_FIELDS = ("tools", "edits", "commits", "fails", "lcl", "stops",
                "llm", "api")


def daily_row(con, day: str = None):
    """그 날의 집계. **읽기만 한다** — 행이 없으면 0 으로 채워 돌려준다.

    예전에는 INSERT OR IGNORE 로 행을 만들어 두고 읽었다. 그게 화면을
    다시 그릴 때마다 쓰기 트랜잭션을 열었고, 게임이 입력을 기다리는 동안
    락이 풀리지 않아 훅이 전부 막혔다. 행은 daily_bump() 가 만든다.
    """
    day = day or today()
    row = con.execute("SELECT * FROM daily WHERE player=? AND day=?",
                      (PLAYER, day)).fetchone()
    if row is not None:
        return row
    out = {f: 0 for f in DAILY_FIELDS}
    out.update(player=PLAYER, day=day)
    return out


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
CHAR_TABLES = ("dialogue", "memory", "owned", "flags", "social")


def reset_character(con, char_id: str) -> None:
    """한 사람과의 관계를 처음으로. 재화와 근무 기록은 남는다."""
    with tx(con):
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
    for cid in known_chars(con):
        reset_character(con, cid)


def reset_everything(con) -> None:
    """전부. 재화·근무 기록·장부까지. 되돌릴 수 없다."""
    with tx(con):
        _wipe(con)
    con.commit()
    init(con)


def _wipe(con) -> None:
    for table in ("state", "ledger", "daily", "dialogue", "memory",
                  "owned", "flags", "work_scan", "work_facts",
                  "work_events", "projects", "social", "commits", "repos"):
        con.execute(f"DELETE FROM {table} WHERE player=?", (PLAYER,))


def _seed_character(con, char_id: str) -> None:
    """초기화용 — 기존 값을 덮어쓴다(seed_characters 는 안 덮어쓴다)."""
    from . import characters
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
