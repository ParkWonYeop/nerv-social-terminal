#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code 설정에 EVA 훅을 병합한다.

기존 훅(ccsidekick 등)은 절대 건드리지 않고 옆에 추가만 한다.
  python3 install-hooks.py                  내 계정에 설치 (~/.claude/settings.json)
  sudo python3 install-hooks.py --global    서버 전체에 설치
                                            (/etc/claude-code/managed-settings.json)
  --uninstall / --dry-run 은 두 모드 모두에서 동작한다.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
MANAGED = Path("/etc/claude-code/managed-settings.json")
CMD = str(Path(__file__).resolve().parent / "eva") + " hook"

TOOL_MATCHER = ("Bash|Edit|Write|NotebookEdit|Read|Grep|Glob|WebFetch|"
                "WebSearch|Agent|Task|TaskCreate|TaskUpdate|Skill|TodoWrite")

WANTED = {
    "PostToolUse":        TOOL_MATCHER,
    "PostToolUseFailure": TOOL_MATCHER,
    "Stop":               None,
    "SessionStart":       None,
    "SessionEnd":         None,
}


def is_ours(entry) -> bool:
    """레이 훅인지 정확히 판별한다.

    'rei'와 'hook'이 부분 문자열로 함께 있다는 것만으로 판정하면
    남의 훅(예: reindex-hook.sh)까지 지워 버린다. rei 실행 파일이나
    reigame 모듈을 hook 인자로 부르는 명령만 우리 것으로 본다.
    """
    for h in entry.get("hooks", []):
        cmd = str(h.get("command", ""))
        if re.search(r"(?:^|[/\s])(?:eva|rei)(?:\.py)?\s+hook(?:\s|$)", cmd):
            return True
        if re.search(r"reigame\s+hook(?:\s|$)", cmd):
            return True
    return False


def load(target):
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.exit(f"{target} 을 읽을 수 없습니다: {exc}")


def install(cfg, remove=False):
    hooks = cfg.setdefault("hooks", {})
    changed = []
    for event, matcher in WANTED.items():
        arr = hooks.setdefault(event, [])
        before = len(arr)
        arr[:] = [e for e in arr if not is_ours(e)]      # 항상 먼저 정리(중복 방지)
        if before != len(arr):
            changed.append(f"  - {event}: 기존 레이 훅 제거")
        if remove:
            if not arr:
                hooks.pop(event, None)
            continue
        entry = {"hooks": [{"type": "command", "command": CMD, "timeout": 10}]}
        if matcher:
            entry["matcher"] = matcher
        arr.append(entry)
        changed.append(f"  + {event}: 레이 훅 추가"
                       + (f" (matcher: {matcher[:30]}…)" if matcher else ""))
    if remove and not hooks:
        cfg.pop("hooks", None)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--global", dest="managed", action="store_true",
                    help="모든 사용자에게 적용 (managed-settings, root 필요)")
    args = ap.parse_args()

    target = MANAGED if args.managed else SETTINGS
    if args.managed and not args.dry_run and os.geteuid() != 0:
        sys.exit("--global 은 root 권한이 필요합니다:  sudo python3 install-hooks.py --global")

    cfg = load(target)
    kept = []
    for ev, arr in (cfg.get("hooks") or {}).items():
        for e in arr:
            if not is_ours(e):
                for h in e.get("hooks", []):
                    kept.append(f"  · {ev}: {str(h.get('command'))[:70]}")

    changed = install(cfg, remove=args.uninstall)

    print("기존 훅 (그대로 보존됨):")
    print("\n".join(kept) if kept else "  (없음)")
    print("\n변경:")
    print("\n".join(changed) if changed else "  (없음)")

    if args.dry_run:
        print("\n--dry-run 이므로 저장하지 않았습니다.")
        return 0

    if target.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = target.with_suffix(f".json.eva-{stamp}.bak")
        shutil.copy2(target, bak)
        print(f"\n백업: {bak}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    if args.managed:
        os.chmod(target, 0o644)   # 모든 사용자의 Claude Code 가 읽어야 한다
    print(f"저장: {target}")
    if not args.uninstall:
        scope = "모든 사용자의" if args.managed else ""
        print(f"\n{scope} 새 Claude Code 세션부터 적용됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
