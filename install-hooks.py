#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""에이전트 설정에 EVA 훅을 병합한다.

기존 훅(ccsidekick, peon-ping 등)은 절대 건드리지 않고 옆에 추가만 한다.

  python3 install-hooks.py                      Claude Code 에 설치
  python3 install-hooks.py --agent codex        Codex 에 설치
  python3 install-hooks.py --agent all          둘 다
  sudo python3 install-hooks.py --global        서버 전체 (Claude 만)

  --uninstall / --dry-run 은 모든 모드에서 동작한다.

두 에이전트의 훅 스키마가 같아서 설치 코드도 하나다. Codex 가
Claude 훅 형식을 그대로 읽는다 — 이벤트 이름도 PascalCase 로 같다.
들어가는 파일만 다르다:

    Claude   ~/.claude/settings.json      의 "hooks" 키
    Codex    ~/.codex/hooks.json          파일 전체
"""
import argparse
import datetime
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from nervterm import agents                                   # noqa: E402
from nervterm.settings import write_json_atomic               # noqa: E402

# 훅 command 는 셸로 실행된다 — 경로에 공백이 있으면 인용 없이는
# 설치가 성공한 것처럼 보이고 훅만 조용히 안 돈다.
CMD = shlex.quote(str(ROOT / "eva")) + " hook"
MANAGED = Path("/etc/claude-code/managed-settings.json")

# 앵커 필수 — 없으면 mcp__foo__Read 같은 MCP 도구 이름에도 걸린다.
# apply_patch 는 Codex 의 파일 수정이다(Codex 는 Edit/Write 를 별칭으로도
# 받지만, 정식 이름을 적어 두는 게 확실하다).
TOOL_MATCHER = ("^(Bash|Edit|Write|NotebookEdit|apply_patch|Read|Grep|Glob|"
                "WebFetch|WebSearch|Agent|Task|TaskCreate|TaskUpdate|Skill|"
                "TodoWrite)$")


def wanted_for(agent):
    """이 에이전트에 넣을 {이벤트: matcher}. matcher 가 None 이면 전체."""
    out = {}
    for event in agent.events:
        out[event] = TOOL_MATCHER if event in agent.tool_events else None
    return out


def load(target: Path) -> dict:
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        sys.exit(f"{target} 을 읽을 수 없습니다: {exc}")
    if not isinstance(raw, dict):
        sys.exit(f"{target} 이 JSON 객체가 아닙니다 — 손대지 않았습니다.")
    return raw


def merge(cfg: dict, agent, *, remove=False):
    """훅을 병합한다. 바뀐 내용의 설명 목록을 돌려준다."""
    hooks = cfg.setdefault("hooks", {})
    changed = []
    # 우리 훅은 **모든** 이벤트에서 먼저 걷어낸다. wanted 이벤트만 돌면
    # 목록에서 빠진 이벤트(예: 폐기된 PostToolUseFailure)에 남은 잔존
    # 등록을 재설치로도 영원히 못 지운다.
    for event in list(hooks.keys()):
        arr = hooks.get(event)
        if not isinstance(arr, list):
            continue
        before = len(arr)
        arr[:] = [e for e in arr if not agents.is_our_hook(e)]
        if before != len(arr):
            changed.append(f"  - {event}: 기존 EVA 훅 제거")
        if not arr:
            hooks.pop(event, None)
    if remove:
        if not hooks:
            cfg.pop("hooks", None)
        return changed
    for event, matcher in wanted_for(agent).items():
        entry = {"hooks": [{"type": "command", "command": CMD,
                            "timeout": agent.hook_timeout(event)}]}
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
        changed.append(f"  + {event}: EVA 훅 추가"
                       + (f" (matcher: {matcher[:30]}…)" if matcher else ""))
    return changed


def survey(cfg: dict):
    """보존될 남의 훅 목록."""
    kept = []
    for event, arr in (cfg.get("hooks") or {}).items():
        if not isinstance(arr, list):
            continue
        for e in arr:
            if agents.is_our_hook(e):
                continue
            for h in (e or {}).get("hooks", []):
                kept.append(f"  · {event}: {str(h.get('command'))[:70]}")
    return kept


def backup(target: Path) -> None:
    if not target.exists():
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = target.with_suffix(f"{target.suffix}.eva-{stamp}.bak")
    shutil.copy2(target, bak)
    print(f"\n백업: {bak}")


def save(target: Path, cfg: dict, args) -> None:
    # 원자적 쓰기(tempfile + os.replace) — 디스크 풀·중단으로 사용자의
    # 에이전트 설정이 잘린 채 남지 않게. 권한은 기존 파일 것을 보존한다.
    write_json_atomic(target, cfg,
                      mode=0o644 if args.managed else None)
    print(f"저장: {target}")


def apply_to(target: Path, agent, args) -> None:
    print(f"\n═══ {agent.label}  →  {target}")
    if args.uninstall and not target.exists():
        print("  (설치돼 있지 않다 — 건너뛴다)")
        return
    cfg = load(target)
    kept = survey(cfg)
    changed = merge(cfg, agent, remove=args.uninstall)
    # 상태줄도 같은 파일이면 여기서 한 번에 — load/save 사이클을 두 번
    # 돌면 그 틈에 Claude Code 본체가 쓴 변경(모델·permissions)이
    # 유실되고, 백업도 실행마다 2개씩 쌓인다.
    if (agent.id == "claude" and not args.managed
            and (args.statusline or args.uninstall)):
        changed += merge_statusline(cfg, args)

    print("기존 훅 (그대로 보존됨):")
    print("\n".join(kept) if kept else "  (없음)")
    print("\n변경:")
    print("\n".join(changed) if changed else "  (없음)")

    if args.dry_run:
        print("\n--dry-run 이므로 저장하지 않았습니다.")
        return

    backup(target)
    save(target, cfg, args)

    if not args.uninstall:
        if agent.id == "codex":
            # Codex 는 새 훅을 신뢰할지 물어본다. 모르면 훅이 조용히
            # 안 도는 것처럼 보이므로 미리 알려 준다.
            print("\n  Codex 는 다음 실행 때 이 훅을 신뢰할지 묻습니다.")
            print("  승인해야 재화가 적립됩니다.")
        print(f"\n{agent.label} 의 새 세션부터 적용됩니다.")


WIDGET_CMD = (shlex.quote(sys.executable) + " "
              + shlex.quote(str(ROOT / "nervterm" / "widget.py")))


def is_our_statusline(cfg) -> bool:
    got = (cfg.get("statusLine") or {}).get("command", "")
    return "nervterm/widget.py" in str(got) or "nervterm.widget" in str(got)


def merge_statusline(cfg: dict, args) -> list:
    """cfg 에 상태줄 변경을 반영한다. 바뀐 내용 설명 목록을 돌려준다.

    남의 상태줄이 이미 있으면 덮지 않는다. 상태줄은 하나뿐이라
    덮으면 그 사람이 쓰던 게 사라진다.
    """
    existing = cfg.get("statusLine")
    if args.uninstall:
        if not existing:
            return []
        if not is_our_statusline(cfg):
            return ["  · 남의 상태줄은 건드리지 않는다: "
                    + str(existing.get("command"))[:60]]
        cfg.pop("statusLine", None)
        return ["  - 상태줄 제거"]
    if existing and not is_our_statusline(cfg):
        return ["  · 이미 다른 상태줄이 있다. 덮지 않는다: "
                + str(existing.get("command"))[:60]]
    cfg["statusLine"] = {
        "type": "command",
        "command": WIDGET_CMD,
        "padding": 0,
    }
    return [f"  + 상태줄 추가: {WIDGET_CMD}"]


def apply_statusline(args) -> None:
    """상태줄만 단독으로 적용한다(--only-statusline, codex 단독 설치)."""
    target = Path.home() / ".claude" / "settings.json"
    print(f"\n═══ 상태줄 위젯  →  {target}")
    cfg = load(target)
    changed = merge_statusline(cfg, args)
    print("\n".join(changed) if changed else "  (바꿀 것이 없다)")
    if not changed:
        return
    if args.dry_run:
        print("\n  --dry-run 이므로 저장하지 않았습니다.")
        return
    backup(target)
    save(target, cfg, args)
    if not args.uninstall:
        print("\n  새 Claude Code 세션부터 하단에 뜬다.")
        print("  한 번은 eva 를 켜서 상대를 골라야 표시할 것이 생긴다.")


def enable_in_settings(agent_ids, on=True) -> None:
    """게임 설정에도 켜 준다 — 훅만 깔고 세션을 안 읽으면 반쪽이다."""
    try:
        from nervterm import settings
        for aid in agent_ids:
            settings.put(f"agents.{aid}", on)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  (설정 갱신 실패: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="claude",
                    choices=["claude", "codex", "all"],
                    help="어느 에이전트에 설치할지 (기본: claude)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--global", dest="managed", action="store_true",
                    help="모든 사용자에게 적용 (Claude 만, root 필요)")
    ap.add_argument("--statusline", action="store_true",
                    help="Claude Code 터미널 하단에 상태줄 위젯도 붙인다")
    ap.add_argument("--only-statusline", action="store_true",
                    help="상태줄만 설치하고 훅은 건드리지 않는다")
    args = ap.parse_args()

    if args.managed and args.agent != "claude":
        sys.exit("--global 은 Claude 에만 쓸 수 있습니다.")
    if args.managed and not args.dry_run and os.geteuid() != 0:
        sys.exit("--global 은 root 권한이 필요합니다:  "
                 "sudo python3 install-hooks.py --global")

    if args.only_statusline:
        apply_statusline(args)
        return 0

    picked = (["claude", "codex"] if args.agent == "all" else [args.agent])
    for aid in picked:
        agent = agents.get(aid)
        target = MANAGED if args.managed else agent.hook_path()
        apply_to(target, agent, args)

    folded = "claude" in picked and not args.managed
    if (args.statusline or args.uninstall) and not folded:
        apply_statusline(args)

    if not args.dry_run:
        enable_in_settings(picked, on=not args.uninstall)
        print()
        print("게임 설정의 '재화를 적립할 에이전트' 도 함께 "
              + ("껐습니다." if args.uninstall else "켰습니다."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
