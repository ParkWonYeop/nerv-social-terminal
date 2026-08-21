# -*- coding: utf-8 -*-
"""레이의 기억.

세 층으로 나뉜다.
  1. 방금 대화        — dialogue 테이블의 이번 접속분
  2. 지난번 대화       — 직전 접속의 마지막 몇 마디
  3. 오래된 기억       — memory 테이블. 관련성 + 최근성 + 중요도로 골라 꺼낸다

임베딩을 쓰지 않는다. 한국어는 조사가 붙어 단어 일치가 잘 안 되므로
글자 2-gram 으로 겹침을 잰다. 순수 파이썬, 추가 의존성 없음.
"""
import datetime as _dt
import math
import re

from . import db

STOP = {"그래", "그거", "이거", "저거", "그리고", "하지만", "나는", "너는",
        "있어", "없어", "해서", "하는", "이런", "그런", "什么"}
_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9_.-]{1,}|\d{2,}")

def ensure(con):
    """스키마는 db.init 이 만든다. 여기서는 아무것도 하지 않는다."""
    return None


# ── 토큰화 ─────────────────────────────────────────────────────────────
def grams(text: str) -> set:
    """글자 2-gram + 라틴 단어."""
    out = set()
    for w in _WORD.findall(text or ""):
        if w in STOP:
            continue
        if re.match(r"[가-힣]", w):
            for i in range(len(w) - 1):
                out.add(w[i:i + 2])
            if len(w) >= 2:
                out.add(w)
        else:
            out.add(w.lower())
    return out


def seq_grams(text: str) -> set:
    """공백·문장부호를 지운 통짜 문자열의 2-gram.

    조사와 어미가 달라지는 한국어에서 '같은 말인지' 판정할 때 쓴다.
    단어 단위 grams() 는 '상대는'/'상대가' 를 다른 것으로 보지만
    이쪽은 '상대' 를 공통으로 잡아낸다.
    """
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", (text or "").lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _age_days(ts: str) -> float:
    try:
        return max(0.0, (_dt.datetime.now() -
                         _dt.datetime.fromisoformat(ts)).total_seconds() / 86400)
    except Exception:
        return 999.0


# ── 쓰기 ───────────────────────────────────────────────────────────────
def remember(con, kind: str, text: str, weight: int = 1, char=None) -> bool:
    """기억을 남긴다. 이미 비슷한 게 있으면 중요도만 올린다."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) < 4:
        return False
    g, gw = seq_grams(text), grams(text)
    if not g:
        return False
    c = char if char is not None else db.CHAR
    for r in con.execute(
            "SELECT id,text,weight FROM memory WHERE player=? AND char=? "
            "ORDER BY id DESC LIMIT 60", (db.PLAYER, c)):
        og, ogw = seq_grams(r["text"]), grams(r["text"])
        if not og:
            continue
        inter = len(g & og)
        jacc = inter / max(1, len(g | og))
        contain = inter / max(1, min(len(g), len(og)))
        # 문자열 2-gram 은 조사·어미 차이를 넘지만("일한다"/"일한다고"),
        # 어미가 겹치는 것만으로 "수족관에 갔다"/"옥상에 갔다" 를 붙여버린다.
        # 그래서 단어 단위 겹침을 한 번 더 확인한다.
        word_jacc = len(gw & ogw) / max(1, len(gw | ogw))
        if (jacc >= 0.6 or contain >= 0.7) and word_jacc >= 0.35:
            con.execute(
                "UPDATE memory SET weight=MIN(weight+1,5), ts=? WHERE id=?",
                (db.now(), r["id"]))
            return False
    con.execute(
        "INSERT INTO memory(player,char,ts,kind,text,weight,hits,last_used) "
        "VALUES(?,?,?,?,?,?,0,'')",
        (db.PLAYER, c, db.now(), kind, text[:280], weight))
    return True


# ── 읽기 ───────────────────────────────────────────────────────────────
def relevant(con, query: str = "", n: int = 8):
    """지금 대화에 관련된 기억을 고른다. [(text, kind)] 최신순."""
    rows = con.execute(
        "SELECT id,ts,kind,text,weight FROM memory WHERE player=? AND char=? "
        "ORDER BY id DESC LIMIT 200", (db.PLAYER, db.CHAR)).fetchall()
    if not rows:
        return []
    q = grams(query)
    scored = []
    for r in rows:
        g = grams(r["text"])
        overlap = len(q & g) / math.sqrt(len(g) + 1) if q and g else 0.0
        recency = 1.0 / (1.0 + _age_days(r["ts"]) / 7.0)
        score = overlap * 4.0 + recency * 1.0 + (r["weight"] - 1) * 0.5
        if q and not overlap:
            score -= 0.8                       # 물어본 것과 무관하면 뒤로
        if r["kind"] in ("gift", "date", "promise"):
            score += 0.6                       # 사건은 오래 남는다
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    top = [r for _, r in scored[:n]]
    ids = [r["id"] for r in top]
    if ids:
        con.execute(
            f"UPDATE memory SET hits=hits+1, last_used=? "
            f"WHERE id IN ({','.join('?' * len(ids))})", [db.now(), *ids])
    top.sort(key=lambda r: r["id"])
    return [(r["text"], r["kind"]) for r in top]


def last_conversation(con, current_sess: str, n: int = 6):
    """직전 접속에서 나눈 마지막 대화. [(role, text)]"""
    row = con.execute(
        "SELECT sess FROM dialogue WHERE player=? AND char=? "
        "AND sess<>'' AND sess<>? "
        "ORDER BY id DESC LIMIT 1", (db.PLAYER, db.CHAR, current_sess)).fetchone()
    if not row:
        return []
    rows = con.execute(
        "SELECT role,text FROM dialogue WHERE player=? AND char=? AND sess=? "
        "AND role IN ('user','rei') ORDER BY id DESC LIMIT ?",
        (db.PLAYER, db.CHAR, row["sess"], n)).fetchall()
    return [(r["role"], r["text"]) for r in reversed(rows)]


def this_conversation(con, sess: str, n: int = 10):
    rows = con.execute(
        "SELECT role,text FROM dialogue WHERE player=? AND char=? AND sess=? "
        "AND role IN ('user','rei') ORDER BY id DESC LIMIT ?",
        (db.PLAYER, db.CHAR, sess, n)).fetchall()
    return [(r["role"], r["text"]) for r in reversed(rows)]


def render(pairs, name="레이") -> str:
    return "\n".join(
        f"    {'상대' if role == 'user' else name}: {text}"
        for role, text in pairs)


# ── 압축 ───────────────────────────────────────────────────────────────
def pending_count(con) -> int:
    """아직 기억으로 압축되지 않은 대화 줄 수."""
    mark = db.geti(con, "consolidated_upto", 0)
    r = con.execute(
        "SELECT COUNT(*) n FROM dialogue WHERE player=? AND char=? AND id>? "
        "AND role IN ('user','rei')", (db.PLAYER, db.CHAR, mark)).fetchone()
    return r["n"] or 0


def consolidate(con, ask_fn, threshold: int = 30, name: str = "레이") -> int:
    """오래된 대화를 기억 몇 줄로 압축한다. LLM 호출 1회. 만든 기억 수 반환.

    ask_fn(system, user) -> dict|None  (llm.ask 를 감싼 것)
    """
    if pending_count(con) < threshold:
        return 0
    mark = db.geti(con, "consolidated_upto", 0)
    rows = con.execute(
        "SELECT id,role,text FROM dialogue WHERE player=? AND char=? AND id>? "
        "AND role IN ('user','rei') ORDER BY id LIMIT 80",
        (db.PLAYER, db.CHAR, mark)).fetchall()
    if not rows:
        return 0
    convo = "\n".join(
        f"{'상대' if r['role'] == 'user' else name}: {r['text']}" for r in rows)

    # 이미 아는 것을 알려줘야 같은 기억이 다른 문장으로 또 들어오지 않는다.
    # 문자열 유사도만으로는 "고양이 두 마리(나비, 초코)를 키움" 과
    # "상대는 고양이 두 마리를 키운다. 이름은 나비와 초코." 를 못 붙인다.
    known = [r["text"] for r in con.execute(
        "SELECT text FROM memory WHERE player=? AND char=? "
        "ORDER BY weight DESC, id DESC LIMIT 40", (db.PLAYER, db.CHAR))]
    known_block = ("\n[이미 기억하고 있는 것 — 이것과 같은 내용은 절대 다시 내지 마라]\n"
                   + "\n".join(f"  - {k}" for k in known)) if known else ""

    system = (
        "너는 대화 기록에서 '기억할 만한 사실'만 뽑아내는 도구다.\n"
        "아래 대화에서 나중에 다시 꺼낼 가치가 있는 것만 골라라.\n"
        "우선순위: (1) 두 사람이 한 약속, (2) 상대가 밝힌 사실"
        "(이름·가족·직업·취향·습관·사정), (3) 실제로 일어난 사건, "
        "(4) 감정이 크게 움직인 순간.\n"
        "약속은 절대 빠뜨리지 마라. 인사·잡담·의미 없는 말은 버려라.\n"
        "아무것도 없으면 빈 배열을 내라.\n"
        "여러 사실을 한 문장에 묶지 마라. 하나씩 따로 쓴다.\n\n"
        "JSON 하나만 출력한다. 코드펜스 금지.\n"
        '{"facts":[{"text":"한 문장으로 쓴 사실","kind":"promise|fact|event"}]}\n'
        "각 text 는 40자 이내. 최대 7개."
        + known_block)
    got = ask_fn(system, f"[대화 기록]\n{convo}")

    made = 0
    if isinstance(got, dict):
        for f in (got.get("facts") or [])[:7]:
            if isinstance(f, dict):
                text, kind = f.get("text", ""), f.get("kind", "fact")
            elif isinstance(f, str):
                text, kind = f, "fact"
            else:
                continue
            if kind not in ("promise", "fact", "event"):
                kind = "fact"
            w = 3 if kind == "promise" else 2
            if isinstance(text, str) and remember(con, kind, text, weight=w):
                made += 1
    db.put(con, "consolidated_upto", rows[-1]["id"])
    prune(con)
    return made


def prune(con, keep: int = 120):
    """기억이 무한정 늘어나지 않게 정리. 약속과 중요한 것은 남긴다."""
    total = con.execute(
        "SELECT COUNT(*) n FROM memory WHERE player=? AND char=?",
        (db.PLAYER, db.CHAR)).fetchone()["n"]
    if total <= keep:
        return 0
    rows = con.execute("SELECT id,ts,kind,weight,hits FROM memory "
                       "WHERE player=? AND char=?",
                       (db.PLAYER, db.CHAR)).fetchall()
    scored = []
    for r in rows:
        score = (r["weight"] * 2.0 + min(r["hits"], 5) * 0.4
                 - _age_days(r["ts"]) / 30.0)
        if r["kind"] in ("promise", "date", "gift"):
            score += 2.0
        scored.append((score, r["id"]))
    scored.sort()
    drop = [i for _, i in scored[:total - keep]]
    if drop:
        con.execute(
            f"DELETE FROM memory WHERE id IN ({','.join('?' * len(drop))})", drop)
    return len(drop)
