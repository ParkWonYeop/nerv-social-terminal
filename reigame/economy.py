# -*- coding: utf-8 -*-
"""재화·호감도 규칙 엔진. 훅과 게임이 공유한다."""
import datetime as _dt
import re

from . import config, db, stance

_DANGER = [(re.compile(p, re.I), why) for p, why in config.DANGER_PATTERNS]
_TEST_OK = re.compile(
    r"\b(\d+\s+passed|all tests? passed|tests? ok|build succeeded|"
    r"0 failed|✓ \d+|PASS\b)", re.I)
# git 전역 옵션(-C path, -c key=val, --git-dir=…)을 지나 commit 에 닿아야 한다.
# 'git log --grep commit' 처럼 하위 명령이 다른 것은 걸리지 않는다.
_COMMIT = re.compile(
    r"\bgit\s+(?:(?:-[cC]\s+\S+|--?[\w-]+(?:=\S+)?)\s+)*commit\b")


def apply(con, *, lcl=0, aff=0, kind="", reason="", session_id="",
          respect_cap=True, char=None):
    """장부에 기록하고 상태에 반영. 실제 반영된 (lcl, aff)를 돌려준다.

    LCL 은 전역 지갑, 호감도(aff)는 char(기본: 활성 캐릭터)에게 간다.
    """
    if lcl and respect_cap:
        row = db.daily_row(con)
        room = max(0, config.DAILY_LCL_CAP - (row["lcl"] or 0))
        lcl = min(lcl, room)
    if lcl:
        db.bump(con, "lcl", lcl, lo=0)
        db.bump(con, "total_earned", lcl)
        db.daily_bump(con, "lcl", lcl)
    if aff:
        db.bump(con, "affection", aff, lo=config.AFF_MIN, hi=config.AFF_MAX,
                char=char)
    if lcl or aff:
        db.log(con, kind or "misc", lcl, aff, reason, session_id,
               char=(char if aff else ""))
    return lcl, aff


def spend(con, amount: int, kind: str, reason: str = "") -> bool:
    """LCL 소비. 잔액 부족이면 False."""
    if db.geti(con, "lcl") < amount:
        return False
    db.bump(con, "lcl", -amount, lo=0)
    db.log(con, kind, -amount, 0, reason)
    return True


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def strip_literals(cmd: str) -> str:
    """인용부호 안과 heredoc 본문을 지운다.

    문서를 쓰거나 grep 을 하면서 위험 명령을 '언급' 하는 것과
    실제로 '실행' 하는 것을 구분하기 위한 것이다.
    실행되는 위험 명령은 통짜로 인용되는 일이 거의 없다.
    """
    if not cmd:
        return ""

    # heredoc 본문 제거 (<<'EOF' … EOF)
    while True:
        m = _HEREDOC.search(cmd)
        if not m:
            break
        term, rest = m.group(2), cmd[m.end():]
        end = re.search(rf"^\s*{re.escape(term)}\s*$", rest, re.M)
        cmd = cmd[:m.start()] + (rest[end.end():] if end else "")

    # 인용부호 안 제거
    out, quote, esc = [], None, False
    for ch in cmd:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(" ")           # 인용 구간은 공백 하나로
            continue
        out.append(ch)
    return "".join(out)


def check_danger(command: str):
    """위험/이상한 명령이면 사유 문자열, 아니면 None."""
    if not command:
        return None
    bare = strip_literals(command)
    for rx, why in _DANGER:
        if rx.search(bare):
            return why
    return None


def on_tool(con, *, tool: str, tool_input: dict, tool_response, ok: bool,
            session_id: str = ""):
    """PostToolUse 1건 처리. 화면에 보여줄 이벤트 목록을 돌려준다."""
    events = []
    db.daily_bump(con, "tools", 1)

    if not ok:
        db.daily_bump(con, "fails", 1)
        streak = db.bump(con, "fail_streak", 1)
        if streak > 0 and streak % config.AFF_FAIL_STREAK == 0:
            for c in db.CHARS:
                apply(con, aff=config.AFF_FAIL_PENALTY, kind="fail_streak",
                      reason=f"{streak}회 연속 도구 실패",
                      session_id=session_id, char=c)
            events.append(("fail", f"{streak}회 연속 실패"))
        return events

    db.put(con, "fail_streak", 0)

    base = config.TOOL_REWARD.get(tool, 1)
    if tool in ("Edit", "Write", "NotebookEdit"):
        db.daily_bump(con, "edits", 1)

    cmd = ""
    if tool == "Bash" and isinstance(tool_input, dict):
        cmd = str(tool_input.get("command", ""))
    # 인용부호·heredoc 을 벗긴 것으로만 판정한다.
    # 문서나 테스트 목록에 "git commit" 이라고 적은 것을 커밋으로 세면 안 된다.
    bare = strip_literals(cmd)

    why = check_danger(cmd)
    if why:
        # 두 사람 다 같은 단말 기록을 본다 — 전원에게 반영
        from . import recall
        for c in db.CHARS:
            apply(con, aff=config.AFF_DANGER_PENALTY, kind="danger",
                  reason=why, session_id=session_id, char=c)
            # 위험한 짓은 호감보다 신뢰를 더 크게 깎는다.
            stance.move(con, "trust", config.TRUST_DANGER, char=c)
            db.log(con, "danger_trust", 0, 0,
                   f"신뢰 {config.TRUST_DANGER}: {why}", char=c)
            db.flag(con, "last_danger", why, char=c)
            recall.remember(con, "fact",
                            f"상대가 위험한 명령을 실행했다: {why}",
                            weight=3, char=c)
        events.append(("danger", why))

    if bare and _COMMIT.search(bare):
        db.daily_bump(con, "commits", 1)
        base += config.COMMIT_BONUS
        for c in db.CHARS:
            apply(con, aff=config.AFF_COMMIT, kind="commit",
                  reason="커밋", session_id=session_id, char=c)
            stance.move(con, "trust", config.TRUST_COMMIT, char=c)
        events.append(("commit", "커밋 완료"))

    # 테스트 통과는 명령이 아니라 '출력' 을 본다. 출력은 벗기지 않는다.
    text = tool_response if isinstance(tool_response, str) else str(tool_response)
    if bare and _TEST_OK.search(text[:4000]):
        base += config.TEST_PASS_BONUS
        events.append(("test", "테스트 통과"))

    got, _ = apply(con, lcl=base, kind="tool", reason=tool,
                   session_id=session_id)
    if got:
        events.append(("lcl", got))
    return events


def on_stop(con, session_id: str = ""):
    """세션 마무리 보너스."""
    row = db.daily_row(con)
    if (row["stops"] or 0) >= config.STOP_BONUS_DAILY_MAX:
        return 0
    db.daily_bump(con, "stops", 1)
    got, _ = apply(con, lcl=config.STOP_BONUS, kind="stop",
                   reason="세션 마무리", session_id=session_id)
    return got


def touch_activity(con):
    db.put(con, "last_active", db.now())


def roll_day(con):
    """날짜가 바뀌었으면 연속 접속일 갱신 + 보너스. (streak, bonus) 반환."""
    today = db.today()
    last = db.get(con, "last_day")
    if last == today:
        return db.geti(con, "streak_days"), 0
    if last:
        try:
            gap = (_dt.date.fromisoformat(today) -
                   _dt.date.fromisoformat(last)).days
        except ValueError:
            gap = 99
    else:
        gap = 1
    streak = db.geti(con, "streak_days") + 1 if gap == 1 else 1
    db.put(con, "streak_days", streak)
    db.put(con, "last_day", today)
    bonus, _ = apply(con, lcl=config.STREAK_BONUS * streak, kind="streak",
                     reason=f"{streak}일 연속", respect_cap=False)
    return streak, bonus


def days_since_active(con) -> int:
    last = db.get(con, "last_active")
    if not last:
        return 0
    try:
        then = _dt.datetime.fromisoformat(last)
    except ValueError:
        return 0
    return max(0, (_dt.datetime.now() - then).days)


def settle_neglect(con, char=None):
    """48시간 넘게 방치했으면 호감도 감소. 적용된 (일수, 감소량).

    캐릭터별로 따로 계산한다(각자 따로 서운해한다). char 기본은 활성 캐릭터.
    상한(AFF_NEGLECT_CAP)은 방치 1회당 상한이다 — 돌아오면 함께 리셋된다.
    적용량은 neglect_notify 에도 쌓아 둔다. 훅(SessionStart)이 먼저 조용히
    적용해 버려도, 게임이 나중에 켜질 때 그 몫을 사용자에게 알리기 위해서다.
    """
    days = days_since_active(con)
    if days < 2:
        db.put(con, "neglect_applied", 0, char=char)
        db.put(con, "neglect_total", 0, char=char)   # 부재가 끝났으니 리셋
        return 0, 0
    already = db.geti(con, "neglect_applied", char=char)
    if days <= already:
        return days, 0
    new_days = days - already
    penalty = config.AFF_NEGLECT_PER_DAY * new_days
    total_so_far = db.geti(con, "neglect_total", char=char)
    room = config.AFF_NEGLECT_CAP - total_so_far      # 둘 다 음수
    penalty = max(penalty, room) if room < 0 else 0
    if penalty:
        apply(con, aff=penalty, kind="neglect", reason=f"{days}일 방치",
              char=char)
        db.bump(con, "neglect_total", penalty, char=char)
        db.bump(con, "neglect_notify", penalty, char=char)
        db.put(con, "neglect_notify_days", days, char=char)
    db.put(con, "neglect_applied", days, char=char)
    return days, penalty
