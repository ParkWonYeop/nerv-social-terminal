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
import re

from . import agents, db

MAX_BYTES_PER_SCAN = 4_000_000        # 한 번에 읽을 상한(폭주 방지)


def _add(con, day, ts, kind, text, sid="", agent="claude"):
    text = (text or "").strip()
    if not text:
        return
    con.execute(
        "INSERT OR IGNORE INTO work_facts(player,day,ts,kind,text,sid,agent) "
        "VALUES(?,?,?,?,?,?,?)",
        (db.PLAYER, day, ts, kind, text[:300], sid, agent))


def scan(con, *, budget=MAX_BYTES_PER_SCAN) -> int:
    """켜 둔 에이전트들의 세션 기록을 증분으로 읽는다. 읽은 바이트 수.

    에이전트마다 파일 위치도 레코드 형식도 다르다. 그 차이는 전부
    agents.py 가 안다 — 여기는 '증분으로 읽고 넣는' 일만 한다.
    """
    read_total = 0
    for agent in agents.enabled():
        if read_total >= budget:
            break
        read_total += _scan_agent(con, agent, budget - read_total)
    return read_total


def _scan_agent(con, agent, budget) -> int:
    read_total = 0
    # 파일마다 한 번씩 묻지 않는다 — 기록이 수천 개면 턴마다 수천 번이다.
    known = {r["path"]: r for r in con.execute(
        "SELECT path,offset,mtime FROM work_scan WHERE player=?",
        (db.PLAYER,))}
    for path in agent.session_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        row = known.get(str(path))
        offset = row["offset"] if row else 0
        if row and stat.st_mtime <= row["mtime"] and offset >= stat.st_size:
            continue
        if offset > stat.st_size:      # 파일이 잘렸다면 처음부터
            offset = 0
        if read_total >= budget:
            break
        batch = []
        try:
            # 바이너리로 읽는다 — 텍스트 모드 + errors="replace" 는 잘못된
            # 바이트 1개가 U+FFFD(재인코딩 시 3바이트)로 바뀌어 바이트
            # 오프셋이 영구히 어긋났다. 오프셋 계산은 바이트로만 한다.
            with open(path, "rb") as f:
                f.seek(offset)
                want = budget - read_total
                blob = f.read(want)
                consumed = len(blob)
                if blob and not blob.endswith(b"\n"):
                    cut = blob.rfind(b"\n")
                    if cut >= 0:
                        blob = blob[:cut + 1]
                        consumed = cut + 1
                    elif consumed == want:
                        # 개행 없는 초대형 한 줄 — 파싱을 포기하고 건너뛴다.
                        # consumed 를 유지해 offset 이 전진해야 이 파일이
                        # 매 스캔마다 예산만 태우며 멈춰 있지 않는다.
                        blob = b""
                    else:
                        # 파일 끝이 아직 개행 전 — 쓰는 중이니 다음에 다시
                        blob = b""
                        consumed = 0
                sid = path.stem
                # 옛(텍스트 모드) 오프셋이 줄 중간을 가리켜도 그 줄만
                # json.loads 에서 버려지고 다음 줄부터 저절로 재동기화된다.
                for bline in blob.split(b"\n"):
                    line = bline.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        facts = agent.harvest(rec, sid)
                    except Exception:
                        continue
                    batch.extend(facts)
                read_total += consumed
                new_offset = offset + consumed
        except OSError:
            continue
        # 파일 하나치를 짧은 트랜잭션 하나로. 읽고 파싱하는 동안에는 락을
        # 쥐지 않는다 — 그 사이 훅이 막히면 안 된다. 사실과 읽은 위치가
        # 함께 들어가야 다음 스캔이 같은 줄을 다시 읽지 않는다.
        with db.tx(con):
            for day, ts, kind, text, fsid in batch:
                _add(con, day, ts, kind, text, fsid, agent.id)
                if kind == "project":
                    _seen_project(con, text, ts)
            con.execute(
                "INSERT INTO work_scan(player,path,offset,mtime) "
                "VALUES(?,?,?,?) ON CONFLICT(player,path) DO UPDATE SET "
                "offset=excluded.offset, mtime=excluded.mtime",
                (db.PLAYER, str(path), new_offset, stat.st_mtime))
    return read_total


def _seen_project(con, label, ts):
    """이미 아는 작업 디렉터리로 적어 둔다 — '새 저장소' 사건의 기준선.

    훅이 처음 보는 디렉터리를 새 저장소로 알리는데, 기록에 이미 있는
    곳은 새것이 아니다.
    """
    name = agents.project_name(label)
    if name:
        con.execute("INSERT OR IGNORE INTO projects(player,name,first_ts) "
                    "VALUES(?,?,?)", (db.PLAYER, name, ts or db.now()))


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
