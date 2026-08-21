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

from . import config, db, recall

AXES = ("affection", "trust", "interest", "patience")

LOW_CONTENT = {"ㅇㅇ", "ㅇㅋ", "ㄱㄱ", "웅", "응", "어", "그래", "ㅎㅎ", "ㅋㅋ",
               "ㅋㅋㅋ", "네", "예", "음", "흠", "아", "오", "?", "??", "…",
               "ok", "okay", "k", "y", "yes", "no", "hi", "hello"}


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


def recover_patience(con):
    """인내는 시간이 지나면 돌아온다."""
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
    gain = int(hours * config.PATIENCE_RECOVER_PER_HOUR)
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
    if len(t) <= 2 or t.lower() in LOW_CONTENT:
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


def check_broken_promises(con):
    """지키지 않은 약속을 찾는다. [(text, 며칠 지났나)]"""
    rows = con.execute(
        "SELECT id,ts,text FROM memory WHERE player=? AND char=? "
        "AND kind='promise' ORDER BY id DESC LIMIT 10",
        (db.PLAYER, db.CHAR)).fetchall()
    out = []
    for r in rows:
        if db.flag(con, f"promise_done_{r['id']}"):
            continue
        try:
            age = (_dt.datetime.now() -
                   _dt.datetime.fromisoformat(r["ts"])).days
        except ValueError:
            continue
        if age >= config.PROMISE_GRACE_DAYS:
            out.append((r["text"], age))
    return out


def settle_promises(con):
    """오래 방치된 약속만큼 신뢰를 깎는다. 한 약속당 한 번만."""
    hit = 0
    for text, age in check_broken_promises(con):
        row = con.execute(
            "SELECT id FROM memory WHERE player=? AND char=? "
            "AND kind='promise' AND text=?",
            (db.PLAYER, db.CHAR, text)).fetchone()
        if not row:
            continue
        key = f"promise_penalized_{row['id']}"
        if db.flag(con, key):
            continue
        db.flag(con, key, "1")
        move(con, "trust", config.TRUST_BROKEN_PROMISE)
        db.log(con, "promise_broken", 0, 0, f"{age}일 지난 약속: {text}")
        hit += 1
    return hit


def apply_response(con, got: dict):
    """레이의 응답에 실린 관계 변화를 반영. 실제 반영량을 돌려준다."""
    out = {}
    for field, key in (("trust", "trust_delta"),
                       ("interest", "interest_delta"),
                       ("patience", "patience_delta")):
        try:
            d = int(got.get(key, 0) or 0)
        except (TypeError, ValueError):
            d = 0
        d = max(-8, min(8, d))
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
    name, tone = char.name, char.tone
    lines = [
        f"[{name}가 이 상대를 어떻게 여기는가 — 지금]",
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
        lines += ["", f"[{name}가 이 사람에 대해 내린 판단 — {name} 자신의 말]",
                  f"  \"{st['impression']}\""]
    if st["doubts"]:
        lines += ["", f"[{name}가 아직 걸리는 것]", f"  \"{st['doubts']}\""]
    if broken:
        lines += ["", f"[지키지 않은 약속 — {name}는 잊지 않았다]"]
        lines += [f"  - {t} ({d}일 지났다)" for t, d in broken[:3]]
    if boring:
        lines += ["", f"[방금 상대의 말에 대해] {boring}이다. "
                      f"{name}는 이런 것에 성의를 보이지 않는다."]
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
