@echo off
chcp 65001 > nul
title 자재 AI Agent 로컬 서버 (main.py : 8001)
echo ===============================================
echo   자재 출고 자동등록 - 로컬 백엔드 서버
echo   포트 8001 (이 창을 켜둔 채로 사용하세요)
echo ===============================================
echo.
echo 사용법:
echo   1) 처리할 Excel 파일을 먼저 열어둡니다
echo   2) 클라우드 페이지에서 [출고 자동등록 (AI Agent)] 실행
echo   3) 끝나면 Excel 에서 Ctrl+S 저장
echo.
echo 서버를 끄려면 이 창에서 Ctrl+C 또는 창을 닫으세요.
echo -----------------------------------------------
cd /d "%~dp0"
python main.py
echo.
echo 서버가 종료되었습니다.
pause
