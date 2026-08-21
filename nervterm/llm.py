# -*- coding: utf-8 -*-
"""claude CLI 헤드리스 래퍼.

팀 플랜 OAuth 좌석을 그대로 사용한다(별도 API 키 없음).
대신 플랜 사용량을 아끼려고: 시스템 프롬프트/툴 정의를 최대한 벗기고,
하루 호출 상한을 두고, 실패하면 조용히 폴백한다.
"""
import json
import os
import re
import shutil
import subprocess

from . import config, db

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def _ensure_lean_settings() -> str:
    """훅·상태줄이 꺼진 설정 파일. 게임의 LLM 호출이 훅을 재발동시키지 않게."""
    p = config.lean_settings_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"hooks": {}, "statusLine": {"type": "command",
                                                             "command": "true"}}),
                     encoding="utf-8")
    return str(p)


def available() -> bool:
    return shutil.which("claude") is not None


def budget_left(con) -> int:
    row = db.daily_row(con)
    return max(0, config.DAILY_LLM_CALLS - (row["llm"] or 0))


def _extract_json(text: str):
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


def ask(con, system: str, user: str, *, offline: bool = False,
        timeout: int = None):
    """레이에게 한 턴 묻는다.

    성공하면 dict, 못 쓰면 None(→ 호출부가 폴백 대사 사용).
    """
    if offline or not available():
        return None
    if budget_left(con) <= 0:
        return None

    cmd = [
        "claude", "-p",
        "--model", config.MODEL,
        "--effort", config.EFFORT,
        "--output-format", "json",
        "--settings", _ensure_lean_settings(),
        "--setting-sources", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--disallowed-tools", *config.LLM_DISALLOWED.split(),
        "--max-turns", "1",
        "--system-prompt", system,
        user,
    ]
    env = dict(os.environ)
    env["REI_GAME"] = "1"          # 자기 훅 재귀 차단
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=timeout or config.LLM_TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    db.daily_bump(con, "llm", 1)
    con.commit()

    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads(proc.stdout)
    except Exception:
        return None
    if envelope.get("is_error"):
        return None
    return _extract_json(envelope.get("result", ""))


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
