# -*- coding: utf-8 -*-
"""한국어 조사 — 앞말의 받침에 따라 갈린다.

캐릭터 이름과 재화 이름은 플러그인이 정한다. 레이·아스카·미사토·에밀리아는
전부 모음으로 끝나서 "{name}가" 로 박아 둬도 티가 안 났다. 렘이나 람이
오면 프롬프트에 "렘가 이 상대를…", "렘로서 응답하라" 가 실린다. 실제로
리제로 세계의 재화(동화)는 이미 "동화이 부족하다" 로 찍히고 있었다.

    josa("렘", "이/가")      → "렘이"
    josa("레이", "이/가")    → "레이가"
    josa("서울", "으로/로")  → "서울로"     (ㄹ 받침은 '로')

순수 계산만 한다. 훅·위젯 경로에서 써도 될 만큼 가볍다.
"""

# (받침 있을 때, 없을 때)
_PAIRS = {
    "이/가": ("이", "가"),
    "은/는": ("은", "는"),
    "을/를": ("을", "를"),
    "과/와": ("과", "와"),
    "으로/로": ("으로", "로"),
    "으로서/로서": ("으로서", "로서"),
    "아/야": ("아", "야"),
    "이랑/랑": ("이랑", "랑"),
    "이나/나": ("이나", "나"),
    "이라/라": ("이라", "라"),
}

# ㄹ 받침 뒤에서는 받침이 없는 쪽을 쓰는 조사들
_RIEUL_LIKE_VOWEL = {"으로/로", "으로서/로서"}

# 숫자를 한국어로 읽었을 때의 받침 — 0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔
_DIGIT_FINAL = {"0": "ㅇ", "1": "ㄹ", "3": "ㅁ", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ"}

# 라틴 문자는 읽는 법을 모른다. 자음으로 끝나면 받침이 있다고 본다
# ("Ken" 켄, "Bob" 밥). 모음·반모음(r, w, y, h)으로 끝나면 없다고 본다.
_LATIN_CONSONANT = set("bcdgjklmnpqtvxz")


def _final(word: str) -> str:
    """마지막 글자의 받침. '' 없음 / 'ㄹ' / 'x'(그 밖의 받침)."""
    for ch in reversed(word or ""):
        if ch.isspace() or ch in ")]}」』'\".,!?…~":
            continue
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            jong = (code - 0xAC00) % 28
            if jong == 0:
                return ""
            return "ㄹ" if jong == 8 else "x"
        if ch in _DIGIT_FINAL:
            return "ㄹ" if _DIGIT_FINAL[ch] == "ㄹ" else "x"
        if ch.isdigit():
            return ""
        low = ch.lower()
        if low.isascii() and low.isalpha():
            if low == "l":
                return "ㄹ"
            return "x" if low in _LATIN_CONSONANT else ""
        return ""
    return ""


def particle(word: str, pair: str) -> str:
    """조사만. josa() 가 앞말까지 붙여 준다."""
    if pair not in _PAIRS:
        raise ValueError(f"모르는 조사: {pair}")
    with_final, without = _PAIRS[pair]
    fin = _final(word)
    if not fin:
        return without
    if fin == "ㄹ" and pair in _RIEUL_LIKE_VOWEL:
        return without
    return with_final


def josa(word: str, pair: str) -> str:
    """앞말 + 맞는 조사."""
    return f"{word}{particle(word, pair)}"


if __name__ == "__main__":
    for w, p, want in (("렘", "이/가", "렘이"), ("레이", "이/가", "레이가"),
                       ("람", "으로서/로서", "람으로서"),
                       ("레이", "으로서/로서", "레이로서"),
                       ("서울", "으로/로", "서울로"), ("동화", "이/가", "동화가"),
                       ("LCL", "이/가", "LCL이"), ("렘", "과/와", "렘과"),
                       ("미사토", "은/는", "미사토는"), ("Rei", "을/를", "Rei를")):
        assert josa(w, p) == want, (w, p, josa(w, p))
    print("ok")
