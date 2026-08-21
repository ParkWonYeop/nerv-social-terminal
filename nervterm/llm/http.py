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
    default_model = "qwen3:14b"
    default_base_url = "http://localhost:11434"
    note = "내 기계에서 돈다. 과금도 플랜 소모도 없다. 대신 느리고 덜 똑똑하다."

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
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"temperature": 0.8, "num_predict": 700},
            },
            {}, timeout or self.timeout)
        if not got:
            return None
        return (got.get("message") or {}).get("content")
