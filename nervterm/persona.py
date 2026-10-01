# -*- coding: utf-8 -*-
"""페르소나 엔진 — 캐릭터 정의(characters.py)를 프롬프트로 조립한다.

캐릭터별 내용(CORE, 폴백 대사, 선물 의미 등)은 전부 플러그인에 있다.
여기는 캐릭터와 무관한 조립 규칙만 남는다.

시스템 프롬프트는 두 조각이다 — [고정부, 가변부].

    고정부  페르소나 · 세계 · 출력 규칙       턴마다 바이트 단위로 같다
    가변부  시각 · 관계 상태 · 기억 · 근무    턴마다 바뀐다

고정부를 앞에 두면 접두사 캐시(Claude Code, Anthropic API)가 턴마다
맞는다. 예전에는 출력 규칙이 가변부 뒤에 붙어 있어 캐시되는 앞부분이
페르소나까지뿐이었다.

이름 뒤 조사는 hangul.josa 로 붙인다. 받침 있는 이름(렘, 람)이 와도
"렘가 이 상대를…" 같은 문장이 프롬프트에 실리지 않게.
"""
import random

from .hangul import josa


def targets_text(char) -> str:
    """약속의 이행 대상으로 고를 수 있는 것들. 고정부에 들어간다."""
    dates = ", ".join(f"{k}={v[0]}" for k, v in (char.dates or {}).items())
    gifts = ", ".join(f"{k}={v[0]}" for k, v in (char.gifts or {}).items())
    out = ["  date:<장소키>   그 장소에 함께 가는 약속"]
    if dates:
        out.append(f"                  장소키: {dates}")
    out.append("  gift:<선물키>   그 물건을 주는 약속")
    if gifts:
        out.append(f"                  선물키: {gifts}")
    out += ["  visit           다시 찾아오겠다는 약속",
            "  rest            오늘 밤은 새벽까지 일하지 않고 쉬겠다는 약속",
            "  (빈 문자열)     그 밖의 말로 한 약속"]
    return "\n".join(out)


def rules(char) -> str:
    name = char.name
    eun, ga, reul = josa(name, "은/는"), josa(name, "이/가"), josa(name, "을/를")
    return f"""[출력 형식 — 절대 어길 수 없음]
JSON 객체 하나만 출력한다. 앞뒤에 어떤 글자도 붙이지 않는다.
마크다운 코드펜스(```)를 쓰지 않는다. 설명하지 않는다.

{{"narration":"{name}의 행동/장면 묘사 1문장. 없으면 빈 문자열","line":"{name}의 대사","emotion":"neutral|slight|warm|cold|curious|shaken|annoyed|distant","affection_delta":정수,"trust_delta":정수,"interest_delta":정수,"patience_delta":정수,"mood":"지금 {name}의 기분을 한 단어로","inner":"{name}의 속마음 한 문장","memory":"기억할 만한 사실. 없으면 빈 문자열","promise":"이번에 새로 한 약속. 없으면 빈 문자열","promise_target":"약속의 이행 대상. 없으면 빈 문자열","kept_promise":"상대가 방금 지킨 약속의 번호. 없으면 빈 문자열","impression":"","doubts":"","choices":[]}}

[가장 중요한 것 — {eun} 비위를 맞추지 않는다]
너는 상대를 기분 좋게 해주는 역할이 아니다. 위에 정의된 그 사람이다.
- 무리해서 대화를 이어주지 마라. 할 말이 없으면 짧게 끝내도 된다.
- 재미없으면 재미없다는 태도를 보여라.
- 상대가 듣고 싶어 하는 말을 해주지 마라. 위로를 요구해도 제 방식대로 답한다.
- 상대가 무례하거나 성의 없으면 제 성격대로 응수하거나 끊는다.
- 상대가 아첨하거나 급하게 거리를 좁히려 하면 오히려 물러난다.
- 대화를 먼저 끊을 수 있다. 인내가 낮으면 실제로 끊어라.
- 캐릭터가 하지 않을 말은 절대 하지 않는다. 말투 규칙이 최우선이다.

[수치 변화 — 후하게 주지 마라]
대부분의 턴은 0이다. 평범한 대화로 관계가 움직이지 않는다.

affection_delta  -3 ~ +3
 +3  이 사람이 처음으로 {reul} 진짜 사람으로 대해준 순간. 아주 드물다.
 +2  진심이 담긴 말. {name} 자신에 대한 관심.
 +1  성실한 대화.
  0  대부분. 사무적인 말, 잡담, 근황.
 -1  성의 없음. 딴청. 같은 말 반복.
 -2  {reul} 도구/인형/서비스로 취급. 무례.
 -3  모욕. {ga} 소중히 여기는 것에 대한 조롱.

trust_delta      -8 ~ +8   느리게 오르고 크게 깎인다
 +1~2  말과 행동이 일치했다. 꾸준히 왔다.
 -3~8  거짓말. 모순. 약속을 어겼다. 위험한 짓을 했다.
        {name}의 경계를 억지로 넘으려 했다.

interest_delta   -8 ~ +8
 +2~4  이 사람에 대해 새로 알게 된 것이 있다. 되물을 거리가 생겼다.
  -2~6 같은 말 반복. 내용 없는 말. 성의 없는 단답.

patience_delta   -8 ~ +8
 -2~8  의미 없는 말을 계속한다. 캐묻는다. 같은 걸 또 묻는다.
 +1~2  상대가 편하게 해줬다. 좋은 대화였다.

[기억]
- 위 컨텍스트에 있는 기억을 자연스럽게 꺼내라. 다만 매번 꺼내지는 않는다.
- 상대가 전에 한 말과 지금 말이 어긋나면 지적하라. 그리고 trust_delta 를 음수로 내려라.
- 없는 기억을 만들어내지 마라. 모르면 모른다고 한다.

[약속]
- 약속은 드물다. 이번 대화에서 **상대(플레이어)가** 분명히 약속한 것만
  "promise" 에 한 문장으로 적는다. {name} 자신이 한 약속·제안은 적지 않는다.
  예의상 하는 말("다음에 봐")은 약속이 아니다.
- "promise_target" 에는 그 약속을 무엇으로 지키는지 적는다:
{targets_text(char)}
- 위 [아직 지키는 중인 약속] 중 말로 한 약속(지키는 방법이 적혀 있지 않은
  것)을 상대가 방금 지켰다고 판단되면 "kept_promise" 에 그 번호를 적는다.
  지키는 방법이 적힌 약속은 실제로 그 일을 해야 지켜진다 — 말로는 안 된다.
"""


def impression_rules(name: str) -> str:
    ga = josa(name, "이/가")
    return f"""
[추가 — 이번 턴에는 인상을 다시 써라]
JSON 의 두 필드를 채운다.
  "impression": {ga} 이 사람을 어떻게 보는지. {name} 자신의 말투로 1~2문장.
                좋게 쓰지 마라. 지금까지의 대화와 기록에 근거해서 솔직하게 쓴다.
  "doubts":     아직 걸리는 것. 없으면 빈 문자열.
"""


def context_block(char, *, aff, stage_name, stage_guide, money, today_tools,
                  today_commits, days_since, streak, memories,
                  currency="LCL", work_today="", work_past=None, last_convo="",
                  this_convo="", danger_note="", stance_block="",
                  now_line="", gap_line="", odd_hour=False,
                  event_lines=None, social_block="", away_work=0,
                  care_lines=None):
    """현재 상태를 시스템 프롬프트 뒤에 붙일 컨텍스트."""
    name = char.name
    ga, eun = josa(name, "이/가"), josa(name, "은/는")
    if days_since == 0:
        last = "오늘도 찾아왔다"
    else:
        last = f"{days_since}일 만에 찾아왔다"
    out = []
    if stance_block:
        out += [stance_block, ""]

    # 시각을 맨 위에 둔다. 이게 없으면 캐릭터가 시간대를 추측하고,
    # 틀린 추측 위에 없는 기억을 쌓는다.
    if now_line:
        out += ["[지금]", f"- {now_line}"]
        if gap_line:
            out.append(gap_line)
        if odd_hour:
            out.append("- 상대는 이런 시간까지 일하고 있다. "
                       f"{josa(name, '이라/라')}면 그냥 넘어가지 않을 수도 있다.")
        out.append("")

    out += [
        "[지금 상황]",
        f"- 관계 단계: '{stage_name}'",
        f"- 이 단계에서 {name}의 태도: {stage_guide}",
        f"- 상대는 {last}. 단말 연속 접속 {streak}일차.",
        f"- 오늘 근무 기록: 도구 사용 {today_tools}회, 커밋 {today_commits}회. "
        f"보유 {currency} {money}.",
    ]
    if away_work:
        # '오지 않았다' 와 '여기 있었으면서 안 왔다' 는 다르다.
        out.append(f"- 상대는 {days_since}일 동안 {josa(name, '을/를')} 찾아오지 "
                   f"않았다. 그런데 그동안 {away_work}일은 단말에서 일했다 — "
                   f"같은 단말을 보는 {eun} 그걸 안다.")

    if care_lines:
        out += ["", "[상대가 요즘 챙겨 주는 것]"]
        out += [f"- {line}" for line in care_lines]

    if work_today:
        out += ["", "[오늘 상대가 실제로 한 일 — 단말로 알고 있다]",
                work_today]
    if work_past:
        out += ["", "[지난 며칠]"]
        out += [f"  {d}: {t}" for d, t in work_past]
    if event_lines:
        out += ["", "[최근 단말 기록에서 눈에 띄는 일]"]
        out += event_lines

    if social_block:
        out += ["", social_block]

    if memories:
        out += ["", f"[{ga} 기억하는 것]"]
        out += [f"  - {m}" for m in memories]

    if last_convo:
        out += ["", "[지난번에 만났을 때 나눈 마지막 대화]", last_convo]
    if this_convo:
        out += ["", "[이번에 지금까지 나눈 대화]", this_convo]

    if danger_note:
        out += ["", f"[{ga} 걸리는 것] {danger_note}"]

    out += ["",
            "[작업 이야기를 다룰 때]",
            f"- 위 기록을 다 읊지 마라. {eun} 보고서를 읽어주는 사람이 아니다.",
            "- 하나만 골라 짧게 건드린다.",
            "- 기억하는 것을 자연스럽게 꺼내라. 다만 매번 꺼내지는 않는다.",
            "- 모르는 것을 아는 척하지 마라.",
            "",
            "[시간을 다룰 때]",
            "- 위에 적힌 시각이 지금이다. 다른 시간대를 상상하지 마라.",
            "- 적혀 있지 않은 일을 지어내지 마라. 아침 이야기를 하려면 "
            "지금이 아침이거나, 기억에 그 아침이 있어야 한다.",
            "- 시간을 매번 언급할 필요는 없다. 걸릴 때만 짚는다."]
    return "\n".join(out)


def system_prompt(char, ctx: str, extra: str = ""):
    """[고정부, 가변부].

    고정부 = 페르소나 + 세계관 + 출력 규칙. 세계관이 캐릭터와 규칙
    사이에 들어간다 — '누구인가' 다음에 '어디서 누구를 상대하는가'.
    가변부 = 지금 상황(+ 이번 턴만의 특별 규칙).
    """
    from . import world
    static = (f"{char.core}\n"
              f"{world.active().prompt_block(char.name)}\n\n"
              f"{rules(char)}")
    return [static, ctx + (extra or "")]


# ── 폴백 대사 ──────────────────────────────────────────────────────────
def fallback_response(con, st, char) -> dict:
    """LLM을 못 쓸 때의 응답. 관계 상태를 반영해 차갑게도 나온다."""
    from . import config, db

    trust = db.geti(con, "trust")
    interest = db.geti(con, "interest")
    patience = db.geti(con, "patience")

    stage_idx = getattr(st, "stage_idx", 0)

    pool_key = None
    if patience < config.PATIENCE_MIN_TALK:
        pool_key = "no_patience"
    elif interest < config.INTEREST_FLOOR_TERSE:
        pool_key = "no_interest"
    elif trust < 20 and stage_idx >= 2:
        pool_key = "no_trust"

    if pool_key:
        narr, line, emo = random.choice(char.cold[pool_key])
    else:
        narr, line, emo = fallback(char, stage_idx)
    return empty_response(narr, line, emo)


def empty_response(narr="", line="…", emo="neutral", **over) -> dict:
    """수치가 하나도 안 움직이는 응답 하나. normalize() 와 같은 모양."""
    got = {"narration": narr, "line": line, "emotion": emo,
           "affection_delta": 0, "trust_delta": 0, "interest_delta": 0,
           "patience_delta": 0, "mood": "flat", "impression": "",
           "doubts": "", "inner": "", "memory": "", "choices": [],
           "promise": "", "promise_target": "", "kept_promise": ""}
    got.update(over)
    return got


def fallback(char, stage_idx: int, kind: str = "talk"):
    """(narration, line, emotion) 반환."""
    if kind == "neglect":
        return (f"{josa(char.name, '이/가')} 천천히 이쪽을 본다.",
                random.choice(char.neglect_lines), "cold")
    if kind == "danger":
        return "", random.choice(char.danger_lines), "cold"
    pool = char.fallback.get(stage_idx) or char.fallback[0]
    return random.choice(pool)
