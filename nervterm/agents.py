# -*- coding: utf-8 -*-
"""재화를 적립할 에이전트들 — Claude Code / Codex.

훅이 설치된 에이전트라면 어느 것이든 작업량이 재화가 된다.
여기에는 두 가지가 모인다:

    1. 훅을 어디에 설치하는가        install-hooks.py 가 쓴다
    2. 세션 기록을 어떻게 읽는가     work.py 가 쓴다

둘을 한 파일에 둔 이유: 새 에이전트를 붙일 때 고쳐야 할 곳이
여기 하나뿐이어야 한다.

**훅 페이로드는 양쪽이 같은 모양이다.** Codex 가 Claude 훅 스키마를
그대로 쓰기 때문에(내부적으로 ClaudeHooksEngine 을 돌린다) hook.py 는
누가 불렀는지 몰라도 된다.
"""
import json
import os
import re
from pathlib import Path

# 우리 훅인지 판별. 'rei' 와 'hook' 이 부분 문자열로 함께 있다는 것만으로
# 판정하면 남의 훅(예: reindex-hook.sh)까지 지워 버린다.
_OURS = re.compile(r"(?:^|[/\s])(?:eva|nervterm|rei)(?:\.py)?\s+hook(?:\s|$)")


def is_our_hook(entry) -> bool:
    for h in (entry or {}).get("hooks", []):
        if _OURS.search(str(h.get("command", ""))):
            return True
    return False


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


COMMIT_RE = re.compile(
    r"""git\s+(?:-\S+\s+)*commit\b[^\n]*?-m\s*(['"])(.+?)\1""", re.S)


def commit_message(cmd: str) -> str:
    """명령에서 커밋 메시지를 꺼낸다. 못 꺼내면 빈 문자열.

    `git commit -m "$(cat <<'EOF' … EOF)"` 처럼 셸이 만들어 넣는 형태는
    버린다. 정규식이 잡는 건 메시지가 아니라 그걸 만드는 명령이라,
    그대로 두면 캐릭터가 "$(cat <<'EOF'…" 를 커밋 제목으로 읊는다.
    """
    m = COMMIT_RE.search(cmd or "")
    if not m:
        return ""
    msg = _clean(m.group(2))
    if msg.startswith("$(") or msg.startswith("`") or "cat <<" in msg:
        return ""
    return msg[:120]


class Agent:
    """에이전트 하나."""

    id = ""
    label = ""
    install_hint = ""
    # 훅 설정 파일. Claude 는 settings.json, Codex 는 hooks.json.
    hook_file = None
    # 이 에이전트가 아는 훅 이벤트. 없는 이벤트를 넣으면 경고가 뜬다.
    events = ("PostToolUse", "Stop", "SessionStart", "SessionEnd")
    tool_events = ("PostToolUse",)

    def hook_path(self) -> Path:
        raise NotImplementedError

    def hook_installed(self) -> bool:
        p = self.hook_path()
        if not p.is_file():
            return False
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            return False
        for arr in (cfg.get("hooks") or {}).values():
            if isinstance(arr, list) and any(is_our_hook(e) for e in arr):
                return True
        return False

    def session_files(self):
        return []

    @staticmethod
    def newest_first(paths):
        """최근에 고친 파일부터.

        한 번에 읽는 양에 상한이 있어서 순서가 중요하다. 오래된 것부터
        읽으면 기록이 많이 쌓인 사람은 캐릭터가 '오늘 한 일' 에 닿기까지
        수백 번을 실행해야 한다. 실제로 여기 Codex 기록이 4.2GB 였고,
        한 번에 4MB 씩 읽으니 옛날 것만 천 번 읽을 판이었다.

        최근 것부터 읽으면 오래된 파일은 그냥 안 읽힌 채 남는다.
        그게 맞다 — 반년 전 작업이 지금 대화에 나올 일은 없다.
        """
        def when(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        return sorted(paths, key=when, reverse=True)

    def harvest(self, rec, sid_fallback=""):
        """레코드 한 줄 → [(day, ts, kind, text)]

        kind 는 work.py 가 아는 것들: title / prompt / project / desc /
        file / commit.
        """
        return []


# ═══════════════════════════════════════════════════════════════════════
#  Claude Code
# ═══════════════════════════════════════════════════════════════════════
class ClaudeAgent(Agent):
    id = "claude"
    label = "Claude Code"
    install_hint = "python3 install-hooks.py"
    events = ("PostToolUse", "PostToolUseFailure", "Stop",
              "SessionStart", "SessionEnd")
    tool_events = ("PostToolUse", "PostToolUseFailure")

    def hook_path(self) -> Path:
        return Path.home() / ".claude" / "settings.json"

    def sessions_dir(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def session_files(self):
        root = self.sessions_dir()
        if not root.is_dir():
            return []
        return self.newest_first(root.glob("*/*.jsonl"))

    def harvest(self, rec, sid_fallback=""):
        from . import db

        out = []
        t = rec.get("type")
        sid = rec.get("sessionId") or rec.get("session_id") or sid_fallback
        ts = rec.get("timestamp", "") or db.now()
        day = ts[:10] if len(ts) >= 10 else db.today()

        if t == "ai-title":
            # timestamp 가 없다. day 를 비워 두고 digest 단계에서 세션
            # 날짜로 귀속시킨다. 사람이 직접 타이핑한 프롬프트가 있는
            # 세션의 제목만 나중에 채택된다 — 게임이 스스로 띄운
            # 세션의 제목을 근무 실적으로 오인하지 않기 위해.
            return [("", db.now(), "title", rec.get("aiTitle", ""), sid)]

        if rec.get("isSidechain"):        # 서브에이전트 잡음 제외
            return out

        if t == "user":
            if rec.get("promptSource") != "typed":
                return out
            msg = rec.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                out.append((day, ts, "prompt", _clean(content)[:160], sid))
            cwd = rec.get("cwd", "")
            if cwd:
                branch = rec.get("gitBranch") or ""
                label = os.path.basename(cwd) + (f" ({branch})" if branch
                                                 else "")
                out.append((day, ts, "project", label, sid))
            return out

        if t == "assistant":
            for b in (rec.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name = b.get("name", "")
                inp = b.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                if name in ("Edit", "Write", "NotebookEdit"):
                    fp = inp.get("file_path") or inp.get("notebook_path") or ""
                    if fp:
                        out.append((day, ts, "file",
                                    os.path.basename(str(fp)), sid))
                elif name == "Bash":
                    cmd = str(inp.get("command", ""))
                    desc = _clean(str(inp.get("description", "")))
                    if desc:
                        out.append((day, ts, "desc", desc[:100], sid))
                    msg = commit_message(cmd)
                    if msg:
                        out.append((day, ts, "commit", msg, sid))
        return out


# ═══════════════════════════════════════════════════════════════════════
#  Codex
# ═══════════════════════════════════════════════════════════════════════
class CodexAgent(Agent):
    """Codex CLI / Desktop.

    훅은 ~/.codex/hooks.json 에 들어간다 — config.toml 이 아니라
    별도 JSON 파일이다. 구조는 Claude 의 settings.json 의 hooks 와
    똑같고, 이벤트 이름도 같은 PascalCase 다.

    세션 기록은 ~/.codex/sessions/<연>/<월>/<일>/rollout-*.jsonl 이고
    형식은 Claude 와 전혀 다르다:

        event_msg:user_message        사람이 친 프롬프트
        event_msg:patch_apply_end     적용된 파일 수정 (changes 에 경로)
        session_meta / turn_context   작업 디렉터리
        function_call exec_command    셸 명령 (arguments 안에 JSON)
        custom_tool_call exec         셸 명령 (input 안에 JS)
        custom_tool_call apply_patch  파일 수정 (*** Update File: 경로)
        function_call update_plan     작업 단계 — Claude 의 description 자리

    Codex 에는 ai-title 같은 자동 제목이 없다. 제목을 억지로 지어내는
    대신 안 만든다 — work.py 의 요약이 제목이 없으면 프롬프트로
    대신하게 돼 있고, 그게 더 정직하다.
    """

    id = "codex"
    label = "Codex"
    install_hint = "python3 install-hooks.py --agent codex"
    events = ("PostToolUse", "Stop", "SessionStart", "SessionEnd")
    tool_events = ("PostToolUse",)

    def hook_path(self) -> Path:
        return self.home() / "hooks.json"

    def home(self) -> Path:
        override = os.environ.get("CODEX_HOME")
        return Path(override).expanduser() if override else (
            Path.home() / ".codex")

    def sessions_dir(self) -> Path:
        return self.home() / "sessions"

    def session_files(self):
        root = self.sessions_dir()
        if not root.is_dir():
            return []
        return self.newest_first(root.glob("**/rollout-*.jsonl"))

    # ── 명령 문자열 뽑기 ───────────────────────────────────────────────
    #
    # 값 안에 이스케이프된 따옴표가 들어 있다 — git commit -m \"메시지\".
    # (.+?) 로 비탐욕 매칭하면 그 이스케이프에서 끊겨 커밋을 놓친다.
    # 그래서 이스케이프 쌍을 통째로 삼키고, 뒤에서 JSON 으로 푼다.
    _JS_CMD = re.compile(
        r"""["']cmd["']\s*:\s*"((?:[^"\\]|\\.)*)\"""", re.S)
    _PATCH_FILE = re.compile(
        r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", re.M)

    # 사람이 친 것이 아니라 하네스가 밀어 넣은 user_message 들.
    #
    # Claude 쪽에는 promptSource:"typed" 라는 표시가 있어서 한 줄로
    # 걸러진다. Codex 에는 그런 표시가 없다 — client_id 가 붙는 것도
    # 있지만 사람이 친 것에도 없는 경우가 훨씬 많아서 쓸 수 없다.
    # 그래서 실제 기록을 훑어 나온 앞머리로 거른다.
    #
    # 이걸 안 걸러내면 승인 판정용으로 주입되는
    # "The following is the Codex agent history…" 가 근무 실적이 된다.
    # 실제 저장소에서 이게 전체 user_message 의 대부분이었다.
    _INJECTED = (
        "the following is the codex agent history",
        "<heartbeat>",
        "<app-context>",
        "<environment_context>",
        "<user_instructions>",
        "# in app browser:",
    )

    @classmethod
    def _injected(cls, msg: str) -> bool:
        low = msg.lstrip().lower()
        return any(low.startswith(mark) for mark in cls._INJECTED)

    @staticmethod
    def _unescape(raw: str) -> str:
        try:
            return json.loads(f'"{raw}"')
        except Exception:                                     # noqa: BLE001
            return raw.replace('\\"', '"').replace("\\\\", "\\")

    def _command_of(self, payload) -> str:
        """도구 호출에서 실제 셸 명령을 꺼낸다."""
        name = payload.get("name", "")
        if name == "exec_command":
            try:
                args = json.loads(payload.get("arguments") or "{}")
            except Exception:                                 # noqa: BLE001
                return ""
            return str(args.get("cmd", "")) if isinstance(args, dict) else ""
        if name == "exec":
            # input 은 tools.exec_command({...}) 를 부르는 JS 코드다.
            m = self._JS_CMD.search(str(payload.get("input") or ""))
            return self._unescape(m.group(1)) if m else ""
        return ""

    def harvest(self, rec, sid_fallback=""):
        from . import db

        out = []
        t = rec.get("type")
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            return out
        ptype = payload.get("type", "")
        ts = rec.get("timestamp", "") or db.now()
        day = ts[:10] if len(ts) >= 10 else db.today()
        sid = sid_fallback

        if t == "session_meta":
            sid = payload.get("session_id") or sid_fallback
            cwd = payload.get("cwd") or ""
            if cwd:
                out.append((day, ts, "project", os.path.basename(cwd), sid))
            return out

        if t == "turn_context":
            cwd = payload.get("cwd") or ""
            if cwd:
                out.append((day, ts, "project", os.path.basename(cwd), sid))
            return out

        if t == "event_msg" and ptype == "user_message":
            msg = _clean(str(payload.get("message") or ""))
            if msg and not self._injected(msg):
                out.append((day, ts, "prompt", msg[:160], sid))
            return out

        if t == "event_msg" and ptype == "patch_apply_end":
            # 실제로 적용된 파일 수정. apply_patch 호출보다 이쪽이 정확하다 —
            # 성공 여부까지 들어 있다.
            if not payload.get("success"):
                return out
            for path in (payload.get("changes") or {}):
                out.append((day, ts, "file",
                            os.path.basename(str(path).strip()), sid))
            return out

        if t != "response_item":
            return out

        if ptype in ("function_call", "custom_tool_call"):
            name = payload.get("name", "")

            if name == "apply_patch":
                for path in self._PATCH_FILE.findall(
                        str(payload.get("input") or "")):
                    out.append((day, ts, "file",
                                os.path.basename(path.strip()), sid))
                return out

            if name == "update_plan":
                # 계획의 각 단계가 Claude 의 Bash description 자리를 대신한다.
                try:
                    args = json.loads(payload.get("arguments") or "{}")
                except Exception:                             # noqa: BLE001
                    return out
                for step in (args.get("plan") or [])[:6]:
                    text = _clean(str((step or {}).get("step", "")))
                    if text:
                        out.append((day, ts, "desc", text[:100], sid))
                return out

            msg = commit_message(self._command_of(payload))
            if msg:
                out.append((day, ts, "commit", msg, sid))
        return out


AGENTS = [ClaudeAgent(), CodexAgent()]
BY_ID = {a.id: a for a in AGENTS}


def enabled():
    """설정에서 켜 둔 에이전트들."""
    from . import settings
    table = settings.get("agents", {}) or {}
    return [a for a in AGENTS if table.get(a.id)]


def get(agent_id: str):
    return BY_ID.get(agent_id)
