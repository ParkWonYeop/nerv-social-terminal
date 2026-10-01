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
import datetime as _dt
import json
import os
import re
from pathlib import Path

# 우리 훅인지 판별. 'rei' 와 'hook' 이 부분 문자열로 함께 있다는 것만으로
# 판정하면 남의 훅(예: reindex-hook.sh)까지 지워 버린다.
# 경로에 공백이 있으면 설치기가 shlex.quote 로 감싼다 — 닫는 따옴표 허용.
_OURS = re.compile(
    r"(?:^|[/\s])(?:eva|nervterm|rei)(?:\.py)?['\"]?\s+hook(?:\s|$)")


def is_our_hook(entry) -> bool:
    for h in (entry or {}).get("hooks", []):
        if _OURS.search(str(h.get("command", ""))):
            return True
    return False


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def local_time(ts: str):
    """에이전트 기록의 시각 → 이 기계의 (날짜, 시각 iso).

    세션 기록의 timestamp 는 UTC 다("…Z"). 앞 10글자를 날짜로 잘라 쓰면
    한국(UTC+9)에서는 오전 9시 전에 한 일이 전부 '어제' 로 들어간다 —
    새벽 근무를 지적하는 캐릭터가 정작 무슨 일을 했는지는 못 보는 모순이
    생겼다. 게임의 '오늘'(db.today) 은 로컬 날짜이므로 여기서 맞춘다.
    """
    from . import db
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        now = db.now()
        return now[:10], now
    if t.tzinfo is not None:
        t = t.astimezone().replace(tzinfo=None)
    iso = t.isoformat(timespec="seconds")
    return iso[:10], iso


def project_name(label: str) -> str:
    """'nerv-social-terminal (main)' → 'nerv-social-terminal'"""
    return (label or "").split(" (", 1)[0].strip()


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


# harvest() 가 이걸 kind 로 내면 그 세션 파일은 사람이 한 일이 아니다 —
# work.py 가 파일째 건너뛰고 다시 읽지 않는다(work_scan.skip).
SKIP_FILE = "__skip__"


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

    # 이벤트별 훅 타임아웃(초). 기본은 10.
    hook_timeouts = {}
    default_hook_timeout = 10

    def hook_path(self) -> Path:
        raise NotImplementedError

    def hook_timeout(self, event: str) -> int:
        return self.hook_timeouts.get(event, self.default_hook_timeout)

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

    def session_id(self, path) -> str:
        """세션 파일 → 세션 id. 제목과 프롬프트를 이어 붙이는 열쇠."""
        return path.stem

    # 훅이 있는 에이전트인가. 없으면(로컬) 설치기가 건너뛴다.
    needs_hook = True
    # 처음 보는 기록 파일을 처음부터 읽지 않고 끝에서 시작하는가 —
    # 시각이 안 적힌 기록이면 지난 것을 다 '오늘' 로 읽게 된다.
    start_at_end = False

    def decode(self, line: str):
        """기록 한 줄 → 레코드. 못 읽으면 None."""
        try:
            return json.loads(line)
        except ValueError:
            return None

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
    # PostToolUse 는 **성공한** 도구 호출에만 발동한다. 실패는
    # PostToolUseFailure 로 따로 온다(공식 문서: "After a tool call fails").
    # 한때 '없는 이벤트' 로 잘못 알고 등록을 걷어냈다 — 그 뒤로 연속 실패
    # 감점·실패 한 마디·'실패 끝에 통과' 사건이 사실상 죽어 있었다.
    # Notification — 에이전트가 사람을 기다릴 때. 상태줄 한 마디만 건다.
    events = ("PostToolUse", "PostToolUseFailure", "Stop", "SessionStart",
              "SessionEnd", "Notification")
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
        day, ts = local_time(rec.get("timestamp", ""))

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
    형식은 Claude 와 전혀 다르다. 그리고 **판마다 바뀐다** — 둘 다 읽는다.

    옛 판 (~0.1xx 초)
        event_msg:user_message        사람이 친 프롬프트
        event_msg:patch_apply_end     적용된 파일 수정 (changes 에 경로)
        function_call exec_command    셸 명령 (arguments 안에 JSON)
        custom_tool_call apply_patch  파일 수정 (*** Update File: 경로)
        function_call update_plan     작업 단계 — Claude 의 description 자리

    지금 판 (0.153 에서 확인)
        event_msg:item_completed 의 item 하나하나:
          UserMessage        사람이 친 프롬프트 (content[].text)
          FileChange         적용된 파일 수정 (changes 의 키가 경로)
          CommandExecution   셸 명령 (command 는 argv 목록)
        옛 판의 user_message·patch_apply_end·update_plan 은 더 안 나온다.
        이걸 몰라서 0.153 이후의 Codex 작업은 근무 일지에 하나도 안 잡혔다.

    공통
        session_meta / turn_context   작업 디렉터리
        custom_tool_call exec         셸 명령 (input 안에 JS)

    **승인 검토 스레드는 사람이 한 일이 아니다.** 자동 검토(auto_review)를
    켜면 승인 요청마다 guardian 서브 에이전트가 세션을 하나씩 만든다 —
    실제 기록에서 세션 파일의 절반 이상이 이것이었다. session_meta 에
    parent_thread_id 가 있거나 thread_source 가 user 가 아니면 파일째
    건너뛴다(Claude 의 isSidechain 과 같은 자리).

    제목은 ~/.codex/session_index.jsonl 의 thread_name 이다 — Claude 의
    ai-title 자리. 세션 id(파일 이름 끝의 UUID)로 프롬프트와 이어진다.
    """

    id = "codex"
    label = "Codex"
    install_hint = "python3 install-hooks.py --agent codex"
    # PermissionRequest — 에이전트가 사람의 승인을 기다릴 때. 상태줄 한
    # 마디만 건다. 아무것도 출력하지 않으므로 Codex 는 평소 승인 흐름대로
    # 간다("If no matching hook decides, Codex uses the normal approval flow").
    events = ("PostToolUse", "Stop", "SessionStart", "SessionEnd",
              "PermissionRequest")
    tool_events = ("PostToolUse",)
    # Codex 는 종료 훅을 3초로 잘라 버리고, 더 큰 값을 적어 두면
    # 실행할 때마다 "clamping SessionEnd hook timeout" 경고를 찍는다.
    # 어차피 잘릴 값이라면 처음부터 3 으로 적어 경고를 없앤다.
    hook_timeouts = {"SessionEnd": 3}

    def hook_path(self) -> Path:
        return self.home() / "hooks.json"

    def home(self) -> Path:
        override = os.environ.get("CODEX_HOME")
        return Path(override).expanduser() if override else (
            Path.home() / ".codex")

    def sessions_dir(self) -> Path:
        return self.home() / "sessions"

    _SID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{12})$")

    def session_files(self):
        root = self.sessions_dir()
        out = []
        if root.is_dir():
            out = list(root.glob("**/rollout-*.jsonl"))
        index = self.home() / "session_index.jsonl"
        if index.is_file():
            out.append(index)            # 스레드 제목
        return self.newest_first(out)

    def session_id(self, path) -> str:
        """rollout-2026-10-01T19-02-52-<UUID>.jsonl → <UUID>

        session_index.jsonl 의 id 가 이 UUID 다. 파일 이름 통째로 쓰면
        제목과 프롬프트가 이어지지 않는다.
        """
        m = self._SID.search(path.stem)
        return m.group(1) if m else path.stem

    @staticmethod
    def _argv_command(cmd) -> str:
        """CommandExecution 의 command(argv 목록) → 셸 명령 문자열.

        ["/bin/zsh", "-lc", "git commit -m '…'"] 처럼 셸에 넘긴 것이면 그
        문자열이 실제 명령이다.
        """
        if isinstance(cmd, str):
            return cmd
        if not isinstance(cmd, list):
            return ""
        parts = [str(x) for x in cmd]
        if len(parts) >= 3 and parts[-2] in ("-c", "-lc", "-ic"):
            return parts[-1]
        return " ".join(parts)

    @staticmethod
    def _subagent(meta: dict) -> bool:
        """사람이 연 스레드가 아닌가 — 승인 검토(guardian) 같은 것."""
        if meta.get("parent_thread_id"):
            return True
        source = meta.get("thread_source")
        if source and source != "user":
            return True
        src = meta.get("source")
        return isinstance(src, dict) and "subagent" in src

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
        out = []
        t = rec.get("type")
        payload = rec.get("payload")

        if t is None and "thread_name" in rec and rec.get("id"):
            # session_index.jsonl 한 줄 — 스레드 제목. 날짜는 비워 둔다:
            # 그 세션에 사람이 친 프롬프트가 있는 날로 귀속된다(work.py).
            from . import db
            name = _clean(str(rec.get("thread_name") or ""))
            if name:
                out.append(("", db.now(), "title", name[:120], str(rec["id"])))
            return out

        if not isinstance(payload, dict):
            return out
        ptype = payload.get("type", "")
        day, ts = local_time(rec.get("timestamp", ""))
        sid = sid_fallback

        if t == "session_meta":
            if self._subagent(payload):
                return [(day, ts, SKIP_FILE, "", sid)]
            cwd = payload.get("cwd") or ""
            if cwd:
                out.append((day, ts, "project", os.path.basename(cwd), sid))
            return out

        if t == "event_msg" and ptype == "item_completed":
            return self._harvest_item(payload.get("item") or {}, day, ts,
                                      sid)

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


    def _harvest_item(self, item, day, ts, sid):
        """지금 판의 item_completed 항목 하나."""
        out = []
        kind = item.get("type") if isinstance(item, dict) else ""
        if kind == "UserMessage":
            text = " ".join(
                str(c.get("text") or "") for c in (item.get("content") or [])
                if isinstance(c, dict) and c.get("type") == "text")
            msg = _clean(text)
            if msg and not self._injected(msg):
                out.append((day, ts, "prompt", msg[:160], sid))
        elif kind == "FileChange":
            if item.get("status") not in (None, "completed"):
                return out
            for path in (item.get("changes") or {}):
                out.append((day, ts, "file",
                            os.path.basename(str(path).strip()), sid))
        elif kind == "CommandExecution":
            if item.get("status") not in (None, "completed"):
                return out
            msg = commit_message(self._argv_command(item.get("command")))
            if msg:
                out.append((day, ts, "commit", msg, sid))
        return out


# ═══════════════════════════════════════════════════════════════════════
#  로컬 에이전트
# ═══════════════════════════════════════════════════════════════════════
class LocalAgent(Agent):
    """Ollama 로 도는 것들, 그리고 훅이 없는 모든 에이전트.

    보상은 훅이 아니라 git 커밋으로 판정한다(local.py) — 무엇으로 일했든
    커밋은 남는다. 대화는 Ollama 가 남기는 것만 읽는다: `ollama run` 에서
    친 말이 ~/.ollama/history 에 한 줄씩 쌓인다(시각은 없다). 게임이
    대사를 만들 때 쓰는 ollama API 호출은 여기 남지 않는다.
    """

    id = "local"
    label = "로컬 에이전트 (Ollama 등)"
    install_hint = "훅이 필요 없다 — 작업 폴더의 git 커밋으로 판정한다"
    needs_hook = False
    start_at_end = True
    events = ()
    tool_events = ()

    def hook_path(self):
        return None

    def hook_installed(self) -> bool:
        return True

    def ollama_home(self) -> Path:
        override = os.environ.get("OLLAMA_HOME")
        return Path(override).expanduser() if override else (
            Path.home() / ".ollama")

    def session_files(self):
        hist = self.ollama_home() / "history"
        return [hist] if hist.is_file() else []

    def session_id(self, path) -> str:
        return "ollama"

    def decode(self, line: str):
        text = line.strip()
        return {"_text": text} if text else None

    def harvest(self, rec, sid_fallback=""):
        from . import db
        text = _clean(str(rec.get("_text") or ""))
        if not text or text.startswith("/"):     # /bye, /set 같은 REPL 명령
            return []
        return [(db.today(), db.now(), "prompt", text[:160], "ollama")]


AGENTS = [ClaudeAgent(), CodexAgent(), LocalAgent()]
BY_ID = {a.id: a for a in AGENTS}


def enabled():
    """설정에서 켜 둔 에이전트들."""
    from . import settings
    table = settings.get("agents", {}) or {}
    return [a for a in AGENTS if table.get(a.id)]


def get(agent_id: str):
    return BY_ID.get(agent_id)
