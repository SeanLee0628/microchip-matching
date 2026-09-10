# -*- coding: utf-8 -*-
"""data URL → bytes 변환 테스트 (stdlib unittest).

실제 버그: xlsx 의 data URL 접두사는 78자라서 'b64[:64] 안에 콤마가 있으면 자른다'
방식으로는 안 잘렸다 → base64.b64decode 가 'Incorrect padding' 으로 터졌다.
실행: python -m unittest test_dataurl -v   (backend/ 에서)
"""
import base64
import unittest

import dataurl

BLOB = b"PK\x03\x04fake-xlsx-bytes-\x00\x01\x02"
B64 = base64.b64encode(BLOB).decode()

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestToBytes(unittest.TestCase):
    def test_xlsx_data_url_78char_prefix(self):
        """회귀: 크롬이 .xlsx 에 붙이는 78자 접두사도 잘린다 (예전엔 Incorrect padding)."""
        url = f"data:{XLSX_MIME};base64,{B64}"
        self.assertEqual(len(url) - len(B64), 78)      # 접두사가 실제로 78자
        self.assertEqual(dataurl.to_bytes(url), BLOB)

    def test_short_prefixes_still_work(self):
        for mime in ("image/png", "application/octet-stream", "application/haansoftxlsx"):
            with self.subTest(mime=mime):
                self.assertEqual(dataurl.to_bytes(f"data:{mime};base64,{B64}"), BLOB)

    def test_raw_base64_without_prefix(self):
        self.assertEqual(dataurl.to_bytes(B64), BLOB)

    def test_missing_padding_is_repaired(self):
        self.assertEqual(dataurl.to_bytes(B64.rstrip("=")), BLOB)

    def test_whitespace_and_newlines_ignored(self):
        chunked = "\n".join(B64[i:i + 8] for i in range(0, len(B64), 8))
        self.assertEqual(dataurl.to_bytes(f"  data:{XLSX_MIME};base64,{chunked}\n"), BLOB)

    def test_bytes_pass_through(self):
        self.assertEqual(dataurl.to_bytes(BLOB), BLOB)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            dataurl.to_bytes("")

    def test_data_url_without_comma_raises(self):
        with self.assertRaises(ValueError):
            dataurl.to_bytes(f"data:{XLSX_MIME};base64")

    def test_garbage_raises_valueerror_not_binascii(self):
        """디코드 실패는 ValueError 로 — 사용자에겐 'Incorrect padding' 대신 읽을 수 있는 메시지."""
        with self.assertRaises(ValueError):
            dataurl.to_bytes("한글이라 base64 가 아님")


class TestToB64(unittest.TestCase):
    def test_returns_string_without_prefix(self):
        out = dataurl.to_b64(f"data:image/png;base64,{B64}")
        self.assertEqual(out, B64)
        self.assertEqual(base64.b64decode(out), BLOB)   # /inspect 가 이 문자열을 그대로 쓴다


if __name__ == "__main__":
    unittest.main(verbosity=2)
