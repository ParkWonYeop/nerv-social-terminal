# -*- coding: utf-8 -*-
"""대사 생성 — 어느 모델을 쓰든 게임 쪽 호출은 하나다.

    llm.ask(con, system, user)  →  dict 또는 None

None 이 오면 호출부가 사전 작성 대사를 쓴다. 그래서 모델이 없어도,
한도를 넘어도, 서버가 죽어도 게임은 계속 돈다.

프로바이더를 붙이는 비용은 `complete()` 하나다 — JSON 처리와 예산
관리는 여기서 공통으로 한다.
"""
from .. import config, db, settings
from . import guard
from .base import (BILLING_API, BILLING_KO, BILLING_NONE,
                   BILLING_SUBSCRIPTION, Provider, extract_json, normalize)
from .cli import ClaudeCLI, CodexCLI, CodexLocalCLI
from .http import AnthropicAPI, Ollama, OpenAIAPI, OpenAICompat

# 설정 화면에 뜨는 순서. 무료·안전한 것이 위로 온다.
CATALOG = [
    ClaudeCLI,
    CodexCLI,
    Ollama,
    CodexLocalCLI,
    OpenAICompat,
    AnthropicAPI,
    OpenAIAPI,
]

BY_ID = {p.id: p for p in CATALOG}
DEFAULT_ID = ClaudeCLI.id


# ── 지금 쓰는 프로바이더 ───────────────────────────────────────────────
def current() -> Provider:
    """설정에서 고른 프로바이더 인스턴스.

    고른 것이 과금 가드에 막혀 있으면 기본값으로 되돌린다 — 설정
    파일을 손으로 고쳐 가드를 우회하는 길을 막는다.
    """
    cfg = settings.get("llm", {}) or {}
    klass = BY_ID.get(cfg.get("provider") or DEFAULT_ID, BY_ID[DEFAULT_ID])
    got = klass(cfg)
    if guard.blocked_reason(got):
        return BY_ID[DEFAULT_ID](cfg)
    return got


def provider_label() -> str:
    return current().label


def is_billable() -> bool:
    return current().is_billable()


def available() -> bool:
    """지금 대사를 만들 수 있는가."""
    ok, _ = current().available()
    return ok


def probe(provider=None):
    """(가능한가, 사유) — 설정 화면이 초록/빨강을 칠할 때 쓴다."""
    p = provider or current()
    blocked = guard.blocked_reason(p)
    if blocked:
        return False, blocked
    return p.available()


# ── 예산 ───────────────────────────────────────────────────────────────
def budget_left(con) -> int:
    """오늘 남은 대사 생성 횟수. 플랜 상한과 과금 상한 중 작은 쪽."""
    row = db.daily_row(con)
    plan_left = max(0, config.daily_llm_calls() - (row["llm"] or 0))
    if not is_billable():
        return plan_left
    return min(plan_left, guard.budget_left(con))


# ── 한 턴 ──────────────────────────────────────────────────────────────
def ask(con, system: str, user: str, *, offline: bool = False,
        timeout: int = None):
    """캐릭터에게 한 턴 묻는다.

    성공하면 dict, 못 쓰면 None(→ 호출부가 폴백 대사 사용).
    """
    if offline:
        return None

    provider = current()
    ok, _why = provider.available()
    if not ok:
        return None
    if budget_left(con) <= 0:
        return None

    billable = provider.is_billable()
    text = provider.complete(system, user, timeout=timeout)

    # 호출이 나갔으면 실패했어도 센다 — 유료라면 이미 돈이 나갔고,
    # 구독이라면 이미 한도를 썼다. 실패를 공짜로 재시도하게 두면
    # 상한이 상한 노릇을 못 한다.
    db.daily_bump(con, "llm", 1)
    if billable:
        guard.note_call(con)
    con.commit()

    if text is None:
        return None
    return extract_json(text)
