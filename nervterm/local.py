# -*- coding: utf-8 -*-
"""로컬 에이전트 — Ollama 로 도는 것들, 그리고 훅이 없는 모든 것.

로컬 에이전트는 종류가 너무 많고(aider, opencode, goose, …) 대부분 훅이
없다. 그래서 도구 하나하나에 맞추지 않고, 무엇으로 일했든 남는 것을 본다:
**git 커밋.** 작업 폴더의 저장소 reflog 를 읽어, Claude·Codex 훅이 적어
두지 않은 새 커밋을 '로컬에서 한 일' 로 친다.

    note_commit(con, cwd, source)   훅이 커밋을 봤을 때 해시를 적는다
    scan(con)                       지켜보는 저장소의 새 커밋을 정산한다

대화는 Ollama 가 남기는 것을 읽는다 — `ollama run` 에서 친 말이
~/.ollama/history 에 쌓인다(agents.LocalAgent).

설정 → 보상·근무 기록 대상 → '로컬 에이전트' 를 체크해야 돈다. 처음
켠 순간부터의 커밋만 센다 — 켜자마자 지난 몇 년 치가 적립되면 안 된다.
"""
import datetime as _dt
import os
import time
from pathlib import Path

from . import clock, config, db, settings

# 저장소를 찾을 때 들어가지 않는 곳
SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__", ".git", ".hg",
    "Library", "Applications", ".Trash", ".cache", ".npm", ".cargo",
    ".rustup", ".gradle", ".m2", "Pictures", "Movies", "Music", "Downloads",
    ".local", ".ollama", ".docker", "go", "vendor", "dist", "build",
}
MAX_DEPTH = 3            # ~/a/b/c 까지
MAX_REPOS = 300
MAX_DIRS = 20_000        # 이만큼 훑었으면 그만 — 시작이 느려지면 안 된다
REFRESH_HOURS = 24       # 저장소 목록은 하루 한 번 다시 찾는다

# 로컬 커밋 하나의 값. 훅 경로의 '파일 수정 + 커밋' 과 맞춘다.
LOCAL_COMMIT_LCL = config.COMMIT_BONUS + config.TOOL_REWARD["Edit"]


def enabled() -> bool:
    table = settings.get("agents", {}) or {}
    return bool(table.get("local"))


def roots() -> list:
    got = settings.get("local.roots") or ["~"]
    return [str(Path(r).expanduser()) for r in got if str(r).strip()]


# ── git 을 실행하지 않고 읽는다 ────────────────────────────────────────
def git_dir(repo: str) -> str:
    """작업 트리의 .git 디렉터리. worktree·submodule 이면 'gitdir:' 를 따라간다."""
    dot = os.path.join(repo, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, "r", encoding="utf-8") as f:
            line = f.readline().strip()
    except OSError:
        return ""
    if line.startswith("gitdir:"):
        path = line[7:].strip()
        return path if os.path.isabs(path) else os.path.normpath(
            os.path.join(repo, path))
    return ""


def _reflog(repo: str) -> str:
    gd = git_dir(repo)
    return os.path.join(gd, "logs", "HEAD") if gd else ""


def parse_reflog_line(line: str):
    """'<old> <new> 이름 <메일> <epoch> <tz>\\t<행동>: <메시지>'
    → (해시, epoch, 행동, 메시지) 또는 None."""
    head, _, tail = line.partition("\t")
    parts = head.split()
    if len(parts) < 4 or not tail:
        return None
    new_hash = parts[1]
    try:
        epoch = int(parts[-2])
    except ValueError:
        return None
    action, _, message = tail.partition(": ")
    return new_hash, epoch, action.strip(), message.strip()


def is_commit(action: str) -> bool:
    """reflog 행동 중 커밋을 만든 것 — commit / commit (initial|amend|merge)."""
    return action == "commit" or action.startswith("commit (")


def last_commit(repo: str, within: int = 180) -> str:
    """이 저장소에서 방금(within 초 안) 만든 마지막 커밋 해시. 없으면 ''.

    훅이 쓴다. reflog 의 마지막 줄만 보면 안 된다 — `git commit && git
    checkout …` 이면 마지막 줄은 checkout 이다. 그렇다고 최근 커밋을 전부
    가져가면, 바로 전에 로컬 에이전트가 만든 커밋까지 '훅이 만든 것' 이 돼
    영영 적립되지 않는다. 이 명령이 만든 마지막 커밋 하나만.
    """
    path = _reflog(repo)
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", "replace").splitlines()[-20:]
    except OSError:
        return ""
    now = time.time()
    for line in reversed(tail):
        got = parse_reflog_line(line)
        if got and is_commit(got[2]) and now - got[1] <= within:
            return got[0]
    return ""


def note_commit(con, cwd: str, source: str) -> int:
    """훅이 커밋을 봤다 — 그 커밋 해시를 '어디서 만든 것' 과 함께 적는다.

    체크를 푼 에이전트의 커밋도 적는다. 안 적으면 로컬 판독이 그걸 '로컬에서
    한 일' 로 잘못 알고 적립한다.
    """
    from .events import project_root
    if not cwd:
        return 0
    h = last_commit(project_root(cwd))
    if not h:
        return 0
    return con.execute(
        "INSERT OR IGNORE INTO commits(player,hash,source,ts) "
        "VALUES(?,?,?,?)", (db.PLAYER, h, source, db.now())).rowcount


# ── 저장소 찾기 ────────────────────────────────────────────────────────
def find_repos(bases, *, depth=MAX_DEPTH, cap=MAX_REPOS) -> list:
    """작업 폴더 아래의 git 저장소. 저장소 안으로는 더 내려가지 않는다."""
    found, seen = [], 0
    stack = [(os.path.abspath(b), 0) for b in bases]
    while stack and len(found) < cap and seen < MAX_DIRS:
        path, level = stack.pop()
        seen += 1
        if os.path.exists(os.path.join(path, ".git")):
            found.append(path)
            continue
        if level >= depth:
            continue
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for e in entries:
            if (e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS
                    and not (e.name.startswith(".") and level == 0
                             and e.name not in (".config",))):
                stack.append((e.path, level + 1))
    return sorted(found)


def _refresh_repos(con) -> None:
    last = db.get(con, "local_repos_at")
    try:
        age = (_dt.datetime.now()
               - _dt.datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        age = REFRESH_HOURS
    if age < REFRESH_HOURS:
        return
    repos = find_repos(roots())
    with db.tx(con):
        for repo in repos:
            con.execute("INSERT OR IGNORE INTO repos(player,path) VALUES(?,?)",
                        (db.PLAYER, repo))
        db.put(con, "local_repos_at", db.now())


# ── 정산 ───────────────────────────────────────────────────────────────
def scan(con) -> int:
    """지켜보는 저장소의 새 커밋을 정산한다. 정산한 커밋 수."""
    if not enabled():
        return 0
    since = db.get(con, "local_since")
    if not since:
        # 처음 켰다 — 지금부터 센다. 지난 커밋을 한꺼번에 적립하지 않는다.
        db.put(con, "local_since", db.now())
        _refresh_repos(con)
        _skip_to_end(con)
        return 0
    try:
        since_epoch = _dt.datetime.fromisoformat(since).timestamp()
    except ValueError:
        since_epoch = time.time()
    _refresh_repos(con)
    made = 0
    for row in con.execute("SELECT path,offset,mtime FROM repos WHERE player=?",
                           (db.PLAYER,)).fetchall():
        made += _scan_repo(con, row["path"], row["offset"], row["mtime"],
                           since_epoch)
    return made


def _skip_to_end(con) -> None:
    for row in con.execute("SELECT path FROM repos WHERE player=?",
                           (db.PLAYER,)).fetchall():
        path = _reflog(row["path"])
        try:
            st = os.stat(path)
        except OSError:
            continue
        con.execute("UPDATE repos SET offset=?, mtime=? WHERE player=? "
                    "AND path=?", (st.st_size, st.st_mtime, db.PLAYER,
                                   row["path"]))


def _scan_repo(con, repo, offset, mtime, since_epoch) -> int:
    path = _reflog(repo)
    try:
        st = os.stat(path)
    except OSError:
        return 0
    if st.st_mtime <= mtime and st.st_size <= offset:
        return 0
    if st.st_size < offset:              # reflog 가 정리됐다(gc) — 처음부터
        offset = 0
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            blob = f.read()
    except OSError:
        return 0
    cut = blob.rfind(b"\n")
    if cut < 0:
        return 0
    blob, consumed = blob[:cut + 1], cut + 1
    name = os.path.basename(repo)
    made = 0
    with db.tx(con):
        for line in blob.decode("utf-8", "replace").splitlines():
            got = parse_reflog_line(line)
            if not got or not is_commit(got[2]) or got[1] < since_epoch:
                continue
            h, epoch, _action, message = got
            fresh = con.execute(
                "INSERT OR IGNORE INTO commits(player,hash,source,ts) "
                "VALUES(?,?,?,?)", (db.PLAYER, h, "local", db.now())).rowcount
            if fresh:
                _reward(con, name, message, epoch)
                made += 1
        con.execute("UPDATE repos SET offset=?, mtime=? WHERE player=? "
                    "AND path=?", (offset + consumed, st.st_mtime, db.PLAYER,
                                   repo))
    return made


def _reward(con, repo_name: str, message: str, epoch: int) -> None:
    """로컬 커밋 하나 — 훅 경로의 커밋과 같은 규칙으로 정산한다."""
    from . import economy, events
    when = _dt.datetime.fromtimestamp(epoch)
    day, ts = when.date().isoformat(), when.isoformat(timespec="seconds")
    db.daily_bump(con, "commits", 1, day=day)
    economy.apply(con, lcl=LOCAL_COMMIT_LCL, kind="local_commit",
                  reason=f"{repo_name}: {message[:60]}")
    # 꾸준함은 신뢰를 조금 — 하루 상한 안에서, 만난 사람에게만(호감은 없다)
    for c in db.known_chars(con):
        if economy.met(con, c):
            give = economy.capped(con, c, "commit_trust", config.TRUST_COMMIT,
                                  config.TRUST_COMMIT_DAILY_MAX)
            if give:
                from . import stance
                stance.move(con, "trust", give, char=c)
    for kind, text in (("commit", message[:120]), ("project", repo_name)):
        if text:
            con.execute(
                "INSERT OR IGNORE INTO work_facts(player,day,ts,kind,text,sid,"
                "agent) VALUES(?,?,?,?,?,?,?)",
                (db.PLAYER, day, ts, kind, text, f"local:{repo_name}", "local"))
    if when.hour in clock.ODD_HOURS:
        events.note(con, "late_night", day,
                    f"새벽 {when.hour}시에도 커밋했다 ({repo_name})", ts)
