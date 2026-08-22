# -*- coding: utf-8 -*-
"""HTTP 프로바이더 — API 키를 쓰는 것들과 로컬 서버.

의존성을 늘리지 않으려고 urllib 만 쓴다. 이 게임은 rich 하나로 돈다.

**API 키를 설정 파일에 저장하지 않는다.** 설정에는 '어느 환경변수에서
키를 읽을지' 이름만 넣는다. 키가 평문으로 홈에 굴러다니면 안 되고,
저장소를 통째로 복사·백업하는 사람도 있다.
"""
import json
import os
import urllib.error
import urllib.request

from .base import (BILLING_API, BILLING_NONE, Provider, RESPONSE_SCHEMA,
                   is_local_url)


def _post(url, payload, headers, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError):
        return None


class _KeyedProvider(Provider):
    """API 키가 필요한 프로바이더의 공통부."""

    billing = BILLING_API
    wants_api_key = True

    def api_key(self) -> str:
        return os.environ.get(self.key_env, "").strip()

    def available(self):
        if not self.key_env:
            return False, "어느 환경변수에서 키를 읽을지 정해야 한다"
        if not self.api_key():
            return False, f"환경변수 {self.key_env} 가 비어 있다"
        return True, ""


# ═══════════════════════════════════════════════════════════════════════
#  Anthropic API
# ═══════════════════════════════════════════════════════════════════════
class AnthropicAPI(_KeyedProvider):
    id = "anthropic-api"
    label = "Anthropic API (키)"
    default_model = "claude-sonnet-5"
    default_base_url = "https://api.anthropic.com"
    default_key_env = "ANTHROPIC_API_KEY"
    wants_base_url = True
    note = "토큰당 청구된다. 구독 좌석과는 별개의 지갑이다."

    def complete(self, system, user, *, timeout=None):
        got = _post(
            f"{self.base_url.rstrip('/')}/v1/messages",
            {
                "model": self.model,
                "max_tokens": 700,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": self.api_key(),
             "anthropic-version": "2023-06-01"},
            timeout or self.timeout)
        if not got:
            return None
        blocks = got.get("content") or []
        return "".join(b.get("text", "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")


# ═══════════════════════════════════════════════════════════════════════
#  OpenAI API
# ═══════════════════════════════════════════════════════════════════════
class OpenAIAPI(_KeyedProvider):
    id = "openai-api"
    label = "OpenAI API (키)"
    default_model = "gpt-5.6"
    default_base_url = "https://api.openai.com"
    default_key_env = "OPENAI_API_KEY"
    wants_base_url = True
    note = "토큰당 청구된다. Codex 구독 좌석과는 별개의 지갑이다."

    def _payload(self, system, user):
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": False,
                                "schema": RESPONSE_SCHEMA},
            },
        }

    def complete(self, system, user, *, timeout=None):
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        got = _post(url, self._payload(system, user), headers,
                    timeout or self.timeout)
        if not got:
            # 구조화 출력을 못 받아주는 서버일 수 있다. 한 번만 맨몸으로.
            plain = self._payload(system, user)
            plain.pop("response_format", None)
            got = _post(url, plain, headers, timeout or self.timeout)
        if not got:
            return None
        try:
            return got["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None


# ═══════════════════════════════════════════════════════════════════════
#  OpenAI 호환 — 로컬 서버 / 자체 호스팅 / 그 밖
# ═══════════════════════════════════════════════════════════════════════
class OpenAICompat(OpenAIAPI):
    """LM Studio · vLLM · llama.cpp · 그 밖의 OpenAI 호환 서버.

    과금 여부를 주소로 판단한다. localhost 나 사설망이면 무료로 보고,
    바깥 주소면 과금으로 본다 — 모르면 안전한 쪽으로.
    """

    id = "openai-compat"
    label = "OpenAI 호환 서버"
    default_model = ""
    default_base_url = "http://localhost:1234"
    default_key_env = "OPENAI_COMPAT_API_KEY"
    default_timeout = 240
    note = "LM Studio · vLLM · llama.cpp 등. 로컬 주소면 과금 없음으로 본다."

    def is_billable(self) -> bool:
        return not is_local_url(self.base_url)

    def available(self):
        if not self.base_url:
            return False, "서버 주소가 필요하다"
        if not is_local_url(self.base_url) and not self.api_key():
            return False, f"바깥 주소다 — 환경변수 {self.key_env} 에 키가 필요하다"
        return True, ""

    def api_key(self) -> str:
        # 로컬 서버는 대개 키를 안 본다. 아무 값이나 보내면 된다.
        return os.environ.get(self.key_env, "").strip() or "local"


# ═══════════════════════════════════════════════════════════════════════
#  Ollama
# ═══════════════════════════════════════════════════════════════════════
class Ollama(Provider):
    id = "ollama"
    label = "Ollama (로컬)"
    billing = BILLING_NONE
    wants_base_url = True
    default_base_url = "http://localhost:11434"
    default_timeout = 240        # 로컬 모델은 느리다
    note = "내 기계에서 돈다. 과금도 플랜 소모도 없다. 대신 느리고 덜 똑똑하다."

    # 모델을 안 정했을 때 설치된 것 중에서 고르는 순서.
    #
    # 이 게임은 한국어 대사와 성격 유지가 전부다. 벤치마크 점수가 높은
    # 모델이 아니라 **한국어를 자연스럽게 하고 말투를 지키는** 모델이
    # 필요하다. 실측(레이·아스카·미사토 각 3턴):
    #
    #   exaone3.5:7.8b   ~12초  JSON 7/7  말투 정확 ("그래, 많이 했네.")
    #   gemma3:12b       ~28초  준수      ("많이 했네." "…왜?")
    #   qwen3:8b/14b     ~12초  JSON 실패 잦음, 설명조로 흐름
    #
    # qwen3 는 추론 모드를 끄지 않으면 한 턴에 30초를 더 쓴다.
    PREFERRED = (
        "exaone3.5", "exaone",        # LG. 한국어 특화. 지금 가장 나음
        "gemma3", "gemma2",
        "qwen3", "qwen2.5",
        "llama3.1", "llama3",
    )

    _picked = None

    @property
    def model(self) -> str:
        got = self._per_provider("models", "NERV_LLM_MODEL")
        if got:
            return got
        if Ollama._picked is None:
            Ollama._picked = self._auto_pick()
        return Ollama._picked

    def _auto_pick(self) -> str:
        """설치된 모델 중 이 게임에 맞는 것. 없으면 빈 문자열."""
        installed = self.models()
        if not installed:
            return ""
        for want in self.PREFERRED:
            for name in installed:
                if name.startswith(want):
                    return name
        return installed[0]

    def available(self):
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:                                     # noqa: BLE001
            return False, f"{self.base_url} 에 응답이 없다 (ollama serve 실행?)"
        return True, ""

    def models(self):
        """설치된 모델 목록. 설정 화면이 보여준다."""
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                got = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:                                     # noqa: BLE001
            return []
        return [m.get("name", "") for m in (got.get("models") or [])
                if m.get("name")]

    def complete(self, system, user, *, timeout=None):
        got = _post(
            f"{self.base_url.rstrip('/')}/api/chat",
            {
                "model": self.model,
                "stream": False,
                # format 에 스키마를 주면 ollama 가 문법 수준에서 강제한다.
                # 작은 모델은 부탁만으로는 JSON 을 안 지킨다.
                "format": RESPONSE_SCHEMA,
                # 추론 모드를 끈다. qwen3 같은 모델은 기본으로 켜져 있어서
                # 대사 한 줄 쓰기 전에 한참 생각한다. 실측 qwen3:14b 가
                # 30.4초 → 2.1초. 14배다.
                #
                # 이 게임에 추론은 필요 없다. 캐릭터는 논리 문제를 푸는 게
                # 아니라 성격대로 반응하면 되고, 오히려 길게 생각할수록
                # 설명조의 밋밋한 대사가 나온다.
                "think": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"temperature": 0.8, "num_predict": 700},
            },
            {}, timeout or self.timeout)
        if not got:
            # think 를 모르는 옛 ollama 일 수 있다. 한 번만 빼고 다시.
            got = _post(
                f"{self.base_url.rstrip('/')}/api/chat",
                {
                    "model": self.model, "stream": False,
                    "format": RESPONSE_SCHEMA,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "options": {"temperature": 0.8, "num_predict": 700},
                },
                {}, timeout or self.timeout)
        if not got:
            return None
        return (got.get("message") or {}).get("content")
