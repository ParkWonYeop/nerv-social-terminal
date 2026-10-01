# -*- coding: utf-8 -*-
"""HTTP 프로바이더 — API 키를 쓰는 것들과 로컬 서버.

의존성을 늘리지 않으려고 urllib 만 쓴다. 이 게임은 rich 하나로 돈다.

**API 키를 설정 파일에 저장하지 않는다.** 설정에는 '어느 환경변수에서
키를 읽을지' 이름만 넣는다. 키가 평문으로 홈에 굴러다니면 안 되고,
저장소를 통째로 복사·백업하는 사람도 있다.

**재시도는 서버가 요청 모양을 거절했을 때(4xx)만 한다.** 타임아웃이나
연결 실패까지 다시 보내면 로컬 모델은 240초를 두 번 기다리고(8분 정지),
유료 API 는 같은 요청 값을 두 번 낸다.
"""
import json
import os
import urllib.error
import urllib.request

from .base import (BILLING_API, BILLING_NONE, Provider, RESPONSE_SCHEMA,
                   flatten, is_local_url)

# 4xx 중에서도 이건 모양 문제가 아니다 — 다시 보내도 똑같이 실패한다.
_NO_RETRY = {401, 403, 404, 429}


def _post(url, payload, headers, timeout):
    """(응답 JSON 또는 None, HTTP 상태). 상태 0 은 연결·타임아웃 실패."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), 200
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None, 0


def get_json(url, headers=None, timeout=6):
    """GET 해서 JSON. 실패하면 None — 모델 목록처럼 없어도 되는 것에 쓴다."""
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _why(status: int) -> str:
    """연결 시험에 보여 줄 사유."""
    if status == 0:
        return "연결 실패 또는 시간 초과"
    hint = {400: "요청 모양이나 모델 이름이 맞지 않다",
            401: "키가 틀렸다", 403: "이 키로는 쓸 수 없다",
            404: "그런 모델이나 주소가 없다", 429: "요청이 너무 많다(한도)"}
    return f"HTTP {status}" + (f" — {hint[status]}" if status in hint else "")


def reshape_worth_retry(status: int) -> bool:
    """요청 모양을 바꿔 한 번 더 보낼 만한 실패인가."""
    return 400 <= status < 500 and status not in _NO_RETRY


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

    def _send(self, url, payload, headers, timeout):
        self.calls += 1
        got, status = _post(url, payload, headers, timeout)
        if got is None:
            self.last_error = _why(status)
        return got, status


# ═══════════════════════════════════════════════════════════════════════
#  Anthropic API
# ═══════════════════════════════════════════════════════════════════════
class AnthropicAPI(_KeyedProvider):
    id = "anthropic-api"
    label = "Anthropic API (키)"
    default_model = "claude-sonnet-5-5"
    default_base_url = "https://api.anthropic.com"
    default_key_env = "ANTHROPIC_API_KEY"
    wants_base_url = True
    note = "토큰당 청구된다. 구독 좌석과는 별개의 지갑이다."

    # 대사 한 줄이면 수백 토큰이다. 그런데 현행 모델은 thinking 을
    # 생략하면 adaptive 로 생각부터 하고, 그 토큰도 이 상한에 들어간다.
    # 700 으로 두면 대사를 쓰기도 전에 상한에 닿아 빈 응답(→ 폴백 대사)이
    # 나올 수 있었다. 여유를 두고, 생각의 깊이는 effort 로 줄인다.
    MAX_TOKENS = 2000
    EFFORT = "low"

    def _system_blocks(self, system):
        """[고정부, 가변부] 면 고정부에 캐시 표시를 단다.

        고정부(페르소나·세계·출력 규칙)는 턴마다 바이트 단위로 같다.
        가변부(시각·상태·기억)만 바뀐다. 접두사 캐시라서 순서가 중요하다.
        """
        if isinstance(system, (list, tuple)) and len(system) > 1:
            blocks = [{"type": "text", "text": system[0],
                       "cache_control": {"type": "ephemeral"}}]
            rest = flatten(list(system[1:]))
            if rest:
                blocks.append({"type": "text", "text": rest})
            return blocks
        return flatten(system)

    def payload(self, system, user, schema, *, structured=True):
        body = {
            "model": self.model,
            "max_tokens": self.MAX_TOKENS,
            "system": self._system_blocks(system),
            "messages": [{"role": "user", "content": user}],
        }
        if structured:
            body["output_config"] = {
                "effort": self.EFFORT,
                "format": {"type": "json_schema",
                           "schema": schema or RESPONSE_SCHEMA},
            }
        return body

    def catalog(self):
        if not self.api_key():
            return []
        got = get_json(f"{self.base_url.rstrip('/')}/v1/models?limit=100",
                       {"x-api-key": self.api_key(),
                        "anthropic-version": "2023-06-01"})
        return [(m["id"], m.get("display_name") or m["id"],
                 (m.get("created_at") or "")[:10])
                for m in (got or {}).get("data") or []
                if isinstance(m, dict) and m.get("id")]

    def complete(self, system, user, *, timeout=None, schema=None):
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {"x-api-key": self.api_key(),
                   "anthropic-version": "2023-06-01"}
        wait = timeout or self.timeout
        got, status = self._send(url, self.payload(system, user, schema),
                                 headers, wait)
        if got is None and reshape_worth_retry(status):
            # 옛 모델·프록시가 output_config 를 모를 수 있다. 맨몸으로 한 번.
            got, status = self._send(
                url, self.payload(system, user, schema, structured=False),
                headers, wait)
        if got and got.get("stop_reason") == "refusal":
            self.last_error = "모델이 거절했다 (refusal)"
            return None
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

    def _payload(self, system, user, schema, *, structured=True):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": flatten(system)},
                         {"role": "user", "content": user}],
        }
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": False,
                                "schema": schema or RESPONSE_SCHEMA},
            }
        return body

    def catalog(self):
        """GET /v1/models — 최근 것부터."""
        got = get_json(f"{self.base_url.rstrip('/')}/v1/models",
                       {"Authorization": f"Bearer {self.api_key()}"})
        rows = [m for m in (got or {}).get("data") or []
                if isinstance(m, dict) and m.get("id")]
        rows.sort(key=lambda m: -(m.get("created") or 0))
        return [(m["id"], m["id"], m.get("owned_by") or "") for m in rows]

    def complete(self, system, user, *, timeout=None, schema=None):
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        wait = timeout or self.timeout
        got, status = self._send(url, self._payload(system, user, schema),
                                 headers, wait)
        if got is None and reshape_worth_retry(status):
            # 구조화 출력을 못 받아주는 서버일 수 있다. 한 번만 맨몸으로.
            got, status = self._send(
                url, self._payload(system, user, schema, structured=False),
                headers, wait)
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

    def catalog(self):
        """설치된 모델. 이 게임에 맞는 순서(PREFERRED)가 먼저, 크기 표시."""
        got = get_json(f"{self.base_url.rstrip('/')}/api/tags", timeout=3)
        rows = [m for m in (got or {}).get("models") or []
                if isinstance(m, dict) and m.get("name")]

        def rank(m):
            for i, want in enumerate(self.PREFERRED):
                if m["name"].startswith(want):
                    return i
            return len(self.PREFERRED)
        rows.sort(key=lambda m: (rank(m), m["name"]))
        out = []
        for m in rows:
            size = m.get("size") or 0
            params = (m.get("details") or {}).get("parameter_size") or ""
            note = " · ".join(x for x in (
                params, f"{size / 1e9:.1f} GB" if size else "",
                "한국어 대사에 추천" if rank(m) == 0 else "") if x)
            out.append((m["name"], m["name"], note))
        return out

    def payload(self, system, user, schema, *, think_flag=True):
        body = {
            "model": self.model,
            "stream": False,
            # format 에 스키마를 주면 ollama 가 문법 수준에서 강제한다.
            # 작은 모델은 부탁만으로는 JSON 을 안 지킨다.
            "format": schema or RESPONSE_SCHEMA,
            "messages": [{"role": "system", "content": flatten(system)},
                         {"role": "user", "content": user}],
            "options": {"temperature": 0.8, "num_predict": 700},
        }
        if think_flag:
            # 추론 모드를 끈다. qwen3 같은 모델은 기본으로 켜져 있어서
            # 대사 한 줄 쓰기 전에 한참 생각한다. 실측 qwen3:14b 가
            # 30.4초 → 2.1초. 14배다.
            #
            # 이 게임에 추론은 필요 없다. 캐릭터는 논리 문제를 푸는 게
            # 아니라 성격대로 반응하면 되고, 오히려 길게 생각할수록
            # 설명조의 밋밋한 대사가 나온다.
            body["think"] = False
        return body

    def complete(self, system, user, *, timeout=None, schema=None):
        url = f"{self.base_url.rstrip('/')}/api/chat"
        wait = timeout or self.timeout
        self.calls += 1
        if not self.model:
            self.last_error = "설치된 모델이 없다 (ollama pull …)"
            return None
        got, status = _post(url, self.payload(system, user, schema), {}, wait)
        if got is None and reshape_worth_retry(status):
            # think 를 모르는 옛 ollama 일 수 있다(400). 한 번만 빼고 다시.
            # 타임아웃(상태 0)은 다시 보내지 않는다 — 240초를 또 기다린다.
            self.calls += 1
            got, status = _post(
                url, self.payload(system, user, schema, think_flag=False),
                {}, wait)
        if not got:
            self.last_error = _why(status)
            return None
        return (got.get("message") or {}).get("content")
