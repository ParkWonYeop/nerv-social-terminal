# -*- coding: utf-8 -*-
"""진입점.

  python3 -m nervterm            게임 시작
  python3 -m nervterm hook       훅 모드 (에이전트가 stdin으로 호출)
"""
import argparse
import os
import sys

# 훅 모드는 **아무것도 무겁게 임포트하기 전에** 갈라진다.
#
# 훅은 도구 호출마다 프로세스로 새로 뜬다. 여기서 game·menu·ui·플러그인을
# 통째로 끌어오면 그 비용이 모든 도구 호출에 붙는다. 실제로 그렇게
# 만들었다가 훅 한 번이 71ms 에서 86ms 가 됐다.
#
# 이 분기가 함수 안이 아니라 모듈 최상단에 있어야 하는 이유가 그것이다.
# main() 안에서 갈라 봐야 임포트는 이미 다 끝난 뒤다.
if len(sys.argv) > 1 and sys.argv[1] == "hook":
    from .hook import main as _hook_main
    sys.exit(_hook_main())

from . import (characters, db, economy, game, menu, plugins, settings, term,
               ui, world)
from .ui import view as V


def parse():
    p = argparse.ArgumentParser(prog="eva", description="그녀들과의 나날")
    p.add_argument("--char", help="캐릭터를 바로 지정 (선택 화면 생략)")
    p.add_argument("--offline", action="store_true",
                   help="LLM을 쓰지 않고 사전 작성 대사만으로 진행")
    p.add_argument("--no-anim", action="store_true", help="타이핑 연출 끄기")
    p.add_argument("--status", action="store_true", help="기록만 보고 종료")
    p.add_argument("--settings", action="store_true",
                   help="설정 화면으로 바로 간다")
    p.add_argument("--ui", help="이번 실행만 다른 UI 플러그인으로")
    p.add_argument("--plugins", action="store_true",
                   help="설치된 플러그인을 보여주고 종료")
    return p.parse_args()


def restart() -> None:
    """UI 플러그인이 바뀌었다 — 같은 인자로 프로세스를 다시 띄운다.

    모듈 수준에 자리 잡은 화면 상태를 깨끗이 하려면 이게 제일 확실하다.
    """
    try:
        os.execv(sys.executable,
                 [sys.executable, "-m", "nervterm"] + sys.argv[1:])
    except OSError:
        # exec 이 안 되면 다음 실행 때 적용된다고 알려 주고 끝낸다.
        print("다시 실행하면 새 화면으로 뜬다.")
        raise SystemExit(0)


def show_plugins() -> int:
    found = plugins.discover()
    if not found:
        print("설치된 플러그인이 없다.")
        return 0
    print(f"{'종류':<10} {'id':<18} {'버전':<8} {'출처':<9} 상태")
    for (kind, pid), p in sorted(found.items()):
        state = "OK" if p.ok else f"오류: {p.error}"
        print(f"{kind:<10} {pid:<18} {p.version:<8} {p.source:<9} {state}")
    print()
    print("찾는 곳:")
    for path, source in plugins.search_paths():
        mark = "" if path.is_dir() else "  (없음)"
        print(f"  {source:<9} {path}{mark}")
    return 0


def select_view(con) -> V.SelectView:
    cards = []
    for cid in characters.ENABLED:
        ch = characters.get(cid)
        aff = db.geti(con, "affection", char=cid)
        stage, _, _ = characters.stage_of(ch, aff)
        cards.append(V.CharacterCard(
            id=ch.id, name=ch.name, full=ch.full,
            ja=ch.display_ja, en=ch.display_en,
            affection=aff, stage=stage,
            color=(ch.theme or {}).get("main", ""),
            pack=getattr(ch, "pack", "")))
    w = world.active()
    notes = []
    for problem in menu.plugin_problems():
        notes.append(("danger", problem))
    return V.SelectView(cards=cards, terminal_name=w.terminal_name or w.name,
                        world_name=w.name, notes=notes)


def play(con, char, args) -> str:
    """한 사람과의 접속. 돌려주는 값: "" 돌아가기 / "quit" 완전 종료."""
    db.set_char(char.id)
    ui.set_character(char)

    g = game.Game(con, char, offline=args.offline,
                  animate=not args.no_anim and settings.get("animation", True))

    if args.status:
        g.status()
        return "quit"

    card = next((c for c in select_view(con).cards if c.id == char.id), None)
    ui.title_card(card or V.CharacterCard(id=char.id, name=char.name,
                                          full=char.full, ja=char.display_ja,
                                          en=char.display_en))
    if term.ask_line("\n        엔터를 눌러 들어간다… ",
                     rgb=(111, 119, 131)) is None:
        return ""

    made = g.consolidate()
    if made:
        g.push("sys", f"{char.name}가 지난 대화를 정리했다. 기억 {made}개.")
    g.greet()

    while True:
        # 프레임이 살아 있으면 커서가 이미 입력 줄에 있다.
        # 목록·기록 화면 뒤에는 구분선과 힌트를 다시 그려 준다.
        if not g.framed:
            ui.prompt_area(game.HINT)
        raw = term.ask_line("  > ", rgb=(201, 138, 43))
        if raw is None:
            return ""
        raw = raw.strip()
        if not raw:
            if g.framed:
                g.redraw()
            continue

        if not raw.startswith("/"):
            g.talk(raw)
            continue

        parts = raw[1:].split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("quit", "q", "back", "bye"):
            return ""
        elif cmd in ("exit", "종료"):
            return "quit"
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
            ui.notice(f"모르는 명령: /{cmd}   (/help)", "danger")


def main() -> int:
    # 훅 모드는 위 모듈 최상단에서 이미 갈라져 나갔다.
    args = parse()
    if args.plugins:
        return show_plugins()

    if args.ui:
        # 이번 실행만. 설정 파일은 건드리지 않는다.
        os.environ["NERV_UI"] = args.ui

    w = world.load()
    ui.load(w)

    if not characters.IDS:
        print("설치된 캐릭터가 없다.")
        print("plugins/ 에 캐릭터 플러그인을 두거나, "
              "python3 -m nervterm --plugins 로 상태를 확인하라.")
        for pack, why in characters.LOAD_ERRORS:
            print(f"  {pack}: {why}")
        return 1

    animate = not args.no_anim and settings.get("animation", True)

    with db.session() as con:
        db.init(con)
        economy.roll_day(con)

        if args.char:
            char = characters.get(args.char)
            if char is None or args.char not in characters.IDS:
                print(f"'{args.char}' 라는 캐릭터는 없다. "
                      f"있는 것: {', '.join(characters.IDS)}")
                return 1
            play(con, char, args)
            return 0

        if args.status:
            play(con, characters.first_enabled(), args)
            return 0

        if args.settings:
            if menu.open_settings(con) == menu.RESTART:
                restart()
            return 0

        ui.boot(animate=animate)

        while True:
            choice, value = ui.select_character(select_view(con))
            if choice == "quit":
                break
            if choice == "settings":
                got = menu.open_settings(con)
                if got == menu.RESTART:
                    restart()
                if got == "quit":
                    break
                # 설정에서 캐릭터를 껐다 켰을 수 있다
                characters.load(refresh=True)
                continue
            char = characters.get(value)
            if char is None:
                continue
            if play(con, char, args) == "quit":
                break
            ui.set_character(char)

        ui.console.print()
        ui.dim("단말 접속을 종료합니다.")
        ui.console.print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
