# -*- coding: utf-8 -*-
"""과금 안전장치.

돈이 나가는 경로는 하나뿐이다 — API 키를 쓰는 프로바이더.
구독 좌석(claude / codex 로그인)과 로컬 모델은 여기 걸리지 않는다.
**계정 한도를 쓰는 것과 토큰당 청구는 다른 것이다.**

장치는 두 겹이다.

  1. 가드   기본으로 켜져 있다. 켜져 있는 동안은 유료 프로바이더를
            고를 수조차 없다. 끄려면 문장을 타이핑해야 한다.
  2. 상한   가드를 꺼도 남는다. 하루 호출 수를 넘으면 과금을 더 내지
            않고 조용히 사전 작성 대사로 떨어진다.

경고만 띄우고 마는 건 안전장치가 아니다. 실제로 막아야 한다.
"""
from .. import settings

# 가드를 끌 때 타이핑해야 하는 문장.
DISABLE_PHRASE = "과금을 허용한다"


def guard_on() -> bool:
    return bool(settings.get("llm.billing_guard", True))


def set_guard(on: bool) -> None:
    settings.put("llm.billing_guard", bool(on))


def blocked_reason(provider) -> str:
    """이 프로바이더를 지금 고를 수 없는 이유. 고를 수 있으면 빈 문자열."""
    if not provider.is_billable():
        return ""
    if guard_on():
        return "과금 안전장치가 켜져 있다 — 끄기 전에는 고를 수 없다"
    return ""


def daily_cap() -> int:
    """가드를 꺼도 남는 하루 상한. 0 이면 상한 없음."""
    try:
        return max(0, int(settings.get("llm.api_daily_call_cap", 50)))
    except (TypeError, ValueError):
        return 50


def used_today(con) -> int:
    from .. import db
    row = db.daily_row(con)
    try:
        return row["api"] or 0
    except (IndexError, KeyError):
        return 0


def budget_left(con) -> int:
    """남은 유료 호출 수. 상한이 없으면 큰 수."""
    cap = daily_cap()
    if cap <= 0:
        return 1 << 30
    return max(0, cap - used_today(con))


def note_call(con) -> None:
    """유료 호출 1건을 장부에 남긴다."""
    from .. import db
    db.daily_bump(con, "api", 1)


def warning_lines(provider) -> list:
    """유료 프로바이더를 고르기 직전에 보여줄 경고. (tone, text)"""
    return [
        ("danger", f"{provider.label} 는 토큰당 요금이 청구된다."),
        ("plain", "구독 좌석(claude / codex 로그인)과는 별개의 지갑이다."),
        ("plain", f"대사 한 줄마다 API 호출이 하나 나간다. "
                  f"하루 상한은 {daily_cap()}회로 잡혀 있다."),
        ("plain", "키는 설정 파일에 저장되지 않는다 — 환경변수에서 읽는다."),
    ]
