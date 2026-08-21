# 플러그인 만들기

코어는 **관계가 어떻게 움직이는가**만 안다. 호감도가 어떻게 오르고
내리는지, 기억이 어떻게 회상되는지, 재화가 어떻게 쌓이는지.

그 밖의 것 — 누구를 만나는지, 어떤 세상인지, 어떻게 보이는지 — 은
전부 플러그인이 정한다.

| 종류 | 무엇을 정하나 | 동시에 |
|---|---|---|
| `character` | 사람. 페르소나·단계·선물·데이트 | 여러 개 |
| `world` | 재화 이름, 플레이어의 역할 | 하나 |
| `ui` | 색·연출·화면 배치 | 하나 |

## 어디에 두나

```
<설치 폴더>/plugins/                          동봉된 것
~/.local/share/nerv-social-terminal/plugins/  내가 설치한 것
$NERV_PLUGIN_PATH                             개발용 (: 로 여러 개)
```

뒤에서 찾은 것이 앞을 덮는다. 동봉된 `nerv` UI 를 내 것으로 갈아끼우고
싶으면 사용자 폴더에 같은 id 로 두면 된다.

폴더 하나가 플러그인 하나다. 안에 `plugin.toml` 이 있어야 한다.

```
my-plugin/
  plugin.toml
  characters.py      ← plugin.toml 의 entry
  안에서 쓰는 다른 .py 파일들도 import 된다
```

확인:

```bash
python3 -m nervterm --plugins
```

깨진 플러그인이 있어도 게임은 켜진다. 사유가 설정 화면과 이 명령에 뜬다.

---

## 캐릭터 팩

`plugin.toml`:

```toml
[plugin]
id = "my-characters"
kind = "character"
name = "내 캐릭터들"
version = "1.0.0"
api = 1
entry = "characters.py"
description = "한 줄 설명"
author = "누구"

[character]
world = "nerv"      # 이 팩이 전제하는 세계관
```

`characters.py` 는 `CHARACTERS` 목록 하나를 내놓는다:

```python
from nervterm.spec import Character

MY_CHAR = Character(
    id="hoshino",
    name="호시노",              # 대사 앞에 붙는 짧은 호칭
    full="호시노 아이",
    ja="星野アイ",              # 없어도 된다
    en="HOSHINO AI",

    core="""너는 …다.

[말투 — 반드시 지킬 것]
- 한국어 반말. 문장은 짧다.
- 이모지를 쓰지 않는다.
""",

    start={"affection": 5, "trust": 10, "interest": 25, "patience": 70},

    theme={
        "main": "#9ec5e0",
        # stage 색은 stages 개수 이상이어야 한다
        "stage": ["#6f7783", "#7f97a8", "#9ec5e0",
                  "#b7d6ea", "#e4b7c4", "#f0c9d4"],
        # 여덟 감정이 **전부** 있어야 한다. 빠지면 로드가 거부된다 —
        # 조용히 neutral 로 떨어져 표현이 사라지는 게 더 나쁘다.
        "emotion": {
            "neutral": "#9ec5e0", "slight": "#b7d6ea", "warm": "#e4b7c4",
            "cold": "#7f9db8", "curious": "#a9c9b4", "shaken": "#d2565a",
            "annoyed": "#c99a8f", "distant": "#8ea6b8",
        },
    },

    # (하한, 이름, 이 단계에서의 태도 지침) — 하한 0 부터 오름차순
    stages=[
        (0,  "무관심", "대답은 한 마디. 종종 침묵한다."),
        (10, "인식",   "묻는 말에는 답한다."),
        (25, "관심",   "가끔 먼저 짧은 질문을 던진다."),
        (45, "신뢰",   "자기 얘기를 조금 한다."),
        (65, "애착",   "먼저 말을 건다."),
        (85, "…웃는 법", "드물게 웃는다."),
    ],

    # 축별 태도 지침. 수치가 아니라 **말**로 적는다 —
    # 이게 프롬프트에 그대로 들어가서 실제로 차갑게 굴게 만든다.
    tone={
        "trust":    [(0, "사적인 것을 말하지 않는다."),
                     (35, "사실은 말하지만 감정은 아직."),
                     (70, "믿는다.")],
        "interest": [(0, "단답. 되묻지 않는다."),
                     (25, "가끔 짧게 되묻는다."),
                     (60, "먼저 질문한다.")],
        "patience": [(0, "대화를 끊으려 한다."),
                     (30, "참아주고 있다."),
                     (65, "여유가 있다.")],
    },

    # LLM 을 못 쓸 때. 단계 인덱스별 (narration, line, emotion).
    # 0 번은 반드시 있어야 한다.
    fallback={
        0: [("", "…", "neutral"),
            ("창밖을 보고 있다.", "…무슨 일이야.", "cold")],
        1: [("", "왔네.", "neutral")],
    },

    # 관계가 나쁠 때. 네 가지가 전부 있어야 한다.
    cold={
        "no_patience": [("", "…지금은 됐어.", "distant")],
        "no_interest": [("", "그래.", "cold")],
        "no_trust":    [("", "왜 그런 걸 물어?", "cold")],
        "boring":      [("", "…같은 말이야.", "annoyed")],
    },

    # key: (이름, 가격, 최소호감, 기본호감, 이 물건의 의미 — LLM 에 전달)
    gifts={
        "tea": ("홍차", 50, 0, 1, "흔한 물건. 고맙다고는 한다."),
    },
    # key: (이름, 가격, 최소호감, 장면 설정 — LLM 에 전달)
    dates={
        "roof": ("옥상", 100, 0, "방과 후 옥상. 바람이 세다."),
    },
)

CHARACTERS = [MY_CHAR]
```

없어도 되는 것: `ja`, `en`, `neglect_lines`, `danger_lines`, `refusal`,
`refusal_trust`, `refusal_default`, `greet_narr`. 기본값이 채워진다.

**선물·데이트의 마지막 칸이 중요하다.** 그건 화면에 안 뜬다 —
LLM 에게만 간다. "이게 이 사람에게 무슨 의미인가"를 적을수록 반응이
좋아진다. 값이 비싼 것보다 사연이 있는 것이 잘 먹힌다.

### 검증

빠진 필드는 게임 도중이 아니라 **로드할 때** 잡힌다:

```
eva-characters: hoshino: theme['emotion'] 에 shaken 가 없다
```

혼자 확인하려면:

```python
from nervterm.spec import validate_character
validate_character(MY_CHAR)      # 문제가 있으면 SpecError
```

---

## 세계관

플레이어가 **누구이고 무엇을 버는가**를 정한다. 캐릭터가 에반게리온의
인물이라는 건 캐릭터 쪽 사실이지만, 플레이어가 'NERV 기술부
오퍼레이터'라는 건 세계 쪽 사실이다. 그래서 나눠 두었다.

```toml
[plugin]
id = "office"
kind = "world"
name = "그냥 회사"
version = "1.0.0"
api = 1
entry = "world.py"
```

```python
from nervterm.spec import World

WORLD = World(
    id="office",
    name="그냥 회사",
    currency_name="커피",           # 재화 이름. 화면 전체에 이걸로 뜬다
    currency_symbol="☕",
    player_role="이 회사의 개발자다. 종일 단말 앞에 앉아 있다",
    work_framing="상대가 무슨 작업을 했는지 사내 대시보드로 알고 있다.",
    setting="- 평범한 사무실. 형광등과 커피 머신.",
    terminal_name="사내 개발 단말",     # 화면 상단
)
```

`setting` 은 짧게 쓴다. 길면 캐릭터의 페르소나를 밀어낸다.

---

## UI

`BaseUI` 를 상속해서 **바꾸고 싶은 것만** 덮어쓴다. 전부 구현할 필요가
없다 — 안 건드린 것은 기본 렌더러가 그린다.

```toml
[plugin]
id = "my-ui"
kind = "ui"
version = "1.0.0"
api = 1
entry = "ui.py"
```

```python
from nervterm.ui.base import BaseUI, console

class UI(BaseUI):
    PALETTE = {                     # 이거 하나만 바꿔도 된다
        "plain": "white",   "info": "cyan",       "good": "green",
        "warn": "yellow",   "danger": "red",      "money": "yellow",
        "dim": "bright_black",      "accent": "bright_white",
    }
```

이게 전부인 플러그인도 정상이다. 더 하고 싶으면:

| 덮어쓸 것 | 언제 불리나 |
|---|---|
| `boot(animate=)` | 게임 시작. 여기에 연출을 넣는다 |
| `title_card(card)` | 캐릭터를 고른 직후 |
| `select_character(view)` | 시작 화면. `("char", id)` / `("settings", None)` / `("quit", None)` 반환 |
| `frame(status, entries, hints, animate=, delay=)` | 대화 화면 전체 |
| `header(status)` | 상태창 내용 |
| `wrap_header(status)` | 상태창을 감싸는 것 (테두리 등) |
| `shop(view)` | 선물·데이트 목록 |
| `status(view)` / `memory(view)` / `worklog(view)` / `help(view)` | 각 기록 화면 |
| `menu(view)` | 설정 화면 전부. 고른 항목의 key 반환 |
| `notice(text, tone)` / `dim(text)` / `confirm(prompt, phrase)` | 짧은 출력 |
| `thinking(name)` | 대기 표시 (context manager) |

넘어오는 것은 전부 `nervterm.ui.view` 의 데이터 클래스다. 색도 좌표도
없다 — `tone` 은 `"plain" | "info" | "good" | "warn" | "danger" | "money"`
같은 **의미**고, 그게 무슨 색인지는 플러그인이 정한다.

`plugins/plain-ui/` 가 짧은 예제다. 팔레트·연출·화면 배치를 각각 어떻게
바꾸는지 한 파일에 들어 있다.

### 터미널을 직접 다뤄야 할 때

`nervterm.term` 에 배관이 있다. UI 가 바뀌어도 이 층은 안 바뀐다.

```python
from nervterm import term

term.width("한글")        # 표시 폭 (한글은 2칸)
term.pad("이름", 12)      # 폭 기준 패딩
term.truncate(s, 40)      # 폭 기준 자르기
term.ask_line("  > ")     # 한 줄 입력. 취소면 None
term.echo_off()           # 에코 끄기 (context manager)
term.type_inline(...)     # 한 글자씩 찍기
```

---

## 규칙 몇 가지

**플러그인은 `nervterm.spec`, `nervterm.ui.base`, `nervterm.term` 만
임포트한다.** `db` 나 `economy` 같은 내부는 건드리지 않는다. 그쪽은
언제든 바뀐다.

**하나가 깨져도 게임은 켜진다.** 로드 실패는 예외를 던지지 않고 사유로
남아 설정 화면에 뜬다.

**`api = 1` 을 적는다.** 계약이 바뀌면 이 숫자가 오르고, 맞지 않는
플러그인은 사유와 함께 거부된다.

**id 가 겹치면 나중에 찾은 것이 이긴다.** 캐릭터 id 가 겹치면 나중
것은 건너뛰고 경고가 남는다 — 저장된 관계가 섞이지 않게.
