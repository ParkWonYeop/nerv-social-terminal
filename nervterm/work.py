# -*- coding: utf-8 -*-
"""Claude Code 트랜스크립트에서 '무슨 작업을 했는지' 뽑아낸다.

LLM을 쓰지 않는다. Claude Code가 이미 저장해 둔 것들을 줍는다:
  · ai-title    세션마다 자동 생성된 제목  ← 가장 좋은 요약
  · Bash 의 description 필드
  · 실제로 사용자가 타이핑한 프롬프트
  · 수정된 파일 경로 / 커밋 메시지 / git 브랜치

읽은 바이트 위치를 기억해 증분으로만 읽는다.
"""
import json
import os
import re
from pathlib import Path

from . import db, identity
COMMIT_RE = re.compile(r"""git\s+(?:-\S+\s+)*commit\b[^\n]*?-m\s*(['"])(.+?)\1""",
                       re.S)
MAX_BYTES_PER_SCAN = 4_000_000        # 한 번에 읽을 상한(폭주 방지)

def ensure(con):
    """스키마는 db.init 이 만든다. 여기서는 아무것도 하지 않는다."""
    return None


def _add(con, day, ts, kind, text, sid=""):
    text = (text or "").strip()
    if not text:
        return
    con.execute(
        "INSERT OR IGNORE INTO work_facts(player,day,ts,kind,text,sid) "
        "VALUES(?,?,?,?,?,?)", (db.PLAYER, day, ts, kind, text[:300], sid))


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def _harvest(con, rec, sid_fallback=""):
    """트랜스크립트 레코드 한 줄에서 사실을 뽑는다."""
    t = rec.get("type")
    sid = rec.get("sessionId") or rec.get("session_id") or sid_fallback
    ts = rec.get("timestamp", "") or db.now()
    day = ts[:10] if len(ts) >= 10 else db.today()

    if t == "ai-title":
        # timestamp 가 없다. day 를 비워 두고 digest 단계에서 세션 날짜로 귀속시킨다.
        # 사람이 직접 타이핑한 프롬프트가 있는 세션의 제목만 나중에 채택된다
        # (게임이 스스로 띄운 claude -p 세션의 제목을 근무 실적으로 오인하지 않기 위해).
        _add(con, "", db.now(), "title", rec.get("aiTitle", ""), sid)
        return

    if rec.get("isSidechain"):        # 서브에이전트 잡음 제외
        return

    if t == "user":
        if rec.get("promptSource") != "typed":
            return
        msg = rec.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            _add(con, day, ts, "prompt", _clean(content)[:160], sid)
        cwd = rec.get("cwd", "")
        if cwd:
            branch = rec.get("gitBranch") or ""
            label = os.path.basename(cwd) + (f" ({branch})" if branch else "")
            _add(con, day, ts, "project", label, sid)
        return

    if t == "assistant":
        for b in (rec.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name", "")
            inp = b.get("input") or {}
            if not isinstance(inp, dict):
                continue
            if name in ("Edit", "Write", "NotebookEdit"):
                fp = inp.get("file_path") or inp.get("notebook_path") or ""
                if fp:
                    _add(con, day, ts, "file", os.path.basename(str(fp)), sid)
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                desc = _clean(str(inp.get("description", "")))
                if desc:
                    _add(con, day, ts, "desc", desc[:100], sid)
                m = COMMIT_RE.search(cmd)
                if m:
                    _add(con, day, ts, "commit", _clean(m.group(2))[:120], sid)


def scan(con, *, budget=MAX_BYTES_PER_SCAN) -> int:
    """새로 쌓인 트랜스크립트만 읽어 사실을 적재. 읽은 바이트 수 반환."""
    projects = identity.projects_dir()
    if not projects.is_dir():
        return 0
    read_total = 0
    for path in sorted(projects.glob("*/*.jsonl")):
        try:
            stat = path.stat()
        except OSError:
            continue
        row = con.execute(
            "SELECT offset,mtime FROM work_scan WHERE player=? AND path=?",
            (db.PLAYER, str(path))).fetchone()
        offset = row["offset"] if row else 0
        if row and stat.st_mtime <= row["mtime"] and offset >= stat.st_size:
            continue
        if offset > stat.st_size:      # 파일이 잘렸다면 처음부터
            offset = 0
        if read_total >= budget:
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                want = budget - read_total
                chunk = f.read(want)
                consumed = len(chunk.encode("utf-8", "replace"))
                if chunk and not chunk.endswith("\n"):
                    cut = chunk.rfind("\n")
                    if cut >= 0:
                        dropped = chunk[cut + 1:]
                        chunk = chunk[:cut + 1]
                        consumed -= len(dropped.encode("utf-8", "replace"))
                    elif len(chunk) == want:
                        # 개행 없는 초대형 한 줄 — 파싱을 포기하고 건너뛴다.
                        # consumed 를 유지해 offset 이 전진해야 이 파일이
                        # 매 스캔마다 예산만 태우며 멈춰 있지 않는다.
                        chunk = ""
                    else:
                        # 파일 끝이 아직 개행 전 — 쓰는 중이니 다음에 다시
                        chunk = ""
                        consumed = 0
                sid = path.stem
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        _harvest(con, rec, sid)
                    except Exception:
                        continue
                read_total += consumed
                new_offset = offset + consumed
        except OSError:
            continue
        con.execute(
            "INSERT INTO work_scan(player,path,offset,mtime) VALUES(?,?,?,?) "
            "ON CONFLICT(player,path) DO UPDATE SET offset=excluded.offset, "
            "mtime=excluded.mtime",
            (db.PLAYER, str(path), new_offset, stat.st_mtime))
    return read_total


def facts(con, day=None, kind=None, limit=40):
    day = day or db.today()
    q = "SELECT kind,text,ts FROM work_facts WHERE player=? AND day=?"
    args = [db.PLAYER, day]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return list(reversed(con.execute(q, args).fetchall()))


def _human_titles(con, day, n=4):
    """그 날 사람이 실제로 타이핑한 세션들의 자동 생성 제목."""
    rows = con.execute(
        "SELECT DISTINCT t.text FROM work_facts t "
        "WHERE t.player=? AND t.kind='title' AND t.sid IN ("
        "  SELECT DISTINCT p.sid FROM work_facts p "
        "  WHERE p.player=? AND p.kind='prompt' AND p.day=? AND p.sid<>''"
        ") LIMIT ?", (db.PLAYER, db.PLAYER, day, n)).fetchall()
    return [r["text"] for r in rows]


def _pick(con, day, kind, n):
    rows = con.execute(
        "SELECT text FROM work_facts WHERE player=? AND day=? AND kind=? "
        "ORDER BY id DESC LIMIT ?", (db.PLAYER, day, kind, n)).fetchall()
    return [r["text"] for r in reversed(rows)]


def digest(con, day=None) -> str:
    """레이에게 넘길 하루치 작업 요약. 없으면 빈 문자열."""
    day = day or db.today()
    titles = _human_titles(con, day, 4)
    projects = _pick(con, day, "project", 3)
    prompts = _pick(con, day, "prompt", 4)
    descs = _pick(con, day, "desc", 6)
    files = _pick(con, day, "file", 8)
    commits = _pick(con, day, "commit", 4)

    if not any((titles, prompts, descs, files, commits)):
        return ""

    out = []
    if projects:
        out.append("작업한 곳: " + ", ".join(dict.fromkeys(projects)))
    if titles:
        out.append("무엇을 했나: " + " / ".join(dict.fromkeys(titles)))
    if prompts:
        out.append("상대가 시킨 일: " + " | ".join(p[:70] for p in prompts))
    if descs:
        out.append("한 작업: " + ", ".join(dict.fromkeys(descs)))
    if files:
        out.append("건드린 파일: " + ", ".join(dict.fromkeys(files)))
    if commits:
        out.append("커밋: " + " / ".join(commits))
    return "\n".join("  " + o for o in out)


def past_days(con, days=5, skip_today=True):
    """지난 날들의 한 줄 요약. [(day, text), …]"""
    rows = con.execute(
        "SELECT DISTINCT day FROM work_facts WHERE player=? AND day<>'' "
        "ORDER BY day DESC LIMIT ?", (db.PLAYER, days + 2)).fetchall()
    out = []
    today = db.today()
    for r in rows:
        d = r["day"]
        if not d or (skip_today and d == today):
            continue
        bits = list(dict.fromkeys(
            _human_titles(con, d, 3) + _pick(con, d, "commit", 2)))
        if not bits:                       # 제목이 없으면 시킨 일로 대신한다
            bits = [p[:60] for p in _pick(con, d, "prompt", 2)]
        if bits:
            out.append((d, " / ".join(bits)[:120]))
        if len(out) >= days:
            break
    return out
