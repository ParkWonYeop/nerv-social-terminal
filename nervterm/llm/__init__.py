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
                   BILLING_SUBSCRIPTION, FACTS_SCHEMA, RESPONSE_SCHEMA,
                   Provider, extract_json, flatten, inline_text, normalize)
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


def candidate(provider_id: str = None, model: str = None) -> Provider:
    """설정을 건드리지 않고, 고른 프로바이더·모델로 만든 인스턴스.

    바꾸기 전에 시험해 보려고 쓴다. model 이 None 이면 저장된 값 그대로,
    '' 이면 그 프로바이더의 기본값.
    """
    import copy
    cfg = copy.deepcopy(settings.get("llm", {}) or {})
    pid = provider_id or cfg.get("provider") or DEFAULT_ID
    klass = BY_ID.get(pid, BY_ID[DEFAULT_ID])
    if model is not None:
        models = cfg.get("models")
        cfg["models"] = dict(models) if isinstance(models, dict) else {}
        cfg["models"][klass.id] = model
    return klass(cfg)


_CHECK_SYSTEM = "너는 연결 시험용 응답기다. JSON 객체 하나만 낸다."
_CHECK_USER = ('{"line":"들린다","emotion":"neutral"} 처럼 line 에 짧은 '
               '한국어 한 마디를 담아 JSON 으로 답하라.')


def check(con, provider, *, timeout: int = None):
    """연결 시험 — 실제로 한 턴 부른다. (된다, 첫 대사 또는 실패 사유).

    모델·프로바이더를 바꿀 때 이걸 통과해야 바뀐다. 목록에 있는 모델이라도
    이 계정·이 판의 CLI 에서 못 쓰는 것이 있다 — 실제로 codex 에서
    "not supported when using Codex with a ChatGPT account" 로 거절됐다.
    부른 것은 하루 대사 횟수에 들어간다(유료면 돈도 나간다).
    """
    blocked = guard.blocked_reason(provider)
    if blocked:
        return False, blocked
    ok, why = provider.available()
    if not ok:
        return False, why
    if provider.is_billable() and guard.budget_left(con) <= 0:
        return False, "오늘 유료 호출 상한을 다 썼다"
    if con.in_transaction:
        con.commit()
    provider.calls, provider.last_error = 0, ""
    text = provider.complete(_CHECK_SYSTEM, _CHECK_USER, timeout=timeout,
                             schema=RESPONSE_SCHEMA)
    db.daily_bump(con, "llm", 1)
    if provider.is_billable():
        for _ in range(max(1, provider.calls)):
            guard.note_call(con)
    con.commit()
    if text is None:
        return False, (provider.last_error
                       or "응답이 없다 — 모델 이름이 틀렸거나 이 계정에서 "
                          "쓸 수 없는 모델일 수 있다")
    got = extract_json(text)
    if not isinstance(got, dict) or not str(got.get("line") or "").strip():
        return False, f"JSON 으로 답하지 않았다: {inline_text(text)[:80]}"
    return True, inline_text(str(got["line"]))[:60]


def switch(con, *, provider_id: str = None, model: str = None):
    """프로바이더·모델을 바꾼다 — 연결 시험을 통과할 때만. (됐나, 사유).

    실패하면 설정을 하나도 건드리지 않는다. 안 되는 모델이 저장되면
    캐릭터가 조용히 사전 작성 대사만 하게 된다.
    """
    cand = candidate(provider_id, model)
    ok, detail = check(con, cand)
    if not ok:
        return False, detail
    if model is not None:
        settings.put(f"llm.models.{cand.id}", model)
    if cand.id != (settings.get("llm.provider") or DEFAULT_ID):
        settings.put("llm.provider", cand.id)
        # 옛 공용 키만 비워 둔다 — 그건 프로바이더가 바뀌면 뜻이 달라진다.
        settings.put("llm.model", "")
        settings.put("llm.base_url", "")
    return True, detail


# ── 예산 ───────────────────────────────────────────────────────────────
def budget_left(con) -> int:
    """오늘 남은 대사 생성 횟수. 플랜 상한과 과금 상한 중 작은 쪽."""
    row = db.daily_row(con)
    plan_left = max(0, config.daily_llm_calls() - (row["llm"] or 0))
    if not is_billable():
        return plan_left
    return min(plan_left, guard.budget_left(con))


# ── 한 턴 ──────────────────────────────────────────────────────────────
def ask(con, system, user: str, *, offline: bool = False,
        timeout: int = None, schema: dict = None):
    """캐릭터에게 한 턴 묻는다.

    system  문자열 또는 [고정부, 가변부]
    schema  응답 JSON 스키마. 기본은 캐릭터 응답(RESPONSE_SCHEMA).

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

    # 응답을 기다리는 동안(수 초~수 분) 쓰기 락을 쥐고 있으면 안 된다.
    # 그 사이 에이전트의 훅이 전부 막혀 도구 호출이 멈추고 적립이
    # 버려진다. 여기까지 쌓인 변경(기억 조회 횟수 등)을 먼저 내보낸다.
    if con.in_transaction:
        con.commit()

    billable = provider.is_billable()
    provider.calls = 0
    text = provider.complete(system, user, timeout=timeout,
                             schema=schema or RESPONSE_SCHEMA)

    # 호출이 나갔으면 실패했어도 센다 — 유료라면 이미 돈이 나갔고,
    # 구독이라면 이미 한도를 썼다. 실패를 공짜로 재시도하게 두면
    # 상한이 상한 노릇을 못 한다.
    db.daily_bump(con, "llm", 1)
    if billable:
        # 대사 상한은 '턴' 으로, 돈 상한은 실제 요청 수로 센다.
        for _ in range(max(1, provider.calls)):
            guard.note_call(con)
    con.commit()

    if text is None:
        return None
    return extract_json(text)
