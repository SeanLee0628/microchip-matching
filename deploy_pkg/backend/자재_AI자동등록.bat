@echo off
chcp 65001 > nul
echo ===============================================
echo   자재 AI 자동등록 (xlwings)
echo ===============================================
echo.
echo 처리 대상 Excel 파일을 먼저 열어두셨나요?
echo (현재 Excel에서 활성화된 워크북을 처리합니다)
echo.
pause
cd /d "%~dp0"
python jaejae_xl.py
echo.
echo 끝났습니다. Excel에서 Ctrl+S 로 저장하세요.
pause
