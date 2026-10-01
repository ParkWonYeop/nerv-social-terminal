# -*- coding: utf-8 -*-
"""진입점.

  python3 -m nervterm            게임 시작
  python3 -m nervterm hook       훅 모드 (에이전트가 stdin으로 호출)
"""
import argparse
import datetime as _dt
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

# 상태줄 위젯도 같은 이유로 여기서 갈라진다. Claude Code 가 화면을
# 갱신할 때마다 도니 훅보다도 자주 뜬다.
if len(sys.argv) > 1 and sys.argv[1] == "line":
    from .widget import main as _widget_main
    sys.exit(_widget_main())

from . import (characters, db, economy, events, game, menu, plugins, settings,
               term, ui, world)
from .hangul import josa
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
    if not found and not plugins.PARSE_ERRORS:
        print("설치된 플러그인이 없다.")
        return 0
    print(f"{'종류':<10} {'id':<18} {'버전':<8} {'출처':<9} 상태")
    for (kind, pid), p in sorted(found.items()):
        state = "OK" if p.ok else f"오류: {p.error}"
        print(f"{kind:<10} {pid:<18} {p.version:<8} {p.source:<9} {state}")
    for name, why in plugins.PARSE_ERRORS:
        print(f"{'?':<10} {name:<18} {'-':<8} {'-':<9} 오류: {why}")
    print()
    print("찾는 곳:")
    for path, source in plugins.search_paths():
        mark = "" if path.is_dir() else "  (없음)"
        print(f"  {source:<9} {path}{mark}")
    return 0


def first_launch_today(con) -> bool:
    """오늘 처음 켰는가 — 부팅 연출은 하루 한 번만. 매번 8~10초는 길다."""
    today = db.today()
    if db.get(con, "boot_day") == today:
        return False
    db.put(con, "boot_day", today)
    return True


def onboarding_view(con) -> V.HelpView:
    """처음 켰을 때 — 이게 무엇이고 어떻게 굴러가는지 세 줄로."""
    w = world.active()
    table = settings.get("agents", {}) or {}
    on = [name for aid, name in (("claude", "Claude Code"), ("codex", "Codex"),
                                 ("local", "로컬 에이전트")) if table.get(aid)]
    return V.HelpView(
        rows=[("일하면 쌓인다",
               f"에이전트로 실제 작업을 하면 {josa(w.currency_name, '이/가')} "
               f"쌓인다 — 지금 대상: {', '.join(on) or '없음'}"),
              ("말을 건다", "그냥 입력하면 된다. 관계는 하루에 조금씩만 자란다"),
              ("쓴다", "/gift 선물 · /date 데이트 · /care 돌봄 · /episode 이야기"),
              ("모르면", "/help · Tab 으로 명령 자동완성 · 설정은 eva --settings")],
        notes=["Ctrl+C 는 기다리던 대답만 접는다 — 게임은 그대로다."])


def card_summary(con, cid) -> tuple:
    """시작 화면의 한 줄 — 언제 봤나 · 남은 약속 · 못 들은 소식.

    (요약, 눈여겨볼 것) 을 돌려준다. 약속이 걸려 있거나 그 사람이 아직
    모르는 일이 있으면 눈여겨볼 것이다 — 누구부터 만날지 고르는 근거.
    """
    seen = db.get(con, "last_seen", char=cid)
    if not seen:
        parts = ["처음 만난다"]
    else:
        try:
            days = (_dt.date.today()
                    - _dt.date.fromisoformat(seen[:10])).days
        except ValueError:
            days = 0
        parts = ["오늘 만났다" if days <= 0 else
                 "어제 만났다" if days == 1 else f"{days}일 만"]
    promises = con.execute(
        "SELECT COUNT(*) FROM memory WHERE player=? AND char=? "
        "AND kind='promise' AND status=''", (db.PLAYER, cid)).fetchone()[0]
    news = len(events.recent(con, after_id=db.geti(con, "event_mark", char=cid),
                             limit=99)) if seen else 0
    if promises:
        parts.append(f"약속 {promises}")
    if news:
        parts.append(f"새 소식 {news}")
    return " · ".join(parts), bool(promises or news)


def select_view(con) -> V.SelectView:
    world.follow(None)            # 자동 모드면 시작 화면은 기본 세계로
    cards = []
    for cid in characters.ENABLED:
        ch = characters.get(cid)
        aff = db.geti(con, "affection", char=cid)
        stage, _, _ = characters.stage_of(ch, aff)
        summary, attention = card_summary(con, cid)
        cards.append(V.CharacterCard(
            id=ch.id, name=ch.name, full=ch.full,
            ja=ch.display_ja, en=ch.display_en,
            affection=aff, stage=stage,
            color=(ch.theme or {}).get("main", ""),
            pack=getattr(ch, "pack", ""),
            summary=summary, attention=attention))
    w = world.active()
    notes = []
    for problem in menu.plugin_problems():
        notes.append(("danger", problem))
    for line in menu.world_mismatch():
        notes.append(("warn", line))
    return V.SelectView(cards=cards, terminal_name=w.terminal_name or w.name,
                        world_name=w.name, notes=notes)


def play(con, char, args) -> str:
    """한 사람과의 접속. 돌려주는 값: "" 돌아가기 / "quit" 완전 종료."""
    card = next((c for c in select_view(con).cards if c.id == char.id), None)
    db.set_char(char.id)
    world.follow(char)            # 자동 모드면 이 사람의 세계로
    ui.set_character(char)

    g = game.Game(con, char, offline=args.offline,
                  animate=not args.no_anim and settings.get("animation", True))

    if args.status:
        g.pages = False           # 보고 끝낸다 — 키를 기다리지 않는다
        g.status()
        return "quit"

    ui.title_card(card or V.CharacterCard(id=char.id, name=char.name,
                                          full=char.full, ja=char.display_ja,
                                          en=char.display_en))
    # 잠깐 보여 주고 들어간다 — 매번 엔터를 치게 하지 않는다. Esc 면 돌아간다.
    if term.is_tty():
        ui.dim("        (잠시 뒤 들어간다 · Esc 돌아가기)")
        key = term.read_key(timeout=1.6)
        if key == term.KEY_ESC:
            return ""
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            term.stash(key)
    term.set_completer(lambda line: completions(g, line))

    try:
        made = g.consolidate()
        if made:
            g.push("sys", f"{josa(char.name, '이/가')} 지난 대화를 정리했다. "
                          f"기억 {made}개.")
        g.greet()
    except (game.Cancelled, KeyboardInterrupt):
        g.cancelled()

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
            g.redraw()            # 기록 화면을 본 뒤에도 엔터 한 번이면 돌아온다
            continue
        try:
            got = run_command(g, raw)
        except (game.Cancelled, KeyboardInterrupt):
            g.cancelled()
            continue
        if got is not None:
            return got


COMMANDS = {
    # 명령: (별칭들, Game 메서드 이름 — 인자를 받으면 True)
    "talk": (("talk", "say", "t"), "talk", True),
    "date": (("date", "d"), "date", True),
    "gift": (("gift", "g", "shop"), "gift", True),
    "care": (("care", "c", "돌봄"), "care", True),
    "episode": (("episode", "ep", "e", "이야기"), "episode", True),
    "status": (("status", "s"), "status", False),
    "memory": (("memory", "mem", "m"), "memory", False),
    "work": (("work", "w", "worklog", "일지"), "worklog", False),
    "log": (("log", "l", "대화"), "log_view", False),
    "help": (("help", "h", "?"), "help", False),
    "clear": (("clear", "cls", "redraw"), "redraw", False),
}
EXIT_BACK = ("quit", "q", "back", "bye")
EXIT_ALL = ("exit", "종료")


def run_command(g, raw):
    """한 줄을 처리한다. 접속을 끝내면 "" / "quit", 아니면 None."""
    if not raw.startswith("/"):
        g.talk(raw)
        return None
    parts = raw[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in EXIT_BACK:
        return ""
    if cmd in EXIT_ALL:
        return "quit"
    for aliases, method, takes_arg in COMMANDS.values():
        if cmd in aliases:
            fn = getattr(g, method)
            fn(arg) if takes_arg else fn()
            return None
    g.page()
    ui.notice(f"모르는 명령: /{cmd}   (/help · Tab 으로 자동완성)", "danger")
    return None


def completions(g, line: str) -> list:
    """Tab 자동완성 후보. '/gi' → '/gift', '/gift sc' → 'scarf'."""
    if not line.startswith("/"):
        return []
    parts = line.split(" ")
    if len(parts) == 1:
        names = [a[0] for a, _m, _t in COMMANDS.values()]
        return [f"/{n}" for n in names + ["quit", "exit"]]
    table = {
        "date": g.char.dates, "d": g.char.dates,
        "gift": g.char.gifts, "g": g.char.gifts, "shop": g.char.gifts,
        "care": getattr(g.char, "care", None) or {},
        "c": getattr(g.char, "care", None) or {},
        "episode": {k: 1 for k, *_ in getattr(g.char, "episodes", None) or ()},
        "ep": {k: 1 for k, *_ in getattr(g.char, "episodes", None) or ()},
    }
    return sorted((table.get(parts[0][1:].lower()) or {}).keys())


def say_once(words) -> int:
    """TUI 를 띄우지 않고 한 마디 건네고 답만 받는다.

    Claude Code 안에서 `! eva say 안녕` 으로 쓴다. 전체화면을 열지 않으니
    작업하던 화면이 그대로 남는다. 상태줄 위젯과 짝이다.

    게임과 같은 길을 지난다(Game(headless=True)) — 지루함 감점, 인내
    회복, 방치·약속 정산까지. 예전에는 대화 처리를 여기 따로 복제해
    절반만 적용했고, 그래서 say 위주로 쓰면 인내가 줄기만 했다.
    """
    text = " ".join(words).strip()
    if not text:
        print("무슨 말을 할까?  예:  eva say 오늘 좀 힘들었어")
        return 1

    w = world.load()
    ui.load(w)
    if not characters.IDS:
        print("설치된 캐릭터가 없다.")
        return 1

    with db.session() as con:
        db.init(con)
        economy.roll_day(con)
        # 마지막으로 만난 상대. 없으면 첫 번째.
        cid = db.get(con, "widget_char", char="") or ""
        char = characters.get(cid) if cid in characters.IDS else None
        if char is None:
            char = characters.first_enabled()
        db.set_char(char.id)
        world.follow(char)
        ui.set_character(char)

        g = game.Game(con, char, offline=False, animate=False, headless=True)
        g.settle()
        g.talk(text)
        for entry in g.buf:
            if entry.role == "user":
                continue
            if entry.role == "narr":
                ui.dim(entry.text)
            else:
                ui.console.print(ui.entry_text(entry))
    return 0


def main() -> int:
    # 훅·위젯 모드는 위 모듈 최상단에서 이미 갈라져 나갔다.
    if len(sys.argv) > 1 and sys.argv[1] == "say":
        return say_once(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from .wizard import main as setup_main
        return setup_main(sys.argv[2:])
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

        ui.boot(animate=animate and first_launch_today(con))
        if not db.get(con, "onboarded"):
            ui.onboarding(onboarding_view(con))
            db.put(con, "onboarded", db.now())

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
