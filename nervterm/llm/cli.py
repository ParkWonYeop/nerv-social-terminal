# -*- coding: utf-8 -*-
"""구독 좌석을 쓰는 CLI 프로바이더 — Claude Code / Codex.

둘 다 로그인한 계정의 플랜 좌석으로 돈다. 토큰당 청구가 없다.
대신 플랜의 사용량 한도(5시간 창·주간)를 코딩 작업과 나눠 쓰기 때문에,
프롬프트를 최대한 벗기고 하루 호출 상한을 둔다.

**계정 한도를 쓰는 것과 API 과금은 다른 것이다.** 그래서 이 둘은
과금 가드가 막지 않는다.
"""
import json
import os
import shutil
import subprocess
import tempfile

from .. import config
from .base import BILLING_SUBSCRIPTION, RESPONSE_SCHEMA, Provider, flatten


def _tail(text, n=200) -> str:
    """실패 사유 한 줄. 오류 JSON 이 섞여 있으면 그 안의 message 만.

    codex 는 'ERROR: {"type":"error","status":400,"error":{"message":…}}'
    처럼 원문을 그대로 찍는다 — 그대로 보여 주면 읽을 수가 없다.
    """
    import re
    raw = str(text or "")
    found = re.findall(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if found:
        return found[-1].replace('\\"', '"')[:n]
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    return " / ".join(lines[-2:])[-n:] or "응답이 없다"


def _codex_home():
    override = os.environ.get("CODEX_HOME")
    from pathlib import Path
    return Path(override).expanduser() if override else Path.home() / ".codex"


def _game_env():
    """게임이 띄운 에이전트가 훅을 되돌려 발동시키지 않게.

    이게 없으면 대사 한 줄 만들 때마다 훅이 돌아서 재화가 적립되고,
    캐릭터가 자기 대사를 근무 실적으로 착각한다.
    """
    env = dict(os.environ)
    env["REI_GAME"] = "1"          # 옛 이름 — 설치된 훅이 아직 이걸 본다
    env["NERV_GAME"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)   # 구독 좌석으로만 돌린다
    return env


# ═══════════════════════════════════════════════════════════════════════
#  Claude Code
# ═══════════════════════════════════════════════════════════════════════
class ClaudeCLI(Provider):
    id = "claude-cli"
    label = "Claude Code (구독 좌석)"
    billing = BILLING_SUBSCRIPTION
    default_model = "sonnet"
    note = "claude 로그인 계정의 플랜 한도를 쓴다. 토큰당 청구 없음."

    # claude 에는 모델 목록 명령이 없다. 대신 별칭은 언제나 그 계열의
    # 최신 모델을 가리킨다(claude --help: "an alias for the latest model").
    # 그래서 별칭이 맨 위다 — 고르면 앞으로 새 모델이 나와도 따라간다.
    ALIASES = [
        ("sonnet", "Sonnet — 최신", "기본값. 대사 한 줄에 충분하고 빠르다"),
        ("opus", "Opus — 최신", "더 깊다. 플랜 한도를 더 쓴다"),
        ("haiku", "Haiku — 최신", "가장 빠르고 가볍다"),
        ("fable", "Fable — 최신", "가장 강력하다. 플랜 한도를 가장 많이 쓴다"),
    ]
    # 키가 없을 때 보여줄 정확한 이름. 키가 있으면 Models API 에서 받는다.
    KNOWN = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5",
             "claude-fable-5-1"]

    def catalog(self):
        out = list(self.ALIASES)
        exact = []
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if key:
            from .http import get_json
            got = get_json("https://api.anthropic.com/v1/models?limit=100",
                           {"x-api-key": key,
                            "anthropic-version": "2023-06-01"})
            for m in (got or {}).get("data") or []:
                if isinstance(m, dict) and m.get("id"):
                    exact.append((m["id"], m.get("display_name") or m["id"],
                                  "정확한 이름 — 이 모델에 고정된다"))
        if not exact:
            exact = [(mid, mid, "정확한 이름 — 이 모델에 고정된다")
                     for mid in self.KNOWN]
        return out + exact

    def available(self):
        if shutil.which("claude") is None:
            return False, "claude 명령을 찾을 수 없다"
        return True, ""

    def _lean_settings(self) -> str:
        """훅·상태줄이 꺼진 설정 파일."""
        from .. import identity
        p = identity.data_dir() / "lean-settings.json"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(
                {"hooks": {}, "statusLine": {"type": "command",
                                             "command": "true"}}),
                encoding="utf-8")
        return str(p)

    def complete(self, system, user, *, timeout=None, schema=None):
        # claude -p 는 스키마를 강제할 길이 없다 — 프롬프트의 출력 규칙과
        # extract_json() 이 대신한다. 고정부가 앞에 오므로 Claude Code 의
        # 접두사 캐시가 턴마다 맞는다.
        system = flatten(system)
        self.calls = 1
        cmd = [
            "claude", "-p",
            "--model", self.model,
            "--effort", config.EFFORT,
            "--output-format", "json",
            "--settings", self._lean_settings(),
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--disallowed-tools", *config.LLM_DISALLOWED.split(),
            "--max-turns", "1",
            "--system-prompt", system,
            user,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout or self.timeout, env=_game_env())
        except subprocess.TimeoutExpired:
            self.last_error = f"시간 초과 ({timeout or self.timeout}초)"
            return None
        except OSError as exc:
            self.last_error = str(exc)
            return None
        try:
            envelope = json.loads(proc.stdout)
        except Exception:
            envelope = None
        if proc.returncode != 0 or not isinstance(envelope, dict):
            self.last_error = _tail(
                (envelope or {}).get("result") if isinstance(envelope, dict)
                else (proc.stderr or proc.stdout))
            return None
        if envelope.get("is_error"):
            self.last_error = _tail(envelope.get("result"))
            return None
        return envelope.get("result", "")


# ═══════════════════════════════════════════════════════════════════════
#  Codex
# ═══════════════════════════════════════════════════════════════════════
class CodexCLI(Provider):
    id = "codex-cli"
    label = "Codex (구독 좌석)"
    billing = BILLING_SUBSCRIPTION
    default_timeout = 120        # exec 는 프로세스를 새로 띄운다
    default_model = ""            # 빈 값이면 codex 설정의 기본 모델
    note = "codex 로그인 계정의 플랜 한도를 쓴다. 토큰당 청구 없음."

    def available(self):
        if shutil.which("codex") is None:
            return False, "codex 명령을 찾을 수 없다"
        return True, ""

    def _mode_args(self):
        """codex exec 뒤에 붙는 모드 인자. 로컬 모델 판이 덮어쓴다."""
        return []

    @staticmethod
    def parse_catalog(*sources):
        """모델 목록 JSON 들을 합친다. 목록에 보이는 것(visibility=list)만,
        우선순위 순으로. 같은 이름은 우선순위가 높은 쪽을 쓴다."""
        best = {}
        for src in sources:
            models = src.get("models") if isinstance(src, dict) else src
            for m in models or []:
                if not isinstance(m, dict) or not m.get("slug"):
                    continue
                if m.get("visibility", "list") != "list":
                    continue
                prio = m.get("priority")
                prio = prio if isinstance(prio, (int, float)) else 999
                old = best.get(m["slug"])
                if old is None or prio < old[0]:
                    best[m["slug"]] = (prio, m.get("display_name") or m["slug"],
                                       m.get("description") or "")
        return [(slug, name, desc) for slug, (_p, name, desc) in
                sorted(best.items(), key=lambda kv: (kv[1][0], kv[0]))]

    def catalog(self):
        """이 codex 가 아는 목록(`codex debug models`) + 데스크톱 앱이
        서버에서 받아 둔 최신 목록(~/.codex/models_cache.json).

        둘이 다를 수 있다 — 데스크톱 앱이 CLI 보다 새 판이면 캐시 쪽에 더 새
        모델이 있다. 둘 다 보여 주고, 이 CLI 로 실제로 되는지는 연결 시험이
        가린다.
        """
        sources = []
        try:
            proc = subprocess.run(
                ["codex", "debug", "models"], capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=20, env=_game_env())
            sources.append(json.loads(proc.stdout))
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        cache = _codex_home() / "models_cache.json"
        try:
            sources.append(json.loads(cache.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return self.parse_catalog(*sources)

    def complete(self, system, user, *, timeout=None, schema=None):
        """codex exec 로 한 턴.

        codex 에는 --system-prompt 가 없다. 대신 지시문을 프롬프트 앞에
        붙이고, --output-schema 로 JSON 모양을 강제한다 — 프롬프트로
        비는 것보다 이쪽이 확실하다. 스키마는 호출마다 다르다(대사 /
        기억 압축) — 하나로 박아 두면 압축이 캐릭터 응답 모양으로 묶여
        facts 를 낼 수 없었다.

        마지막 메시지는 -o 로 파일에 받는다. --json 의 이벤트 스트림에서
        골라내는 것보다 튼튼하다.
        """
        tmpdir = tempfile.mkdtemp(prefix="nerv-codex-")
        schema_path = os.path.join(tmpdir, "schema.json")
        out_path = os.path.join(tmpdir, "last.txt")
        self.calls = 1
        try:
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(schema or RESPONSE_SCHEMA, f, ensure_ascii=False)

            cmd = ["codex", "exec", *self._mode_args(),
                   "--sandbox", "read-only",
                   "--skip-git-repo-check",
                   "--ephemeral",
                   # 사용자의 config.toml 을 물려받지 않는다. 인증은
                   # CODEX_HOME 에서 그대로 읽으므로 로그인은 유지된다.
                   #
                   # 이게 없으면 사용자가 데스크톱 전용 모델을 기본으로
                   # 잡아 둔 경우 exec 이 400 으로 죽는다. 실제로 여기서
                   # gpt-5.6-sol 이 "not supported when using Codex with a
                   # ChatGPT account" 로 거절됐다. 남의 notify·MCP 설정을
                   # 끌고 들어오지 않는 이점도 있다.
                   "--ignore-user-config",
                   "--output-schema", schema_path,
                   "-o", out_path,
                   "--color", "never"]
            if self.model:
                cmd += ["-m", self.model]
            cmd.append(f"{flatten(system)}\n\n---\n\n{user}")

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout or self.timeout, env=_game_env())
            except subprocess.TimeoutExpired:
                self.last_error = f"시간 초과 ({timeout or self.timeout}초)"
                return None
            except OSError as exc:
                self.last_error = str(exc)
                return None
            if proc.returncode != 0:
                self.last_error = _tail(proc.stderr or proc.stdout)
                return None
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return proc.stdout or None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class CodexLocalCLI(CodexCLI):
    """codex 를 로컬 모델(ollama / lmstudio)로 돌린다."""

    id = "codex-oss"
    label = "Codex — 로컬 모델 (--oss)"
    billing = "none"
    default_timeout = 300
    note = "codex 가 ollama 또는 LM Studio 로 돈다. 과금도 플랜 소모도 없다."

    @property
    def local_provider(self) -> str:
        return (self.cfg.get("local_provider") or "ollama").strip()

    def _mode_args(self):
        return ["--oss", "--local-provider", self.local_provider]

    def catalog(self):
        """로컬 서버에 설치된 모델 — ollama 면 태그 목록, LM Studio 면
        OpenAI 호환 목록."""
        if self.local_provider == "lmstudio":
            from .http import get_json
            got = get_json("http://localhost:1234/v1/models")
            return [(m["id"], m["id"], "LM Studio")
                    for m in (got or {}).get("data") or []
                    if isinstance(m, dict) and m.get("id")]
        from .http import Ollama
        return Ollama({}).catalog()
