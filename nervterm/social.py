# -*- coding: utf-8 -*-
"""캐릭터 간 인지 — 같은 단말을 보는 사람들은 서로의 일을 안다.

관계는 여전히 사람마다 따로다. 호감·신뢰·기억·대화는 그 사람과만의
것이고 남에게 넘어가지 않는다. 넘어가는 것은 **남의 눈에 보이는 일**
뿐이다 — 누구와 어디에 갔는지, 누구에게 무엇을 줬는지, 요즘 누구를
자주 찾는지. 같은 본부에서 일하면 그 정도는 귀에 들어온다.

같은 세계의 사람끼리만 안다. 에밀리아는 레이를 모른다.

    log(con, char_id, kind, key, label)   남의 눈에 보이는 일 하나
    block(con, char)                      이 사람이 아는 남들의 일 (프롬프트)

설정 → 대화 → '캐릭터 간 인지' 로 끌 수 있다.
"""
import datetime as _dt

from . import clock, db, settings
from .hangul import josa

# 기록하는 것. 대화 내용은 여기 없다.
KINDS = ("visit", "date", "gift", "episode", "stage")

LOOKBACK_DAYS = 14          # 이보다 오래된 일은 화제가 아니다
RECENT_VISIT_DAYS = 7       # '요즘 누구를 자주 찾는가' 의 창


def enabled() -> bool:
    return bool(settings.get("social.aware", True))


def log(con, char_id: str, kind: str, key: str = "", label: str = "") -> None:
    con.execute(
        "INSERT INTO social(player,char,ts,kind,key,label) "
        "VALUES(?,?,?,?,?,?)",
        (db.PLAYER, char_id, db.now(), kind, key or "", label or ""))


def _world_of(char_id: str) -> str:
    from . import characters, plugins
    plug = plugins.get("character", characters.pack_of(char_id))
    return plug.world if plug is not None else ""


def peers(char) -> list:
    """같은 세계의, 이 사람이 아닌 캐릭터들(설치된 전부)."""
    from . import characters
    mine = _world_of(char.id)
    out = []
    for cid in characters.IDS:
        if cid == char.id:
            continue
        if _world_of(cid) == mine:
            other = characters.get(cid)
            if other is not None:
                out.append(other)
    return out


def _since(con, char, since=None) -> str:
    """'요전에 만난 뒤' 의 기준. 처음이면 LOOKBACK_DAYS 전부터.

    since — 이번 접속이 시작되기 전의 last_seen. 게임은 찾아온 순간
    last_seen 을 지금으로 갱신하므로, 저장소에서 다시 읽으면 '방금 이후'
    가 돼서 남들의 일이 하나도 안 잡힌다.
    """
    floor = (_dt.datetime.now() - _dt.timedelta(days=LOOKBACK_DAYS)
             ).isoformat(timespec="seconds")
    last = since if since is not None else db.get(con, "last_seen",
                                                  char=char.id)
    return max(last or floor, floor)


def happenings(con, char, *, limit: int = 4, since=None):
    """이 사람이 아는 남들의 일. [(ts, 상대 캐릭터, kind, label)] 최신순."""
    others = {c.id: c for c in peers(char)}
    if not others:
        return []
    marks = ",".join("?" * len(others))
    rows = con.execute(
        f"SELECT ts,char,kind,label FROM social WHERE player=? AND ts>? "
        f"AND char IN ({marks}) AND kind<>'visit' ORDER BY id DESC LIMIT ?",
        (db.PLAYER, _since(con, char, since), *others, limit)).fetchall()
    return [(r["ts"], others[r["char"]], r["kind"], r["label"]) for r in rows]


def visit_counts(con, char) -> dict:
    """요즘 누구를 몇 번 찾았나. {캐릭터 id: 횟수} — 이 사람 포함."""
    ids = [char.id] + [c.id for c in peers(char)]
    since = (_dt.datetime.now() - _dt.timedelta(days=RECENT_VISIT_DAYS)
             ).isoformat(timespec="seconds")
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT char, COUNT(*) n FROM social WHERE player=? AND ts>? "
        f"AND kind='visit' AND char IN ({marks}) GROUP BY char",
        (db.PLAYER, since, *ids)).fetchall()
    return {r["char"]: r["n"] for r in rows}


def _phrase(other, kind: str, label: str) -> str:
    who = other.name
    if kind == "date":
        return f"상대는 {josa(who, '과/와')} {label}에 갔다."
    if kind == "gift":
        return f"상대는 {who}에게 {josa(label, '을/를')} 줬다."
    if kind == "episode":
        return f"상대는 {josa(who, '과/와')} '{label}' 일을 함께 겪었다."
    if kind == "stage":
        return f"상대와 {who} 사이가 꽤 가까워졌다 ('{label}')."
    return ""


def block(con, char, when=None, since=None) -> str:
    """프롬프트에 넣을 '남들의 일'. 알 것이 없으면 빈 문자열."""
    if not enabled():
        return ""
    others = peers(char)
    if not others:
        return ""
    events = happenings(con, char, since=since)
    counts = visit_counts(con, char)
    mine = counts.get(char.id, 0)
    favorite = max(((n, cid) for cid, n in counts.items() if cid != char.id),
                   default=(0, ""))
    if not events and favorite[0] <= mine:
        return ""

    name = char.name
    lines = [f"[다른 사람들과의 일 — {name}도 같은 단말을 보니 안다]"]
    mentioned = []
    for ts, other, kind, label in events:
        text = _phrase(other, kind, label)
        if not text:
            continue
        lines.append(f"- {clock.ago(ts, when) or '최근'}, {text}")
        if other not in mentioned:
            mentioned.append(other)
    if favorite[0] > mine:
        fav = next(c for c in others if c.id == favorite[1])
        lines.append(f"- 이번 주에 상대가 가장 자주 찾은 사람은 {fav.name}"
                     f"({favorite[0]}번)이다. {josa(name, '은/는')} {mine}번.")
        if fav not in mentioned:
            mentioned.append(fav)

    attitude = getattr(char, "others", None) or {}
    for other in mentioned:
        note = attitude.get(other.id)
        if note:
            lines.append(f"- {name}에게 {other.name}: {note}")

    lines += ["- 이걸 매번 꺼내지 마라. 걸릴 때만, 성격대로.",
              "- 어디에 갔고 무엇을 줬는지만 안다. 둘이 나눈 이야기는 모른다 "
              "— 아는 척하지 마라."]
    return "\n".join(lines)
