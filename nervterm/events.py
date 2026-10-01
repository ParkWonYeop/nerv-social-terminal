# -*- coding: utf-8 -*-
"""근무 사건 — 원시 기록에서 규칙으로 뽑는 '이야깃거리'. LLM 0회.

근무 일지(work.py)는 무엇을 했는지 늘어놓는다. 캐릭터에게 필요한 건
목록이 아니라 **걸리는 일 하나**다: 새벽 세 시까지 붙어 있었다, 새
저장소를 시작했다, 연달아 실패하다 결국 테스트를 통과시켰다.

    note(con, kind, key, text)   사건 하나. (kind, key) 가 같으면 무시
    after_tool(...)              훅이 도구 호출 1건마다 부른다
    recent(con, ...)             프롬프트에 실을 최근 사건

훅 경로에서 돈다 — db·config·clock 만 쓴다. 플러그인을 읽지 않는다.
"""
import datetime as _dt
import os

from . import clock, config, db


def note(con, kind: str, key: str, text: str, ts: str = None) -> bool:
    """사건을 남긴다. 새로 생겼으면 True."""
    ts = ts or db.now()
    cur = con.execute(
        "INSERT OR IGNORE INTO work_events(player,ts,day,kind,key,text) "
        "VALUES(?,?,?,?,?,?)", (db.PLAYER, ts, ts[:10], kind, key, text))
    return cur.rowcount == 1


def project_root(cwd: str) -> str:
    """작업 디렉터리에서 저장소 뿌리를 찾는다(.git 이 있는 곳).

    셸이 하위 폴더로 cd 하면 훅의 cwd 도 따라간다 — 그대로 쓰면 src 나
    tests 가 '새 저장소' 가 된다. 못 찾으면 그 디렉터리 그대로.
    """
    path = os.path.normpath(cwd or "")
    probe = path
    for _ in range(12):
        if os.path.exists(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return path


def _new_project(con, cwd: str, when) -> bool:
    """처음 보는 작업 디렉터리면 기록하고, 알릴 만하면 True.

    설치 직후에는 원래 하던 저장소가 전부 처음 보는 것이다. 그래서
    설치하고 며칠은 기준선만 쌓고 알리지 않는다. 게임이 세션 기록을
    읽을 때도 기준선이 채워진다(work._seen_project).
    """
    name = os.path.basename(project_root(cwd))
    if not name or name in (".", "/"):
        return False
    if con.execute("SELECT 1 FROM projects WHERE player=? AND name=?",
                   (db.PLAYER, name)).fetchone():
        return False
    con.execute("INSERT OR IGNORE INTO projects(player,name,first_ts) "
                "VALUES(?,?,?)", (db.PLAYER, name, db.now()))
    created = db.get(con, "created")
    try:
        age = (when - _dt.datetime.fromisoformat(created)).days
    except (TypeError, ValueError):
        return False
    if age < config.NEW_PROJECT_GRACE_DAYS:
        return False
    return note(con, "new_project", name,
                f"새 저장소 '{name}' 에서 일을 시작했다")


def after_tool(con, *, ok: bool, tested: bool, committed: bool,
               prev_fail_streak: int, cwd: str = "", when=None) -> list:
    """도구 호출 1건 뒤의 사건 판정. 새로 생긴 사건의 kind 목록."""
    t = when or _dt.datetime.now()
    day = t.date().isoformat()
    out = []

    def add(kind, key, text):
        if note(con, kind, key, text):
            out.append(kind)

    if t.hour in clock.ODD_HOURS:
        add("late_night", day, f"새벽 {t.hour}시에도 단말 앞에 있었다")
    if t.weekday() >= 5:
        add("weekend", day,
            f"주말({clock.WEEKDAY[t.weekday()]}요일)인데도 일했다")
    if not ok:
        return out

    if tested and prev_fail_streak >= config.EVENT_RECOVER_FAILS:
        add("recovered", day,
            f"{prev_fail_streak}번 연달아 실패하다가 결국 테스트를 통과시켰다")
    row = db.daily_row(con)
    if (row["tools"] or 0) >= config.EVENT_BIG_DAY_TOOLS:
        add("big_day", day,
            f"하루에 도구를 {config.EVENT_BIG_DAY_TOOLS}번 넘게 썼다. "
            "종일 붙어 있었다")
    if committed and (row["commits"] or 0) >= config.EVENT_COMMIT_DAY:
        add("commit_day", day,
            f"하루에 커밋을 {config.EVENT_COMMIT_DAY}번 넘게 했다")
    if cwd and _new_project(con, cwd, t):
        out.append("new_project")
    return out


def streak(con, days: int) -> bool:
    """연속 접속 기록이 고비를 넘었을 때."""
    if days not in config.STREAK_MILESTONES:
        return False
    return note(con, "streak", f"{days}:{db.today()}",
                f"{days}일 연속으로 단말에 왔다")


def danger(con, why: str) -> bool:
    return note(con, "danger", db.now(), f"위험한 명령을 실행했다: {why}")


def recent(con, *, days: int = 3, after_id: int = 0, limit: int = 4):
    """최근 사건. [(id, ts, kind, text)] 최신순."""
    since = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT id,ts,kind,text FROM work_events WHERE player=? AND day>=? "
        "AND id>? ORDER BY id DESC LIMIT ?",
        (db.PLAYER, since, after_id, limit)).fetchall()
    return [(r["id"], r["ts"], r["kind"], r["text"]) for r in rows]


def lines(rows, when=None) -> list:
    """프롬프트용 줄. '- 3시간 전: 새벽 3시에도 단말 앞에 있었다'"""
    out = []
    for _id, ts, _kind, text in rows:
        ago = clock.ago(ts, when) or "최근"
        out.append(f"- {ago}: {text}")
    return out
