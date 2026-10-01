# -*- coding: utf-8 -*-
"""레이가 이 상대를 어떻게 여기는지 — 관계 상태.

호감도 하나로는 사람 같지 않다. 네 축으로 나눈다.

  호감 affection   좋아하는 정도.        느리게 오르고 느리게 내린다.
  신뢰 trust       믿을 만한 사람인가.    깨지면 회복이 아주 느리다.
  관심 interest    더 알고 싶은가.        재미없는 대화로 금방 식는다.
  인내 patience    지금 상대할 기분인가.  시간이 지나면 회복된다.

여기에 레이의 말로 쓴 인상(impression)과 걸리는 것(doubts)이 붙는다.
이 값들이 프롬프트에 들어가서 태도를 정하고, 응답이 다시 이 값을 바꾼다.
"""
import datetime as _dt
import re

from . import clock, config, db, recall
from .hangul import josa

AXES = ("affection", "trust", "interest", "patience")

LOW_CONTENT = {"ㅇㅇ", "ㅇㅋ", "ㄱㄱ", "웅", "응", "어", "그래", "ㅎㅎ", "ㅋㅋ",
               "ㅋㅋㅋ", "네", "예", "음", "흠", "아", "오", "?", "??", "…",
               "ok", "okay", "k", "y", "yes", "no", "hi", "hello",
               # 한국어에서 2글자는 실질 단어("안녕", "왜?", "미안")라
               # 길이로 자르지 않는다 — 성의 없는 것만 목록으로 잡는다.
               "ㄴㄴ", "ㅇㅈ", "ㅎㅇ", "ㅂㅂ", "ㄷㄷ", "ㅊㅊ", "ㅃㅃ", "노노"}


def _band(tone, field, value):
    rows = tone[field]
    pick = rows[0][1]
    for lo, text in rows:
        if value >= lo:
            pick = text
    return pick


def read(con) -> dict:
    st = {a: db.geti(con, a) for a in AXES}
    st["mood"] = db.get(con, "mood") or "flat"
    st["impression"] = db.get(con, "impression")
    st["doubts"] = db.get(con, "doubts")
    st["turns"] = db.geti(con, "turns")
    return st


def move(con, field: str, delta: int, char=None) -> int:
    if not delta:
        return db.geti(con, field, char=char)
    return db.bump(con, field, delta, lo=0, hi=100, char=char)


def recover_patience(con, boost: float = 1.0):
    """인내는 시간이 지나면 돌아온다. 턴마다 불러도 된다(30분 단위).

    boost — 돌봄이 살아 있으면 빨리 돈다(config.CARE_PATIENCE_BOOST).
    """
    last = db.get(con, "patience_ts")
    now = _dt.datetime.now()
    if not last:
        db.put(con, "patience_ts", now.isoformat(timespec="seconds"))
        return 0
    try:
        then = _dt.datetime.fromisoformat(last)
    except ValueError:
        db.put(con, "patience_ts", now.isoformat(timespec="seconds"))
        return 0
    hours = (now - then).total_seconds() / 3600
    if hours < 0.5:
        return 0
    gain = int(hours * config.PATIENCE_RECOVER_PER_HOUR * boost)
    if gain <= 0:
        return 0
    db.put(con, "patience_ts", now.isoformat(timespec="seconds"))
    before = db.geti(con, "patience")
    after = move(con, "patience", gain)
    return after - before


def decay_interest(con, days: int):
    """오래 안 오면 관심이 식는다."""
    if days < 2:
        return 0
    before = db.geti(con, "interest")
    after = move(con, "interest", config.INTEREST_DECAY_PER_DAY * min(days, 10))
    return after - before


def check_boring(con, text: str) -> str:
    """내용 없는 말인지 / 같은 말 반복인지. 사유 문자열 또는 빈 문자열."""
    t = (text or "").strip()
    if len(t) <= 1 or t.lower() in LOW_CONTENT:
        return "내용 없는 말"
    prev = con.execute(
        "SELECT text FROM dialogue WHERE player=? AND char=? AND role='user' "
        "ORDER BY id DESC LIMIT 6", (db.PLAYER, db.CHAR)).fetchall()
    g = recall.seq_grams(t)
    if not g:
        return ""
    for r in prev:
        og = recall.seq_grams(r["text"])
        if not og:
            continue
        inter = len(g & og)
        if inter / max(1, min(len(g), len(og))) >= 0.8:
            return "같은 말 반복"
    return ""


# ═══════════════════════════════════════════════════════════════════════
#  약속
# ═══════════════════════════════════════════════════════════════════════
#
# 약속은 memory(kind='promise') 의 한 줄이고, status 와 target 이 붙는다.
#
#   status   ''        지키는 중
#            kept      지켰다
#            broken    기한을 넘겼다 — 감점됐고, 아직 잊지 않았다
#            forgotten 어긴 지 오래돼 더는 꺼내지 않는다
#
#   target   date:<장소>  그 장소에 함께 가면 지킨 것
#            gift:<물건>  그 물건을 주면 지킨 것
#            visit        기한 안에 다시 찾아오면 지킨 것
#            rest         그날 밤 새벽까지 일하지 않으면 지킨 것 (근무 기록으로 판정)
#            ''           말로만 한 약속 — 캐릭터가 대화로 판단한다(kept_promise)
#
# 예전에는 지킬 길이 없었다. 약속은 5일 뒤 신뢰 -8 로 끝나는 것뿐이라,
# "다음에 같이 가자" 고 말하는 것 자체가 손해였다.

TARGET = re.compile(r"^(?:(?:date|gift):[a-z0-9_-]{1,32}|visit|rest)$")


def clean_target(raw: str, char=None, affection: int = None) -> str:
    """모델이 적은 이행 대상을 검증한다. 모르는 것은 '' (말로만).

    affection 을 주면 지금 호감으로 갈 수 없는 곳·줄 수 없는 것도 '' 다 —
    호감 20 에 '옛 도쿄 폐허(60)' 를 약속하면 지킬 길이 없어 감점만 확정된다.
    """
    t = (raw or "").strip().lower().replace(" ", "")
    if not TARGET.match(t):
        return ""
    if char is not None and ":" in t:
        kind, key = t.split(":", 1)
        table = (char.dates if kind == "date" else char.gifts) or {}
        if key not in table:
            return ""
        if affection is not None and affection < table[key][2]:
            return ""
    return t


def target_label(target: str, char=None) -> str:
    """'date:roof' → '학교 옥상에 함께 가기'"""
    if not target:
        return ""
    if target == "visit":
        return "다시 찾아오기"
    if target == "rest":
        return "새벽까지 일하지 않기"
    kind, _, key = target.partition(":")
    table = {}
    if char is not None:
        table = (char.dates if kind == "date" else char.gifts) or {}
    name = table.get(key, (key,))[0]
    return f"{name}에 함께 가기" if kind == "date" else f"{josa(name, '을/를')} 주기"


def _age(ts: str, now=None) -> float:
    try:
        then = _dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, ((now or _dt.datetime.now()) - then).total_seconds()
               / 86400)


def promises(con, *, status=None, limit=20):
    """이 캐릭터의 약속. [row(id, ts, text, target, status)] 최신순."""
    q = ("SELECT id,ts,text,target,status FROM memory WHERE player=? "
         "AND char=? AND kind='promise'")
    args = [db.PLAYER, db.CHAR]
    if status is not None:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return con.execute(q, args).fetchall()


def open_promises(con):
    """지키는 중인 약속. [(id, text, target, 며칠 지났나)]"""
    return [(r["id"], r["text"], r["target"], int(_age(r["ts"])))
            for r in promises(con, status="")]


def make_promise(con, text: str, target: str = "", char=None) -> int:
    """약속을 남긴다. 새로 생겼으면 id. 이미 있던 약속이면 0.

    '또 올게' 는 한 번에 하나만 — 매번 새로 만들면 3시간마다 지켜서
    신뢰·호감을 받아 가는 길이 된다.
    """
    target = clean_target(target, char, db.geti(con, "affection"))
    if target == "visit" and any(t == "visit" for _, _, t, _
                                 in open_promises(con)):
        return 0
    return recall.remember(con, "promise", text, weight=3, target=target)


def _set_status(con, pid: int, status: str) -> None:
    con.execute("UPDATE memory SET status=? WHERE id=? AND player=?",
                (status, pid, db.PLAYER))


def _kept(con, pid: int, text: str) -> bool:
    """지킨 약속 하나를 닫고 보상한다. 보상했으면 True.

    보상은 하루 PROMISE_KEPT_DAILY_MAX 건까지. 넘으면 지킨 것으로만 남는다.
    """
    _set_status(con, pid, "kept")
    recall.remember(con, "event", f"상대가 약속을 지켰다: {text}", weight=3)
    if not db.capped(con, "promise_kept", 1, config.PROMISE_KEPT_DAILY_MAX):
        db.log(con, "promise_kept", 0, 0, f"지킨 약속(오늘 보상 끝): {text}")
        return False
    move(con, "trust", config.TRUST_KEPT_PROMISE)
    db.bump(con, "affection", config.AFF_KEPT_PROMISE,
            lo=config.AFF_MIN, hi=config.AFF_MAX)
    db.log(con, "promise_kept", 0, config.AFF_KEPT_PROMISE,
           f"지킨 약속: {text}")
    return True


def _broken(con, pid: int, text: str, age: int, target: str = "") -> None:
    """어긴 약속. 지킬 방법이 정해진 약속은 크게, 말로만 한 것은 작게.

    말로만 한 약속은 지켰는지를 캐릭터가 대화로만 안다 — 실제로는 지켰는데
    말을 안 꺼냈을 수도 있다. 흐지부지된 것으로 본다.
    """
    _set_status(con, pid, "broken")
    hit = (config.TRUST_BROKEN_PROMISE if target
           else config.TRUST_LAPSED_PROMISE)
    move(con, "trust", hit)
    db.log(con, "promise_broken", 0, 0,
           f"{age}일 지난 약속(신뢰 {hit}): {text}")


def fulfil(con, kind: str, key: str = "") -> list:
    """방금 한 일이 지키는 중인 약속을 지켰는가. 지킨 약속의 글 목록.

    kind='date', key='roof' — 옥상 데이트를 다녀왔다
    kind='visit'            — 다시 찾아왔다(기한 안에, 충분히 지나서)
    """
    want = f"{kind}:{key}" if key else kind
    done = []
    for pid, text, target, _age_days in open_promises(con):
        if target != want:
            continue
        if kind == "visit":
            row = con.execute("SELECT ts FROM memory WHERE id=?",
                              (pid,)).fetchone()
            hours = _age(row["ts"]) * 24 if row else 0
            if hours < config.PROMISE_VISIT_MIN_HOURS:
                continue          # 방금 해 놓고 바로 지켰다고 하면 안 된다
        if _kept(con, pid, text):
            done.append(text)
    return done


def keep_by_word(con, pid) -> str:
    """캐릭터가 대화로 판단해 지켰다고 한 약속(kept_promise). 글 또는 ''.

    이행 대상이 정해진 약속은 실제 행동으로만 지킨다 — 말로 "갔다 왔어"
    한다고 데이트가 된 것은 아니다.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    row = con.execute(
        "SELECT text,target FROM memory WHERE id=? AND player=? AND char=? "
        "AND kind='promise' AND status=''",
        (pid, db.PLAYER, db.CHAR)).fetchone()
    if row is None or row["target"]:
        return ""
    return row["text"] if _kept(con, pid, row["text"]) else ""


def _rest_window(ts: str):
    """'오늘 밤은 일찍 쉴게' 를 판정할 밤. (시작, 끝)

    새벽에 한 약속이면 그 새벽(지금부터 아침까지), 아니면 다음 날 새벽.
    """
    t0 = _dt.datetime.fromisoformat(ts)
    wake = max(clock.ODD_HOURS) + 1          # 새벽이 끝나는 시각
    if t0.hour in clock.ODD_HOURS:
        start = t0
        end = t0.replace(hour=wake, minute=0, second=0)
    else:
        night = (t0 + _dt.timedelta(days=1)).replace(hour=0, minute=0,
                                                     second=0)
        start, end = night, night.replace(hour=wake)
    return start, end


def check_rest(con, now=None):
    """'쉬겠다' 는 약속을 근무 기록으로 판정한다. (지킨 것, 어긴 것)

    그날 새벽에 도구 호출이 하나라도 있으면 어긴 것이다. 같은 단말을
    보니까 캐릭터는 안다.
    """
    now = now or _dt.datetime.now()
    kept, broken = [], []
    for pid, text, target, age in open_promises(con):
        if target != "rest":
            continue
        row = con.execute("SELECT ts FROM memory WHERE id=?",
                          (pid,)).fetchone()
        try:
            start, end = _rest_window(row["ts"])
        except (TypeError, ValueError):
            continue
        if now < end:
            continue              # 아직 그 밤이 안 끝났다
        # 활동 시각(시 단위)으로 본다. 장부만 보면 하루 적립 상한을 넘긴
        # 뒤의 작업이 안 보인다(적립 0 이면 장부에 안 남는다).
        worked = con.execute(
            "SELECT 1 FROM activity WHERE player=? AND hour>=? AND hour<? "
            "LIMIT 1", (db.PLAYER, start.strftime("%Y-%m-%dT%H"),
                        end.strftime("%Y-%m-%dT%H"))).fetchone() or \
            con.execute(
                "SELECT 1 FROM ledger WHERE player=? AND char='' "
                "AND kind='tool' AND ts>=? AND ts<? LIMIT 1",
                (db.PLAYER, start.isoformat(timespec="seconds"),
                 end.isoformat(timespec="seconds"))).fetchone()
        if worked:
            _broken(con, pid, text, age, "rest")
            recall.remember(con, "event",
                            f"쉬겠다고 해 놓고 새벽까지 일했다: {text}",
                            weight=3)
            broken.append(text)
        else:
            if _kept(con, pid, text):
                kept.append(text)
    return kept, broken


def check_broken_promises(con):
    """어긴 약속(아직 잊지 않은 것). [(text, 며칠 지났나)]

    반환 모양은 UI 플러그인까지 흘러가는 공개 계약이다(뷰모델의
    broken_promises).
    """
    return [(r["text"], int(_age(r["ts"])))
            for r in promises(con, status="broken", limit=10)]


def settle_promises(con):
    """기한을 넘긴 약속을 어긴 것으로 처리한다. 새로 어긴 건수.

    감점은 약속당 한 번(status 가 broken 이 되는 순간). 어긴 약속은
    PROMISE_FORGET_DAYS 가 더 지나면 잊는다 — 감점이 항상 먼저다.
    '쉬겠다' 는 약속은 기한이 아니라 그 밤의 근무 기록으로 판정한다.
    """
    _kept_rest, broken_rest = check_rest(con)
    hit = len(broken_rest)
    for pid, text, target, age in open_promises(con):
        if target == "rest":
            continue
        if age >= config.PROMISE_GRACE_DAYS:
            _broken(con, pid, text, age, target)
            hit += 1
    for r in promises(con, status="broken", limit=50):
        if _age(r["ts"]) >= (config.PROMISE_GRACE_DAYS
                             + config.PROMISE_FORGET_DAYS):
            _set_status(con, r["id"], "forgotten")
    return hit


def apply_response(con, got: dict, char=None):
    """캐릭터의 응답에 실린 관계 변화를 반영. 실제 반영량을 돌려준다.

    {"trust": 2, ...} 에 더해, 이번 턴에 약속이 생겼으면 "promise_made",
    대화로 지켜진 약속이 있으면 "promise_kept" 에 그 글이 담긴다.
    """
    out = {}
    for field, key in (("trust", "trust_delta"),
                       ("interest", "interest_delta"),
                       ("patience", "patience_delta")):
        try:
            d = int(got.get(key, 0) or 0)
        except (TypeError, ValueError):
            d = 0
        d = max(-config.AXIS_DOWN_MAX,
                min(config.AXIS_UP_MAX.get(field, 2), d))
        if field == "trust" and d > 0:
            # 대화로 쌓는 신뢰도 하루 예산 안에서
            d = db.capped(con, "trust_llm", d, config.TRUST_LLM_DAILY_MAX)
        if d:
            before = db.geti(con, field)
            out[field] = move(con, field, d) - before
    mood = (got.get("mood") or "").strip()
    if mood:
        db.put(con, "mood", mood[:24])
    imp = (got.get("impression") or "").strip()
    if imp:
        db.put(con, "impression", imp[:200])
    doubt = (got.get("doubts") or "").strip()
    if doubt:
        db.put(con, "doubts", doubt[:200])

    kept = keep_by_word(con, got.get("kept_promise") or "")
    if kept:
        out["promise_kept"] = kept
    promise = (got.get("promise") or "").strip()
    if promise and make_promise(con, promise, got.get("promise_target", ""),
                                char):
        out["promise_made"] = promise
    return out


def wants_impression(con) -> bool:
    """이번 턴에 인상을 다시 쓸 때인가."""
    turns = db.geti(con, "turns")
    if not db.get(con, "impression"):
        return True
    return turns > 0 and turns % config.IMPRESSION_EVERY_TURNS == 0


def block(con, st: dict, char, *, boring: str = "") -> str:
    """프롬프트에 넣을 관계 상태 블록."""
    broken = check_broken_promises(con)
    pending = open_promises(con)
    name, tone = char.name, char.tone
    ga, eun = josa(name, "이/가"), josa(name, "은/는")
    lines = [
        f"[{ga} 이 상대를 어떻게 여기는가 — 지금]",
        f"- 호감 {st['affection']}/100   신뢰 {st['trust']}/100   "
        f"관심 {st['interest']}/100   인내 {st['patience']}/100",
        f"- 지금 기분: {st['mood']}",
        "",
        "[이 수치가 뜻하는 태도 — 반드시 지켜라]",
        f"- 신뢰: {_band(tone, 'trust', st['trust'])}",
        f"- 관심: {_band(tone, 'interest', st['interest'])}",
        f"- 인내: {_band(tone, 'patience', st['patience'])}",
    ]
    if st["impression"]:
        lines += ["", f"[{ga} 이 사람에 대해 내린 판단 — {name} 자신의 말]",
                  f"  \"{st['impression']}\""]
    if st["doubts"]:
        lines += ["", f"[{ga} 아직 걸리는 것]", f"  \"{st['doubts']}\""]
    if pending:
        lines += ["", "[아직 지키는 중인 약속 — 번호는 kept_promise 에 쓴다]"]
        for pid, text, target, age in pending[:4]:
            how = target_label(target, char)
            when = "오늘" if age == 0 else f"{age}일 전"
            lines.append(f"  - #{pid} {text} ({when}"
                         + (f", 지키는 방법: {how}" if how else "") + ")")
    if broken:
        lines += ["", f"[지키지 않은 약속 — {eun} 잊지 않았다]"]
        lines += [f"  - {t} ({d}일 지났다)" for t, d in broken[:3]]
    if boring:
        lines += ["", f"[방금 상대의 말에 대해] {boring}이다. "
                      f"{eun} 이런 것에 성의를 보이지 않는다."]
    return "\n".join(lines)


def summary_line(st: dict) -> str:
    """화면 표시용 한 줄."""
    return (f"호감 {st['affection']}  신뢰 {st['trust']}  "
            f"관심 {st['interest']}  인내 {st['patience']}")


def refuses(con, *, need: int, what: str):
    """레이가 거절할 이유가 있으면 사유 문자열, 없으면 None.

    호감도만 채우면 다 열리는 건 사람 같지 않다. 지금 기분과 신뢰도 본다.
    거절당하면 LCL 은 쓰이지 않는다 — 가지 않았으니까.
    """
    patience = db.geti(con, "patience")
    trust = db.geti(con, "trust")
    interest = db.geti(con, "interest")

    if patience < config.PATIENCE_MIN_TALK:
        return "지금 그럴 기분이 아니다"
    # 가까운 곳·귀한 것일수록 신뢰가 받쳐줘야 한다
    if need >= 40 and trust < int(need * 0.6):
        return f"아직 그만큼 믿지 않는다 (신뢰 {trust}, {int(need * 0.6)} 필요)"
    if need >= 25 and interest < 15:
        return "지금은 관심이 없다"
    return None


def refusal_line(char, reason: str):
    import random
    if reason.startswith("아직 그만큼"):
        return char.refusal_trust
    pool = char.refusal.get(reason)
    if pool:
        return random.choice(pool)
    return char.refusal_default
