# -*- coding: utf-8 -*-
"""시각 — 지금 몇 시인지, 지난번으로부터 얼마나 지났는지.

이게 없으면 캐릭터가 시간대를 추측한다. 그리고 추측이 틀리면 없는
기억까지 만들어낸다. 실제로 오후 두 시에 "오늘은 아침부터 인사가
살갑네" 하고, 있지도 않은 아침 이야기를 지어냈다.

미사토처럼 "이 시간에 커밋이 왜 있어" 가 페르소나에 박혀 있는 인물은
시계가 없으면 그 대사를 아예 못 쓴다. 에밀리아의 팩은 오전 9시부터
오후 5시까지만 나올 수 있는데, 그것도 시각을 알아야 지켜진다.

순수 계산만 한다. DB 도 설정도 건드리지 않는다.
"""
import datetime as _dt

WEEKDAY = ("월", "화", "수", "목", "금", "토", "일")

# (시작 시각, 이름, 캐릭터에게 줄 힌트)
BANDS = [
    (0,  "심야",     "다들 자는 시간이다"),
    (4,  "새벽",     "아직 해가 안 떴다"),
    (7,  "아침",     ""),
    (11, "낮",       ""),
    (17, "저녁",     ""),
    (21, "밤",       "늦은 시간이다"),
]

# 이 시간대에 일하고 있으면 캐릭터가 한마디 할 만하다
ODD_HOURS = range(0, 6)


def band(hour: int):
    """(이름, 힌트). 24시간을 여섯 구간으로."""
    name, hint = BANDS[0][1], BANDS[0][2]
    for start, n, h in BANDS:
        if hour >= start:
            name, hint = n, h
    return name, hint


def now_line(when=None) -> str:
    """'8월 22일 (금) 오후 1시 49분. 낮.' 같은 한 줄."""
    t = when or _dt.datetime.now()
    name, hint = band(t.hour)
    ampm = "오전" if t.hour < 12 else "오후"
    h12 = t.hour % 12 or 12
    out = (f"{t.month}월 {t.day}일 ({WEEKDAY[t.weekday()]}) "
           f"{ampm} {h12}시 {t.minute}분. {name}.")
    if hint:
        out += f" {hint}."
    return out


def is_odd_hour(when=None) -> bool:
    t = when or _dt.datetime.now()
    return t.hour in ODD_HOURS


def ago(then, when=None) -> str:
    """지난 시각으로부터 얼마나 지났는지 사람 말로. 못 재면 빈 문자열."""
    if not then:
        return ""
    if isinstance(then, str):
        try:
            then = _dt.datetime.fromisoformat(then)
        except ValueError:
            return ""
    now = when or _dt.datetime.now()
    secs = (now - then).total_seconds()
    if secs < 0:
        return ""
    mins = secs / 60

    if mins < 2:
        return "방금"
    if mins < 60:
        return f"{int(mins)}분 전"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}시간 전"
    days = hours / 24
    if days < 2:
        return "어제"
    if days < 30:
        return f"{int(days)}일 전"
    months = days / 30
    if months < 12:
        return f"{int(months)}달 전"
    return f"{int(months / 12)}년 전"


def gap_line(last_talk, last_session=None, when=None) -> str:
    """시간 흐름을 한 줄로. 보여줄 게 없으면 빈 문자열.

    last_talk     이 캐릭터와 마지막으로 주고받은 시각
    last_session  직전 접속의 마지막 시각 (있으면 함께 적는다)
    """
    a = ago(last_talk, when)
    if not a:
        return ""
    if a == "방금":
        return "- 조금 전까지 이야기하고 있었다."
    out = f"- 마지막으로 이야기한 것은 {a}."
    b = ago(last_session, when) if last_session else ""
    if b and b != a:
        out += f" 지난 접속은 {b}."
    return out
