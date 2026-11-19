@echo off
rem 민원똑딱 설치 (Windows). 두 번 눌러 실행한다. 처음 한 번만 하면 된다.
rem 하는 일: 파이썬 가상환경 만들기 → 필요한 프로그램 설치(인터넷 필요) → 근거 DB 만들기(스냅샷에서)
chcp 65001 > nul
setlocal
cd /d "%~dp0"
set SNAPSHOT=2026-11-17

echo.
echo [1/3] 파이썬 가상환경 만들기
where py > nul 2> nul
if errorlevel 1 (
  echo   Python 3.11 이 없어요. https://www.python.org/downloads/ 에서 3.11 을 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 (
    echo   가상환경을 만들지 못했어요. Python 3.11 이 설치되어 있는지 확인해 주세요.
    pause
    exit /b 1
  )
)

echo.
echo [2/3] 필요한 프로그램 설치 (인터넷 필요, 몇 분 걸려요)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   설치에 실패했어요. 인터넷 연결을 확인하고 다시 실행해 주세요.
  pause
  exit /b 1
)

echo.
echo [3/3] 근거 DB 만들기 (스냅샷 %SNAPSHOT%)
if exist "evidence_engine\data\snapshot\%SNAPSHOT%\manifest.json" (
  pushd evidence_engine
  "..\.venv\Scripts\python.exe" data\snapshot.py --restore %SNAPSHOT%
  popd
) else (
  echo   스냅샷 폴더가 없어요: evidence_engine\data\snapshot\%SNAPSHOT%
  echo   근거 수치 없이 형식 예시로만 동작합니다. 실행_매뉴얼.md 의 '빠진 자료 가져오기'를 보세요.
)

if not exist "evidence_engine\.env" (
  copy "evidence_engine\.env.example" "evidence_engine\.env" > nul
  echo.
  echo   evidence_engine\.env 를 만들었어요. 평가용 Claude 키를 ANTHROPIC_API_KEY= 뒤에 넣어 주세요.
)

echo.
echo 설치가 끝났어요. 이제 '실행.bat'를 두 번 눌러 주세요.
pause
