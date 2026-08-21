# -*- coding: utf-8 -*-
"""진입점.

  python3 -m reigame            게임 시작
  python3 -m reigame hook       훅 모드 (Claude Code가 stdin으로 호출)
"""
import argparse
import sys

from . import characters, db, economy, game, ui


def parse():
    p = argparse.ArgumentParser(prog="eva", description="그녀들과의 나날")
    p.add_argument("--char", choices=list(characters.IDS),
                   help="캐릭터를 바로 지정 (선택 화면 생략)")
    p.add_argument("--offline", action="store_true",
                   help="LLM을 쓰지 않고 사전 작성 대사만으로 진행")
    p.add_argument("--no-anim", action="store_true", help="타이핑 연출 끄기")
    p.add_argument("--status", action="store_true", help="기록만 보고 종료")
    return p.parse_args()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import main as hook_main
        return hook_main()

    args = parse()
    with db.session() as con:
        db.init(con)
        economy.roll_day(con)

        # ── 캐릭터 선택 ────────────────────────────────────────────────
        if args.char:
            char = characters.get(args.char)
        elif args.status:
            char = characters.REI
        else:
            ui.boot_sequence(animate=not args.no_anim)
            infos = []
            for cid in characters.IDS:
                ch = characters.get(cid)
                aff = db.geti(con, "affection", char=cid)
                stage, _, _ = characters.stage_of(ch, aff)
                infos.append((ch, aff, stage))
            char = ui.select_character(infos)
            if char is None:
                return 0
        db.set_char(char.id)
        ui.set_character(char)

        g = game.Game(con, char, offline=args.offline,
                      animate=not args.no_anim)

        if args.status:
            g.status()
            return 0

        ui.title_card()
        try:
            ui.console.input("\n        [dim]엔터를 눌러 들어간다…[/dim]")
        except (EOFError, KeyboardInterrupt):
            return 0

        made = g.consolidate()
        if made:
            g.push("sys", f"레이가 지난 대화를 정리했다. 기억 {made}개.")
        g.greet()

        while True:
            # 프레임이 살아 있으면 커서가 이미 입력 줄에 있다.
            # 목록·기록 화면 뒤에는 구분선과 힌트를 다시 그려 준다.
            if not g.framed:
                ui.prompt_area(game.HINT)
            try:
                raw = ui.read_input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                ui.console.print()
                break
            if not raw:
                if g.framed:
                    g.redraw()
                continue

            if raw.startswith("/"):
                parts = raw[1:].split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                if cmd in ("quit", "exit", "q", "bye"):
                    break
                elif cmd in ("talk", "say", "t"):
                    g.talk(arg)
                elif cmd in ("date", "d"):
                    g.date(arg)
                elif cmd in ("gift", "g", "shop"):
                    g.gift(arg)
                elif cmd in ("status", "s", "log"):
                    g.status()
                elif cmd in ("memory", "mem", "m"):
                    g.memory()
                elif cmd in ("work", "w", "worklog", "일지"):
                    g.worklog()
                elif cmd in ("help", "h", "?"):
                    g.help()
                elif cmd in ("clear", "cls", "redraw"):
                    g.redraw()
                else:
                    g.page()
                    ui.notice(f"모르는 명령: /{cmd}   (/help)", ui.EYE)
            else:
                g.talk(raw)

        ui.console.print()
        ui.dim("단말 접속을 종료합니다.")
        ui.console.print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
