@echo off
rem 민원똑딱 실행 (Windows). 두 번 눌러 실행하면 브라우저에 화면이 열린다. 끝내려면 이 창을 닫는다.
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------------------------
rem  Claude(AI) 사용 스위치. 0 이면 Claude 를 부르지 않고 규칙으로만 동작한다(비용 없음).
rem  평가용 키를 evidence_engine\.env 에 넣었으면 아래 0 을 1 로 바꿔 저장한다(사람이 직접 바꾼다).
set ALLOW_PAID_CALLS=0
rem ---------------------------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
  echo 먼저 '설치.bat'를 실행해 주세요.
  pause
  exit /b 1
)
set PYTHONUTF8=1
echo 민원똑딱을 켭니다. 브라우저가 열리지 않으면 http://localhost:8501 로 들어가세요.
echo 끝내려면 이 창을 닫으세요.
".venv\Scripts\python.exe" -m streamlit run complaint_draft\app.py
pause
