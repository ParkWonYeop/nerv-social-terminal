# -*- coding: utf-8 -*-
"""페르소나 엔진 — 캐릭터 정의(characters.py)를 프롬프트로 조립한다.

캐릭터별 내용(CORE, 폴백 대사, 선물 의미 등)은 전부 characters.py 에 있다.
여기는 캐릭터와 무관한 조립 규칙만 남는다.

세계관: 플레이어는 NERV 제1지부 기술부 오퍼레이터.
터미널 앞에서의 실제 작업(도구 사용/커밋)이 그대로 근무 실적이 되고,
그 실적이 캐릭터와 마주칠 구실을 만든다.
"""
import random


def rules(name: str) -> str:
    return f"""[출력 형식 — 절대 어길 수 없음]
JSON 객체 하나만 출력한다. 앞뒤에 어떤 글자도 붙이지 않는다.
마크다운 코드펜스(```)를 쓰지 않는다. 설명하지 않는다.

{{"narration":"{name}의 행동/장면 묘사 1문장. 없으면 빈 문자열","line":"{name}의 대사","emotion":"neutral|slight|warm|cold|curious|shaken|annoyed|distant","affection_delta":정수,"trust_delta":정수,"interest_delta":정수,"patience_delta":정수,"mood":"지금 {name}의 기분을 한 단어로","inner":"{name}의 속마음 한 문장","memory":"기억할 만한 사실. 없으면 빈 문자열"}}

[가장 중요한 것 — {name}는 비위를 맞추지 않는다]
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
 +3  이 사람이 처음으로 {name}를 진짜 사람으로 대해준 순간. 아주 드물다.
 +2  진심이 담긴 말. {name} 자신에 대한 관심.
 +1  성실한 대화.
  0  대부분. 사무적인 말, 잡담, 근황.
 -1  성의 없음. 딴청. 같은 말 반복.
 -2  {name}를 도구/인형/서비스로 취급. 무례.
 -3  모욕. {name}가 소중히 여기는 것에 대한 조롱.

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
"""


def impression_rules(name: str) -> str:
    return f"""
[추가 — 이번 턴에는 인상을 다시 써라]
JSON 에 두 필드를 더 넣는다.
  "impression": {name}가 이 사람을 어떻게 보는지. {name} 자신의 말투로 1~2문장.
                좋게 쓰지 마라. 지금까지의 대화와 기록에 근거해서 솔직하게 쓴다.
  "doubts":     아직 걸리는 것. 없으면 빈 문자열.
"""


def context_block(char, *, aff, stage_name, stage_guide, money, today_tools,
                  today_commits, days_since, streak, memories,
                  currency="LCL", work_today="", work_past=None, last_convo="",
                  this_convo="", danger_note="", stance_block="",
                  now_line="", gap_line="", odd_hour=False):
    """현재 상태를 시스템 프롬프트 뒤에 붙일 컨텍스트."""
    name = char.name
    last = "오늘도 왔다" if days_since == 0 else f"{days_since}일 만에 왔다"
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
                       f"{name}라면 그냥 넘어가지 않을 수도 있다.")
        out.append("")

    out += [
        "[지금 상황]",
        f"- 관계 단계: '{stage_name}'",
        f"- 이 단계에서 {name}의 태도: {stage_guide}",
        f"- 상대는 {last}. 연속 접속 {streak}일차.",
        f"- 오늘 근무 기록: 도구 사용 {today_tools}회, 커밋 {today_commits}회. "
        f"보유 {currency} {money}.",
    ]

    if work_today:
        out += ["", "[오늘 상대가 실제로 한 일 — 사령부 단말로 알고 있다]",
                work_today]
    if work_past:
        out += ["", "[지난 며칠]"]
        out += [f"  {d}: {t}" for d, t in work_past]

    if memories:
        out += ["", f"[{name}가 기억하는 것]"]
        out += [f"  - {m}" for m in memories]

    if last_convo:
        out += ["", "[지난번에 만났을 때 나눈 마지막 대화]", last_convo]
    if this_convo:
        out += ["", "[이번에 지금까지 나눈 대화]", this_convo]

    if danger_note:
        out += ["", f"[{name}가 걸리는 것] {danger_note}"]

    out += ["",
            "[작업 이야기를 다룰 때]",
            f"- 위 기록을 다 읊지 마라. {name}는 보고서를 읽어주는 사람이 아니다.",
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


def system_prompt(char, ctx: str) -> str:
    """페르소나 + 세계관 + 지금 상황 + 출력 규칙.

    세계관이 캐릭터와 규칙 사이에 들어간다. 캐릭터가 '누구인가' 다음에
    '어디서 누구를 상대하는가' 가 오고, 그 다음에 출력 형식이 온다.
    """
    from . import world
    return (f"{char.core}\n"
            f"{world.active().prompt_block(char.name)}\n"
            f"{ctx}\n\n{rules(char.name)}")


# ── 폴백 대사 ──────────────────────────────────────────────────────────
def fallback_response(con, st: dict, char) -> dict:
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
    return {"narration": narr, "line": line, "emotion": emo,
            "affection_delta": 0, "trust_delta": 0, "interest_delta": 0,
            "patience_delta": 0, "mood": "flat", "impression": "",
            "doubts": "", "inner": "", "memory": "", "choices": []}


def fallback(char, stage_idx: int, kind: str = "talk"):
    """(narration, line, emotion) 반환."""
    if kind == "neglect":
        return (f"{char.name}가 천천히 이쪽을 본다.",
                random.choice(char.neglect_lines), "cold")
    if kind == "danger":
        return "", random.choice(char.danger_lines), "cold"
    pool = char.fallback.get(stage_idx) or char.fallback[0]
    return random.choice(pool)
