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
from .base import BILLING_SUBSCRIPTION, Provider


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

    def complete(self, system, user, *, timeout=None):
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
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        try:
            envelope = json.loads(proc.stdout)
        except Exception:
            return None
        if envelope.get("is_error"):
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

    def complete(self, system, user, *, timeout=None):
        """codex exec 로 한 턴.

        codex 에는 --system-prompt 가 없다. 대신 지시문을 프롬프트 앞에
        붙이고, --output-schema 로 JSON 모양을 강제한다 — 프롬프트로
        비는 것보다 이쪽이 확실하다.

        마지막 메시지는 -o 로 파일에 받는다. --json 의 이벤트 스트림에서
        골라내는 것보다 튼튼하다.
        """
        from .base import RESPONSE_SCHEMA

        tmpdir = tempfile.mkdtemp(prefix="nerv-codex-")
        schema_path = os.path.join(tmpdir, "schema.json")
        out_path = os.path.join(tmpdir, "last.txt")
        try:
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(RESPONSE_SCHEMA, f, ensure_ascii=False)

            cmd = ["codex", "exec",
                   "--sandbox", "read-only",
                   "--skip-git-repo-check",
                   "--ephemeral",
                   "--output-schema", schema_path,
                   "-o", out_path,
                   "--color", "never"]
            if self.model:
                cmd += ["-m", self.model]
            cmd.append(f"{system}\n\n---\n\n{user}")

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout or self.timeout, env=_game_env())
            except (subprocess.TimeoutExpired, OSError):
                return None
            if proc.returncode != 0:
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

    def complete(self, system, user, *, timeout=None):
        from .base import RESPONSE_SCHEMA

        tmpdir = tempfile.mkdtemp(prefix="nerv-codex-")
        schema_path = os.path.join(tmpdir, "schema.json")
        out_path = os.path.join(tmpdir, "last.txt")
        try:
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(RESPONSE_SCHEMA, f, ensure_ascii=False)
            cmd = ["codex", "exec", "--oss",
                   "--local-provider", self.local_provider,
                   "--sandbox", "read-only",
                   "--skip-git-repo-check",
                   "--ephemeral",
                   "--output-schema", schema_path,
                   "-o", out_path,
                   "--color", "never"]
            if self.model:
                cmd += ["-m", self.model]
            cmd.append(f"{system}\n\n---\n\n{user}")
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout or self.timeout, env=_game_env())
            except (subprocess.TimeoutExpired, OSError):
                return None
            if proc.returncode != 0:
                return None
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return proc.stdout or None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
