# -*- coding: utf-8 -*-
"""상점(선물)·데이트 목록 헬퍼. 데이터는 characters.py 의 캐릭터 정의에 있다.

gifts: key → (이름, 가격, 최소호감도, 기본 호감도, 의미)
dates: key → (이름, 가격, 최소호감도, 장면 설정)
"""


def gift_list(gifts: dict, aff: int):
    return [(k, v) for k, v in sorted(gifts.items(), key=lambda x: x[1][1])
            if aff >= v[2]]


def date_list(dates: dict, aff: int):
    return [(k, v) for k, v in sorted(dates.items(), key=lambda x: x[1][1])
            if aff >= v[2]]


def locked_gifts(gifts: dict, aff: int):
    return [(k, v) for k, v in sorted(gifts.items(), key=lambda x: x[1][2])
            if aff < v[2]]


def locked_dates(dates: dict, aff: int):
    return [(k, v) for k, v in sorted(dates.items(), key=lambda x: x[1][2])
            if aff < v[2]]
