# 개발 가이드

- 소스를 고치거나 기능을 더하려는 개발자를 위한 문서
- 센터 PC에서 실행만 할 때는 [실행 매뉴얼](../실행_매뉴얼.md) 참고

## 설치

- Python 3.11 기준
- 세 모듈을 가상환경 하나에 함께 설치함

macOS, Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

- PowerShell이 `Activate.ps1`을 막으면 그 창에서만 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`를 실행하고 다시 켬
- 설치 폴더는 `C:\jeju\`처럼 짧은 경로 권장. Windows 기본 경로 길이 제한은 260자
- 한국어 Windows는 출력을 CP949로 내보냄. 실행 진입점은 CP949에 없는 글자를 `?`로 바꿔 멈추지 않게 처리함. UTF-8로 받으려면 `$env:PYTHONUTF8=1` 설정

근거 엔진만 쓸 때는 `evidence_engine/requirements.txt`만 설치해도 됨.

## 환경 변수

- `evidence_engine/.env.example`과 `dialect_normalizer/.env.example`을 각각 `.env`로 복사해서 사용
- `.env`는 저장소에 올리지 않음

| 이름 | 용도 | 기본값 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Claude 호출 | 없음 (없으면 규칙으로 동작) |
| `ALLOW_PAID_CALLS` | 유료 호출 허용 스위치. 실행하는 셸에서 사람이 직접 `1`로 설정할 때만 호출이 나감 | 꺼짐 |
| `CLAUDE_MODEL` | 실행 중 쓰는 모델 | `claude-haiku-5-5` |
| `CLAUDE_TIMEOUT_SEC` | Claude 응답을 기다리는 시간(초) | `60` |
| `CLAUDE_THINKING` | thinking 사용 여부 | `disabled` |
| `CLAUDE_MOCK` | `1`이면 네트워크 없이 빈 응답 (화면과 연결 확인용) | 꺼짐 |
| `JEJUDATAHUB_PROJECT_KEY` | 제주데이터허브 Open API (데이터 수집 때만) | 없음 |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 Open API (데이터 수집 때만) | 없음 |
| `OPENROUTER_API_KEY` | 근거 엔진 LLM 공급자를 OpenRouter로 바꿀 때만 | 없음 |

- 연결 실행은 두 `.env`에서 `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_THINKING`만 읽음
- `ALLOW_PAID_CALLS`는 `.env`에서 읽지 않음. 키 파일을 복사하는 것만으로 요금이 나가지 않게 하기 위한 장치
- 데이터 수집용 키 발급 방법은 [데이터 파이프라인](data-pipeline.md#준비) 참고

## 실행

```bash
streamlit run complaint_draft/app.py                                  # 화면
python -m complaint_agent "구좌읍 버스가 줄어서 병원 가기 힘들어요"           # 터미널 대화
python -m complaint_agent --place 구좌읍 --topic 경로당 "..."              # 지역과 주제를 미리 고름
python -m complaint_agent --out handoff.json "..."                     # 민원서 입력 JSON 저장
python -m complaint_agent --mock-engine "..."                          # 근거 DB 없이 형식만
python evidence_engine/run_engine.py --query evidence_engine/tests/fixtures/q01.json   # 엔진만
```

- 화면 주소 뒤에 `?mode=dev`를 붙이면 엔진 알림과 `handoff.json` 받기 단추가 표시됨
- 기본 화면은 어르신용이라 이 정보를 숨김
- 저장소의 `.streamlit/config.toml`은 운영 설정이라 코드가 바뀌어도 화면을 다시 그리지 않음
- 개발할 때는 명령줄에서 덮어씀

```bash
streamlit run complaint_draft/app.py --server.fileWatcherType auto --client.showErrorDetails full
```

## 테스트

```bash
python -m pytest -q                          # 전체 523개, 네트워크와 유료 호출 없이
python -m pytest -q evidence_engine/tests    # 근거 엔진만
```

- 유료 API를 실제로 부르는 테스트는 `@pytest.mark.paid`로 표시함. `ALLOW_PAID_CALLS=1`이 없으면 건너뜀
- 근거 DB와 데이터셋 카드가 있으면 실제 계산 테스트도 함께 돌고, 없으면 그 테스트만 건너뜀

GitHub Actions(`.github/workflows/tests.yml`)는 Ubuntu와 Windows에서 두 가지 환경을 검사함.

- 전체 설치: 테스트, 터미널 대화 끝까지(CP949 출력), Windows에서는 `설치.bat` 실제 실행
- 일부가 빠진 설치: scikit-learn, streamlit, 사전, DB, 키가 없어도 멈추지 않는지, 한 프로그램에서 민원 두 건을 이어서 처리하는지

## 평가

```bash
python -m complaint_agent.eval.run_eval          # 정답 세트 32건, 유료 호출 없이
cd dialect_normalizer && python evaluate.py --limit 20   # 제주어 변환 (유료 호출, ALLOW_PAID_CALLS=1 필요)
```

정답 세트 평가는 읍면동과 주제 인식, 뜻 선택지, 근거 수와 판정 분포, 남은 한계를 표로 뽑아 `complaint_agent/eval/results/`에 저장함.

## 제출 ZIP 만들기

```bash
python make_submission.py                     # dist/제주민원도우미_제출_<날짜>.zip
python make_submission.py --with-claude-key   # 평가용 키(EVAL_ANTHROPIC_API_KEY)를 ZIP에 포함
```

- ZIP 구성: 저장소 파일, 근거 DB 스냅샷, 데이터셋 카드, 법정동 연계정보, 제주어 사전과 색인
- 빠진 자료가 있으면 ZIP 안의 `제출물_구성.txt`에 가져오는 방법이 기록됨
- 개발용 `.env`, 원자료, 만들어진 DB, 로그는 넣지 않음

## 코드 규칙

- 경로는 `pathlib`으로 다루고, 셸 전용 명령은 쓰지 않음 (Windows 호환)
- 의존성은 `requirements.txt`에 버전을 고정해서 추가함
- 데이터셋 이름과 컬럼명은 카탈로그와 DuckDB에 실제로 있는 것만 씀
- 모듈 사이 계약(`evidence_engine/contracts/`)을 바꿀 때는 양쪽 코드와 테스트를 같은 변경에서 함께 고침
- 커밋 메시지는 `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:` 뒤에 한국어 요약을 붙임
