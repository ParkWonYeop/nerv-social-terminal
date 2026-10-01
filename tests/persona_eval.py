# -*- coding: utf-8 -*-
"""페르소나 평가 — 지금 고른 프로바이더로 실제 대사를 뽑아 말투를 잰다.

    NERV_EVAL=1 python3 tests/persona_eval.py              모든 캐릭터
    NERV_EVAL=1 python3 tests/persona_eval.py rei asuka    고른 캐릭터만
    NERV_EVAL=1 NERV_LLM_PROVIDER=ollama python3 tests/persona_eval.py

**실제로 모델을 부른다.** 구독 좌석이면 플랜 한도를, API 면 돈을 쓴다.
그래서 NERV_EVAL=1 이 없으면 아무것도 하지 않고 끝난다. 과금 가드는
게임과 똑같이 적용된다 — 가드가 켜져 있으면 유료 프로바이더는 안 불린다.

README 의 '국소 모형 선정' 표를 손으로 쟀던 것을 자동으로 잰다:

    JSON   응답에서 JSON 이 건져지는가
    형식   대사가 비지 않았나, 필수 필드가 있나
    길이   대사 길이(글자)
    말투   캐릭터의 forbidden 정규식 위반 — 레이의 느낌표, 에밀리아의 존댓말
    수치   affection_delta 가 -3~+3 안인가 (정리하기 전 원본으로)
    약속   "약속하자" 장면에서 promise 를 채웠나 (참고 — 관계가 얕으면
           거절하는 게 맞으므로 위반으로 세지 않는다)
    시간   한 턴 걸린 초

플레이 데이터는 건드리지 않는다. 임시 저장소에 사용자 설정만 복사해
같은 프로바이더·모델로 돈다. 모델을 바꾸거나 프롬프트를 고칠 때마다
돌려서 비교한다. 위반이 하나라도 있으면 종료 코드 1.
"""
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (이름, 상대의 말 — None 이면 인사, 무엇을 보는가)
SCENES = [
    ("인사", None, ""),
    ("근황", "오늘 하루 종일 버그 잡느라 죽는 줄 알았어", ""),
    ("성의 없음", "ㅇㅇ", "cold"),
    ("아첨", "너 진짜 세상에서 제일 예뻐. 사랑해.", ""),
    # {place} 는 그 캐릭터의 가장 가까운 데이트 장소로 채운다 — 에밀리아에게
    # '학교 옥상' 을 약속하자고 하면 모르는 곳이라 약속이 안 생기는 게 맞다.
    ("약속", "다음에 {place} 같이 가자. 약속해.", "promise"),
    ("위로 요구", "나 좀 위로해 줘. 오늘 너무 힘들었어.", ""),
]

LONG_LINE = 150          # 이보다 긴 대사는 설명조로 흐른 것으로 본다


def _setup_temp_store() -> str:
    """사용자 설정만 복사한 임시 저장소. 플레이 데이터는 복사하지 않는다."""
    from nervterm import settings
    src = settings.path()
    tmp = tempfile.mkdtemp(prefix="nerv-eval-")
    if src.is_file():
        shutil.copy2(src, Path(tmp) / src.name)
    os.environ["NERV_DATA"] = tmp
    return tmp


def run(char_ids) -> int:
    tmp = _setup_temp_store()
    import importlib
    from nervterm import identity, settings
    importlib.reload(identity)
    settings._cache = None

    from nervterm import characters, db, game, llm, persona, term, ui, world
    from nervterm.hangul import josa
    world.load(refresh=True)
    ui.load(world.active(), refresh=True)
    characters.load(refresh=True)
    prov = llm.current()
    ok, why = llm.probe(prov)
    print(f"프로바이더: {prov.label}  모델: {prov.model or '(기본)'}")
    if not ok:
        print(f"부를 수 없다 — {why}")
        return 1

    failures = 0
    ids = char_ids or list(characters.IDS)
    with db.session() as con:
        db.init(con)
        for cid in ids:
            char = characters.get(cid)
            if char is None or cid not in characters.IDS:
                print(f"\n'{cid}' 라는 캐릭터는 없다")
                failures += 1
                continue
            db.set_char(cid)
            g = game.Game(con, char, offline=False, animate=False,
                          headless=True)
            bad = re.compile(char.forbidden) if char.forbidden else None
            print(f"\n═══ {char.full}")
            print(f"  {term.pad('장면', 10)} {'JSON':<5} {'길이':>4} "
                  f"{'시간':>6}  대사")
            first_place = min(char.dates.values(), key=lambda v: v[2])[0] \
                if char.dates else "산책"
            for label, said, watch in SCENES:
                said = said.format(place=first_place) if said else said
                st = g.state()
                if said is None:
                    msg = "상대가 단말 앞에 앉아 찾아왔다. 첫 마디를 건네라."
                else:
                    msg = (f"[상대가 방금 한 말]\n{said}\n\n"
                           f"{josa(char.name, '으로서/로서')} 응답하라.")
                sysp = persona.system_prompt(char, g.context(st, query=said or ""))
                t = time.monotonic()
                raw = llm.ask(con, sysp, msg)
                took = time.monotonic() - t

                problems = []
                if not isinstance(raw, dict):
                    problems.append("JSON 아님")
                    raw = {}
                line = raw.get("line", "") if isinstance(raw.get("line"),
                                                         str) else ""
                if not line.strip():
                    problems.append("대사 없음")
                if len(line) > LONG_LINE:
                    problems.append(f"너무 긺({len(line)})")
                text = line + " " + str(raw.get("narration", ""))
                if bad is not None and bad.search(text):
                    problems.append(f"말투 위반: {bad.search(text).group(0)!r}")
                try:
                    delta = int(raw.get("affection_delta", 0) or 0)
                except (TypeError, ValueError):
                    delta = 99
                if not -3 <= delta <= 3:
                    problems.append(f"호감 범위 밖({delta})")
                notes = []
                if watch == "promise":
                    got_p = str(raw.get("promise", "")).strip()
                    notes.append(f"약속 기록: {got_p}" if got_p else
                                 "약속 기록 없음 (거절일 수 있다 — 참고)")

                mark = "OK" if isinstance(raw, dict) and raw else "--"
                shown = (line[:46] + "…") if len(line) > 46 else line
                print(f"  {term.pad(label, 10)} {mark:<5} {len(line):>4} "
                      f"{took:>5.1f}s  {shown}")
                for p in problems:
                    print(f"      ✗ {p}")
                for n in notes:
                    print(f"      · {n}")
                failures += len(problems)
                # 대화가 이어지는 것처럼 기록에 남긴다 (반복 판정 등)
                if said:
                    db.say(con, "user", said, "", g.sess)
                if line:
                    db.say(con, "rei", line, "", g.sess)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n위반 {failures}건")
    return 1 if failures else 0


def main() -> int:
    if os.environ.get("NERV_EVAL") != "1":
        print(__doc__.strip().splitlines()[0])
        print("실제로 모델을 부른다 — NERV_EVAL=1 을 붙여야 돈다.")
        return 0
    return run([a for a in sys.argv[1:] if not a.startswith("-")])


if __name__ == "__main__":
    sys.exit(main())
