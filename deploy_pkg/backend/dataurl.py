# -*- coding: utf-8 -*-
"""브라우저가 보낸 data URL / base64 문자열 → bytes.

FileReader.readAsDataURL 은 'data:<mime>;base64,<본문>' 형태를 준다.
접두사 길이는 MIME 에 따라 다르다:
    data:image/png;base64,                                                      (22자)
    data:application/octet-stream;base64,                                       (37자)
    data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,  (78자)
그래서 '앞 N자 안에 콤마가 있으면 자른다' 식으로 검사하면 xlsx 에서 접두사가
안 잘린 채 디코딩돼 'Incorrect padding' 이 난다. 콤마 위치로 자르지 말고
'data:' 로 시작하는지로 판단한다.
"""
import base64

_B64_ALPHABET = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def to_b64(s):
    """data URL 이든 순수 base64 든 → 순수 base64 문자열. 실패하면 ValueError."""
    if not s:
        raise ValueError("빈 업로드입니다.")
    s = s.strip()
    if s.startswith("data:"):
        _, sep, body = s.partition(",")
        if not sep:
            raise ValueError("data URL 에 base64 본문(',' 뒤)이 없습니다.")
        s = body
    s = "".join(s.split())                     # 줄바꿈/공백 제거
    if not s or set(s) - _B64_ALPHABET:
        raise ValueError("base64 로 읽을 수 없는 데이터입니다.")
    return s + "=" * (-len(s) % 4)             # 패딩 보정


def to_bytes(s):
    """data URL 이든 순수 base64 든 bytes 로. 실패하면 ValueError."""
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    return base64.b64decode(to_b64(s))
