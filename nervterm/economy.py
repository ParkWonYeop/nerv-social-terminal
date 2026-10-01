# -*- coding: utf-8 -*-
"""재화·호감도 규칙 엔진. 훅과 게임이 공유한다."""
import datetime as _dt
import re

from . import config, db, events, stance

# 파일을 고치는 도구. Codex 는 apply_patch 라는 이름으로 보낸다.
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "apply_patch")

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
    if lcl > 0 and respect_cap:
        row = db.daily_row(con)
        room = max(0, config.DAILY_LCL_CAP - (row["lcl"] or 0))
        lcl = min(lcl, room)
    if lcl:
        db.bump(con, "lcl", lcl, lo=0)
        # 총 획득량은 의미상 절대 줄지 않는다 — 음수 lcl(벌금류)이
        # 들어와도 지갑만 깎인다.
        db.bump(con, "total_earned", max(0, lcl))
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


def met(con, char) -> bool:
    """한 번이라도 찾아간 사람인가."""
    return db.geti(con, "met_count", char=char) > 0


def capped(con, char, key: str, want: int, cap: int) -> int:
    """오늘 이 캐릭터에게 key 명목으로 이미 준 양을 보고 남은 만큼만.

    상태에 'YYYY-MM-DD:n' 으로 적어 둔다 — 날짜가 바뀌면 저절로 0.
    """
    if want <= 0:
        return want
    today = db.today()
    day, _, n = db.get(con, f"cap_{key}", char=char).partition(":")
    used = int(n) if day == today and n.isdigit() else 0
    give = max(0, min(want, cap - used))
    if give:
        db.put(con, f"cap_{key}", f"{today}:{used + give}", char=char)
    return give


def on_tool(con, *, tool: str, tool_input: dict, tool_response, ok: bool,
            session_id: str = "", cwd: str = ""):
    """PostToolUse 1건 처리. 화면에 보여줄 이벤트 목록을 돌려준다.

    ("fail"|"danger"|"commit"|"test"|"lcl"|"event", 값) 의 목록.
    "event" 는 근무 사건(events.py) — 값은 사건 종류.
    """
    out = []
    db.daily_bump(con, "tools", 1)
    prev_fail = db.geti(con, "fail_streak")

    if not ok:
        db.daily_bump(con, "fails", 1)
        streak = db.bump(con, "fail_streak", 1)
        if streak > 0 and streak % config.AFF_FAIL_STREAK == 0:
            for c in db.known_chars(con):
                apply(con, aff=config.AFF_FAIL_PENALTY, kind="fail_streak",
                      reason=f"{streak}회 연속 도구 실패",
                      session_id=session_id, char=c)
            out.append(("fail", f"{streak}회 연속 실패"))
        out += [("event", k) for k in events.after_tool(
            con, ok=False, tested=False, committed=False,
            prev_fail_streak=prev_fail, cwd=cwd)]
        return out

    if prev_fail:
        db.put(con, "fail_streak", 0)

    base = config.TOOL_REWARD.get(tool, 1)
    if tool in EDIT_TOOLS:
        db.daily_bump(con, "edits", 1)

    cmd = ""
    if tool == "Bash" and isinstance(tool_input, dict):
        cmd = str(tool_input.get("command", ""))
    # 인용부호·heredoc 을 벗긴 것으로만 판정한다.
    # 문서나 테스트 목록에 "git commit" 이라고 적은 것을 커밋으로 세면 안 된다.
    bare = strip_literals(cmd)

    why = check_danger(cmd)
    if why:
        # 다들 같은 단말 기록을 본다 — 전원에게 반영
        from . import recall
        events.danger(con, why)
        for c in db.known_chars(con):
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
        out.append(("danger", why))

    committed = bool(bare and _COMMIT.search(bare))
    if committed:
        db.daily_bump(con, "commits", 1)
        base += config.COMMIT_BONUS
        # 꾸준함이 관계를 조금 움직인다 — 하루 상한 안에서, 만난 적
        # 있는 사람에게만. 호감은 대화로 쌓는 것이다.
        for c in db.known_chars(con):
            if not met(con, c):
                continue
            aff = capped(con, c, "commit_aff", config.AFF_COMMIT,
                         config.AFF_COMMIT_DAILY_MAX)
            if aff:
                apply(con, aff=aff, kind="commit", reason="커밋",
                      session_id=session_id, char=c)
            trust = capped(con, c, "commit_trust", config.TRUST_COMMIT,
                           config.TRUST_COMMIT_DAILY_MAX)
            if trust:
                stance.move(con, "trust", trust, char=c)
        out.append(("commit", "커밋 완료"))

    # 테스트 통과는 명령이 아니라 '출력' 을 본다. 출력은 벗기지 않는다.
    text = tool_response if isinstance(tool_response, str) else str(tool_response)
    tested = bool(bare and _TEST_OK.search(text[:4000]))
    if tested:
        base += config.TEST_PASS_BONUS
        out.append(("test", "테스트 통과"))

    got, _ = apply(con, lcl=base, kind="tool", reason=tool,
                   session_id=session_id)
    if got:
        out.append(("lcl", got))
    out += [("event", k) for k in events.after_tool(
        con, ok=True, tested=tested, committed=committed,
        prev_fail_streak=prev_fail, cwd=cwd)]
    return out


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
    """날짜가 바뀌었으면 연속 접속일 갱신 + 보너스. (streak, bonus) 반환.

    확인과 갱신을 한 트랜잭션으로 — 자정 직후 게임과 훅이 동시에 돌면
    둘 다 '날이 바뀌었다' 고 보고 보너스를 두 번 줄 수 있었다.
    """
    with db.tx(con):
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
        events.streak(con, streak)
    return streak, bonus


def _days_since(stamp: str) -> int:
    if not stamp:
        return 0
    try:
        then = _dt.datetime.fromisoformat(stamp)
    except ValueError:
        return 0
    return max(0, (_dt.datetime.now() - then).days)


def days_since_active(con) -> int:
    """에이전트로든 게임으로든 마지막으로 단말에 나타난 뒤 며칠."""
    return _days_since(db.get(con, "last_active"))


def days_since_seen(con, char=None) -> int:
    """이 캐릭터를 마지막으로 찾아온 뒤 며칠. 만난 적 없으면 0."""
    return _days_since(db.get(con, "last_seen", char=char))


def touch_seen(con, char=None) -> None:
    """찾아왔다. 부재가 끝났으니 방치 카운터도 함께 0 으로.

    카운터를 '다음 정산 때 이틀 안이면' 리셋하던 시절에는, 한 번 길게
    비운 뒤로는 그보다 짧은 부재가 전부 '이미 감점함' 으로 건너뛰어졌다.
    """
    db.put(con, "last_seen", db.now(), char=char)
    db.put(con, "neglect_applied", 0, char=char)
    db.put(con, "neglect_total", 0, char=char)


def worked_while_away(con, char=None) -> int:
    """안 찾아온 동안 단말에서 일한 날 수. 안 찾아온 지 얼마 안 됐으면 0.

    '오지 않았다' 와 '여기 있었으면서 나를 안 찾아왔다' 는 다른 일이다.
    매일 단말 앞에 있었다는 걸 캐릭터는 안다 — 같은 단말을 보니까.
    """
    last = db.get(con, "last_seen", char=char)
    if days_since_seen(con, char) < config.ABSENT_WORK_DAYS or not last:
        return 0
    row = con.execute(
        "SELECT COUNT(*) FROM daily WHERE player=? AND day>? AND day<? "
        "AND tools>0", (db.PLAYER, last[:10], db.today())).fetchone()
    return row[0] if row else 0


def settle_neglect(con, char=None):
    """오래 안 찾아왔으면 호감도 감소. 적용된 (일수, 감소량).

    기준은 **이 캐릭터를 마지막으로 찾아온 때**다. 예전에는 전역
    last_active(훅이 도구 호출마다 갱신)를 써서, 매일 코딩만 하면 2주
    동안 한 번도 안 찾아가도 방치가 0 이었다.

    상한(AFF_NEGLECT_CAP)은 부재 1회당이다 — 돌아오면 함께 리셋된다.
    만난 적 없는 사람은 서운할 이유가 없다(last_seen 이 비어 있다).
    """
    days = days_since_seen(con, char)
    if days < 2:
        db.put(con, "neglect_applied", 0, char=char)
        db.put(con, "neglect_total", 0, char=char)   # 부재가 끝났으니 리셋
        return days, 0
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
    db.put(con, "neglect_applied", days, char=char)
    return days, penalty
