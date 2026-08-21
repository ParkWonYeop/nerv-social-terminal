# -*- coding: utf-8 -*-
"""프로바이더가 지킬 계약 + 응답에서 JSON 건져내기.

프로바이더가 하는 일은 하나다: 시스템 프롬프트와 사용자 메시지를 받아
**문자열**을 돌려준다. JSON 으로 만드는 것도, 게임이 쓰는 모양으로
정리하는 것도 전부 여기서 공통으로 한다.

그래서 새 프로바이더를 붙이는 비용이 `complete()` 하나다.
"""
import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)

# ── 과금 분류 ──────────────────────────────────────────────────────────
#
# 이 구분이 안전장치의 전부다. 헷갈리면 안 된다.
#
#   subscription  구독 좌석(claude / codex 로그인)을 쓴다.
#                 토큰당 청구가 없다. 대신 플랜의 5시간·주간 한도를
#                 코딩 작업과 나눠 쓴다.
#   local         내 기계에서 돈다. 전기 말고는 안 든다.
#   api           API 키로 토큰당 청구된다. ← 가드가 막는 것은 이것뿐이다.
BILLING_NONE = "none"            # 로컬
BILLING_SUBSCRIPTION = "subscription"
BILLING_API = "api"

BILLING_KO = {
    BILLING_NONE: "무료 (로컬)",
    BILLING_SUBSCRIPTION: "구독 좌석 (플랜 한도 사용)",
    BILLING_API: "API 과금 (토큰당 청구)",
}


class Provider:
    """대사 한 턴을 만들어 주는 것."""

    id = ""
    label = ""
    billing = BILLING_NONE
    # 설정 화면에 뭘 물어봐야 하는지
    wants_model = True
    wants_base_url = False
    wants_api_key = False
    default_model = ""
    default_base_url = ""
    default_key_env = ""
    # 프로바이더마다 느린 정도가 다르다. 로컬 모델은 한참 걸린다 —
    # 이 기계의 14B 모델이 한 턴에 57초였다. 45초로 잡아 두면 로컬을
    # 고른 사람은 매번 조용히 폴백 대사만 보게 된다.
    default_timeout = 45
    note = ""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    # ── 설정 읽기 ──────────────────────────────────────────────────────
    @property
    def model(self) -> str:
        return (self.cfg.get("model") or "").strip() or self.default_model

    @property
    def base_url(self) -> str:
        return ((self.cfg.get("base_url") or "").strip()
                or self.default_base_url)

    @property
    def key_env(self) -> str:
        return ((self.cfg.get("api_key_env") or "").strip()
                or self.default_key_env)

    @property
    def timeout(self) -> int:
        """설정에 값이 있으면 그것, 없거나 0 이면 프로바이더 기본값."""
        try:
            got = int(self.cfg.get("timeout") or 0)
        except (TypeError, ValueError):
            got = 0
        return got if got > 0 else self.default_timeout

    # ── 프로바이더가 구현할 것 ─────────────────────────────────────────
    def available(self):
        """지금 쓸 수 있는가. (가능한가, 안 되는 이유)"""
        return True, ""

    def complete(self, system: str, user: str, *, timeout: int = None):
        """모델 응답 원문. 실패하면 None — 호출부가 폴백 대사를 쓴다."""
        raise NotImplementedError

    # ── 실제 과금 여부 ─────────────────────────────────────────────────
    def is_billable(self) -> bool:
        """이 설정으로 돌렸을 때 돈이 나가는가.

        기본은 분류 그대로지만, OpenAI 호환 프로바이더처럼 주소에 따라
        달라지는 것은 이걸 덮어쓴다.
        """
        return self.billing == BILLING_API

    def billing_label(self) -> str:
        """화면에 뜨는 과금 설명.

        분류(billing)가 아니라 **지금 설정에서 실제로** 과금되는지를
        말한다. 둘이 갈릴 수 있다 — OpenAI 호환 서버는 분류상 API 지만
        주소가 localhost 면 돈이 나가지 않는다. 분류를 그대로 보여주면
        무료인 설정에 '토큰당 청구' 라고 써 붙이게 된다.
        """
        if self.is_billable():
            return BILLING_KO[BILLING_API]
        if self.billing == BILLING_SUBSCRIPTION:
            return BILLING_KO[BILLING_SUBSCRIPTION]
        return BILLING_KO[BILLING_NONE]


# ═══════════════════════════════════════════════════════════════════════
#  응답에서 JSON 건져내기
# ═══════════════════════════════════════════════════════════════════════
def extract_json(text: str):
    """모델이 코드펜스나 잡담을 붙여도 JSON을 건져낸다."""
    if not text:
        return None
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except Exception:
        pass
    # 첫 { 부터 짝이 맞는 } 까지
    start = t.find("{")
    if start < 0:
        return None
    depth, instr, esc = 0, False, False
    for i, ch in enumerate(t[start:], start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def normalize(obj, *, clamp=3):
    """모델 응답을 게임이 쓰는 모양으로 정리."""
    if not isinstance(obj, dict):
        return None

    def s(k):
        v = obj.get(k, "")
        return v.strip() if isinstance(v, str) else ""

    try:
        d = int(obj.get("affection_delta", 0))
    except (TypeError, ValueError):
        d = 0
    emo = s("emotion") or "neutral"
    if emo not in {"neutral", "slight", "warm", "cold", "curious", "shaken",
                   "annoyed", "distant"}:
        emo = "neutral"

    def axis(key):
        try:
            v = int(obj.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
        return max(-8, min(8, v))

    # 모델이 choices 를 null·문자열 등으로 잘못 내도 죽지 않는다
    raw_choices = obj.get("choices")
    if not isinstance(raw_choices, list):
        raw_choices = []

    return {
        "narration": s("narration"),
        "line": s("line") or "…",
        "emotion": emo,
        "affection_delta": max(-clamp, min(clamp, d)),
        "trust_delta": axis("trust_delta"),
        "interest_delta": axis("interest_delta"),
        "patience_delta": axis("patience_delta"),
        "mood": s("mood"),
        "impression": s("impression"),
        "doubts": s("doubts"),
        "inner": s("inner"),
        "memory": s("memory"),
        "choices": [c.strip() for c in raw_choices
                    if isinstance(c, str) and c.strip()][:4],
    }


# ═══════════════════════════════════════════════════════════════════════
#  구조화 출력 스키마
# ═══════════════════════════════════════════════════════════════════════
#
# 프롬프트로 "JSON 만 내라"고 비는 것보다, 스키마를 강제할 수 있는
# 프로바이더에서는 강제하는 게 낫다. 특히 작은 로컬 모델은
# 부탁만으로는 잘 안 지킨다.
EMOTIONS = ["neutral", "slight", "warm", "cold",
            "curious", "shaken", "annoyed", "distant"]

_PROPERTIES = {
    "narration": {"type": "string"},
    "line": {"type": "string"},
    "emotion": {"type": "string", "enum": EMOTIONS},
    "affection_delta": {"type": "integer"},
    "trust_delta": {"type": "integer"},
    "interest_delta": {"type": "integer"},
    "patience_delta": {"type": "integer"},
    "mood": {"type": "string"},
    "inner": {"type": "string"},
    "memory": {"type": "string"},
    "impression": {"type": "string"},
    "doubts": {"type": "string"},
    "choices": {"type": "array", "items": {"type": "string"}},
}

# OpenAI 계열의 구조화 출력은 `required` 에 **모든** 키가 있어야 하고
# additionalProperties 가 false 여야 한다. 안 지키면 400 이 온다:
#   "'required' is required to be … including every key in properties"
#
# 전부 required 로 두어도 게임은 멀쩡하다. 안 물어본 턴에는 모델이
# impression/doubts 를 빈 문자열로 내고, stance 쪽이 빈 값은 무시한다.
# 스키마를 두 벌로 나누는 것보다 이게 낫다.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": _PROPERTIES,
    "required": list(_PROPERTIES),
    "additionalProperties": False,
}


def is_local_url(url: str) -> bool:
    """이 주소가 내 기계(또는 사설망)를 가리키는가.

    OpenAI 호환 프로바이더는 주소에 따라 과금이 되기도, 안 되기도 한다.
    로컬이면 가드로 막을 이유가 없다.
    """
    if not url:
        return False
    low = url.lower()
    for mark in ("://localhost", "://127.", "://0.0.0.0", "://[::1]",
                 "://192.168.", "://10.", "://host.docker.internal"):
        if mark in low:
            return True
    # 172.16.0.0 ~ 172.31.255.255
    m = re.search(r"://172\.(\d{1,3})\.", low)
    if m and 16 <= int(m.group(1)) <= 31:
        return True
    return False
