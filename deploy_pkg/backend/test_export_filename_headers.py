"""엑셀 다운로드 응답 헤더 회귀 테스트.

Content-Disposition 헤더는 latin-1 로만 인코딩된다. 한글 파일명을 그대로 넣으면
Starlette 가 응답을 만들 때 UnicodeEncodeError 를 내고 500 이 된다.
→ ASCII fallback + RFC 5987 (filename*=UTF-8'') 형식이어야 한다.
"""
from urllib.parse import unquote

from fastapi.testclient import TestClient

import main_aws

client = TestClient(main_aws.app)


def _assert_xlsx_download(res, expect_kor):
    assert res.status_code == 200, res.status_code
    assert res.content[:2] == b"PK", "xlsx 가 아님"
    cd = res.headers["content-disposition"]
    cd.encode("latin-1")  # 헤더가 latin-1 로 인코딩 가능해야 함
    assert "filename*=UTF-8''" in cd, cd
    assert expect_kor in unquote(cd.split("filename*=UTF-8''")[1]), cd


def test_sales_report_export_korean_filename():
    res = client.post(
        "/api/sales-report/export",
        json={"rows": [{"FAMILY": "TEST", "MPN": "X1", "QTY": 1}]},
    )
    _assert_xlsx_download(res, "영업실적_보고_")


def test_po_report_export_korean_filename():
    res = client.post(
        "/api/po-report/export",
        json={"t1": [], "t2": []},
    )
    _assert_xlsx_download(res, "발주요청서_보고_")
