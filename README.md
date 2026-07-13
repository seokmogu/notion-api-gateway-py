# Notion API Gateway

Notion API 토큰 자동 발급 서비스. 노션 폼에서 신청하면 통합 생성, 토큰 발급, 페이지 연결, Slack DM 알림까지 자동으로 처리합니다.

## 동작 흐름

```
신청자가 노션 폼에서 API 키 신청
  → 폴링으로 "Requested" 상태 감지
  → Slack으로 신청 접수 알림
  → Playwright 브라우저 자동화로 Notion 통합 생성
  → 토큰 발급 및 페이지 연결
  → Slack으로 발급 완료 알림
  → 노션 DB 상태를 "완료"로 업데이트
```

### 상태 흐름

```
Requested → Processing → Issued → 완료
                ↓
              Failed (최대 3회 재시도)
```

## 요구사항

- [uv](https://docs.astral.sh/uv/) 0.10+
- Python 3.12+
- Chromium (Playwright가 자동 설치)
- Notion API 토큰 (Internal Integration)
- Slack Bot Token (선택)

## 로컬 설치

```bash
# uv 사용 (권장)
uv venv
uv run playwright install chromium
uv sync
cp .env.example .env  # 환경변수 설정

# 또는 pip 사용
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
cp .env.example .env
```

## 환경변수

### 필수

| 변수 | 설명 | 예시 |
|------|------|------|
| `NOTION_GATEWAY_TOKEN` | API 발급 자동화 전용 Notion Internal Integration 토큰 | `ntn_xxx` |
| `NOTION_GATEWAY_REQUESTS_DATABASE_ID` | 신청 폼이 연결된 Notion DB ID (UUID) | `3297d832-2b04-8087-ab79-fd8dc364f884` |

`NOTION_TOKEN`, `NOTION_REQUESTS_DATABASE_ID`도 기존 배포 호환용 fallback으로 지원합니다.
같은 호스트에서 WDC와 함께 운영할 때는 gateway 전용 변수명을 사용해 토큰을 분리합니다.

### 브라우저 자동화

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NOTION_BROWSER_PROFILE_DIR` | `./data/notion-browser-profile` | 브라우저 프로필 저장 경로 (persistent context) |
| `NOTION_HEADLESS` | `true` | 헤드리스 모드 (`false`로 설정 시 브라우저 창 표시) |
| `NOTION_INTEGRATION_NAME_PREFIX` | `API Access` | 생성되는 통합 이름 접두사 |
| `NOTION_WORKSPACE_NAME` | - | 워크스페이스 선택 힌트 (여러 워크스페이스가 있을 때) |
| `NOTION_GATEWAY_EMAIL` | `notion-automation@worxphere.ai` | 자동 로그인용 이메일 (SSO 미지원) |
| `NOTION_GATEWAY_PASSWORD` | - | 자동 로그인용 비밀번호 |
| `NOTION_GATEWAY_LOGIN_CODE` | - | 2FA 코드 (자동 로그인 시) |

### Slack 알림

현재 연결된 Slack 앱 정보:

| 항목 | 값 |
|------|-----|
| 앱 이름 | `worx-agent` |
| App ID | `A0ARU9YAP52` |
| Bot ID | `B0ARNKVLVPY` |
| 워크스페이스 | 웍스피어 (`jobkorea-linker.slack.com`) |
| 관리 페이지 | https://api.slack.com/apps/A0ARU9YAP52 |

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NOTION_GATEWAY_SLACK_BOT_TOKEN` | - | Slack Bot Token (`xoxb-...`). 필요 스코프: `chat:write`, `users:read.email`, `users:read` |

### 자동 복구 / 관리자 알림

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SELF_HEALING_ENABLED` | `true` | 폴링 전 Notion 내부 API 세션을 점검하고 자동 복구 시도 |
| `SELF_HEALING_ADMIN_EMAIL` | `seokmogu@worxphere.ai` | 자동 복구 실패 시 Slack DM을 받을 관리자 이메일 |
| `SELF_HEALING_ALERT_COOLDOWN_SECONDS` | `900` | 동일 장애 Slack 알림 최소 간격(초) |

### 외부 Watchdog

폴링 프로세스가 아예 죽거나 재부팅 후 로드되지 않으면 프로세스 내부 self-healing도 실행되지 않습니다. `watchdog`은 별도 launchd job에서 실행되어 poller 프로세스와 로그 fresh 상태를 감시하고 Slack으로 관리자에게 알립니다. 경고 후 프로세스와 로그가 다시 정상이 되면 복구 DM을 한 번 보냅니다. 장기 네트워크 백오프 중에는 poller가 heartbeat 로그를 남겨 정상 대기를 로그 정체로 오판하지 않습니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `WATCHDOG_ADMIN_EMAIL` | `seokmogu@worxphere.ai` | poller 장애 알림을 받을 관리자 이메일 |
| `WATCHDOG_ALERT_COOLDOWN_SECONDS` | `900` | 동일 watchdog 장애 Slack 알림 최소 간격(초) |
| `WATCHDOG_POLL_STALE_SECONDS` | `300` | poll 로그가 이 시간 이상 갱신되지 않으면 장애로 판단 |
| `WATCHDOG_POLL_LOG_PATH` | `operations/logs/poll.err.log` | poller stderr 로그 경로 |
| `WATCHDOG_STATE_PATH` | `operations/logs/watchdog-state.json` | watchdog 알림 cooldown 상태 파일 |

### SSL / 네트워크

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NO_SSL_VERIFY` | `false` | SSL 검증 비활성화 (`1`로 설정). 사내 프록시 환경용 |
| `SSL_CA_FILE` | - | 커스텀 CA 인증서 경로. `NO_SSL_VERIFY` 대신 권장 |

### 폴링 / 재시도

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NOTION_API_VERSION` | `2022-06-28` | Notion API 버전 헤더 |
| `REQUEST_POLL_INTERVAL_MS` | `15000` | 폴링 주기 (밀리초) |
| `REQUEST_POLL_LIMIT` | `10` | 한 번에 처리할 최대 요청 수 |
| `NETWORK_MAX_RETRIES` | `3` | 연속 네트워크 실패 허용 횟수 |
| `NETWORK_BACKOFF_SECONDS` | `3600` | 최대 실패 후 대기 시간 (초, 기본 1시간) |

전체 목록은 `.env.example` 참조.

## CLI 명령어

```bash
# 브라우저 세션 초기화 (최초 1회, 수동 로그인 필요)
notion-gateway auth

# 브라우저 세션 갱신
notion-gateway refresh

# 지속 폴링 실행 (메인 운영 모드)
notion-gateway poll

# 단건 처리 (1회 실행 후 종료)
notion-gateway process

# 특정 요청만 처리
notion-gateway process --request <page-id>

# 기존 완료 건의 연결 상태 재확인
notion-gateway check-connections

# 설정 및 연결 진단
notion-gateway doctor

# poll worker 외부 헬스체크 (poller와 별도 launchd job에서 실행)
notion-gateway watchdog

# 발급된 API Access 통합의 댓글 기능 권한 진단 (기본 dry-run)
notion-gateway comment-capabilities

# 기존 API Access 통합에 댓글 읽기/삽입 capability 일괄 반영
notion-gateway comment-capabilities --execute

# 디버그 로깅
notion-gateway -v poll
```


## 배포 전 체크리스트

### 1. 사전 준비

- [ ] Notion Internal Integration 생성 ([https://www.notion.so/my-integrations](https://www.notion.so/my-integrations))
  - 신청 DB에 대한 읽기/쓰기 권한 부여
  - 기본 기능: 콘텐츠 읽기/삽입/업데이트, 사용자 정보 읽기 활성화
  - 댓글 읽기/삽입은 신청 폼의 `댓글 권한 추가 요청` 체크박스가 선택된 발급 통합에만 추가
  - 토큰 값(`ntn_...`)을 `NOTION_GATEWAY_TOKEN`에 설정
- [ ] 신청용 Notion DB 생성 및 폼 연결
  - DB ID를 `NOTION_GATEWAY_REQUESTS_DATABASE_ID`에 설정
- [ ] (선택) Slack Bot 생성 ([https://api.slack.com/apps](https://api.slack.com/apps))
  - 필요 스코프: `chat:write`, `users:read.email`, `users:read`
  - Bot Token을 `NOTION_GATEWAY_SLACK_BOT_TOKEN`에 설정

### 2. Notion DB 스키마

신청 DB에 다음 속성(property)이 필요합니다:

| 속성 이름 | 타입 | 설명 |
|-----------|------|------|
| `조직명` | Title | 신청 조직명 |
| `신청 페이지 링크` | URL | 접근 권한 부여할 페이지 URL |
| `자동화 계정 권한 확인` | Checkbox | 신청 페이지가 `웍스피어 모든 사용자 편집허용`이 아닌 경우, 페이지 또는 상위 페이지에 `notion-automation@worxphere.ai`를 편집/Full access 권한자로 추가했는지 신청자가 확인 |
| `댓글 권한 추가 요청` | Checkbox | 선택값. 체크하면 발급 통합에 댓글 읽기/삽입 capability와 페이지 연결 role을 추가하고, 미체크면 기본 콘텐츠 권한만 발급 |
| `정규 페이지 ID` | Rich text | 정규화된 페이지 ID (자동 입력) |
| `신청자` | People | 신청자 (폼에서 자동 할당) |
| `상태` | Select | 처리 상태. 옵션: `Requested`, `Processing`, `Issued`, `완료`, `Failed` |
| `발급 토큰키` | Rich text | 발급된 API 토큰 (자동 입력) |
| `통합 이름` | Rich text | 생성된 통합 이름 (자동 입력) |
| `처리 오류` | Rich text | 에러 메시지 (자동 입력) |
| `신청일자` | Date | 신청 일시 |
| `처리 완료일시` | Date | 완료 일시 (자동 입력) |
| `연결 여부` | Checkbox | 페이지 연결 성공 여부 (자동 입력) |
| `재시도 횟수` | Number | 재시도 카운터 (자동 입력) |

### 3. 브라우저 인증 (필수, 최초 1회)

Playwright가 Notion 웹 UI를 제어하려면 관리자 계정으로 로그인된 브라우저 세션이 필요합니다.

```bash
# 브라우저 창이 열림 — 수동 로그인 또는 NOTION_EMAIL/NOTION_PASSWORD 자동 로그인
NOTION_HEADLESS=false notion-gateway auth
```

인증 후 `data/notion-browser-profile/` 디렉토리에 세션이 저장됩니다. 이 디렉토리를 운영 머신에 복사하거나, 운영 머신에서 직접 `auth`를 실행하세요.

원격 서버에서 `auth`를 실행하려면:
- X11 포워딩: `ssh -X user@server` 후 `NOTION_HEADLESS=false notion-gateway auth`
- VNC/원격 데스크톱 사용

폴링 중 세션은 **1시간마다 자동 갱신**됩니다. 세션이 만료되면 수동으로 `notion-gateway auth`를 다시 실행하세요.

### 4. 네트워크 요구사항

서비스가 접근해야 하는 외부 엔드포인트:

| 대상 | 포트 | 용도 |
|------|------|------|
| `api.notion.com` | 443 (HTTPS) | Notion REST API |
| `www.notion.so` | 443 (HTTPS) | 브라우저 자동화 (통합 생성) |
| `slack.com` | 443 (HTTPS) | Slack API (알림, 선택) |

### 5. SSL 인증서 설정

사내 프록시 환경에서 자체 서명 인증서를 사용하는 경우:

```bash
# 방법 1: 커스텀 CA 인증서 (권장)
SSL_CA_FILE=/path/to/corporate-ca-bundle.crt

# 방법 2: SSL 검증 비활성화 (임시 용도)
NO_SSL_VERIFY=1
```

### 6. 리소스 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| CPU | 1 vCPU | 2 vCPU |
| 메모리 | 512 MB | 1 GB |
| 디스크 | 500 MB | 1 GB |
| 네트워크 | 아웃바운드 HTTPS | 아웃바운드 HTTPS |

디스크는 Chromium 바이너리(~200MB) + 브라우저 프로필(~50MB)이 주요 사용량입니다.

## 운영

### Mac mini LaunchDaemon

맥미니에서는 사용자 로그인 세션에 묶이는 `LaunchAgent`가 아니라 root-owned `LaunchDaemon`으로 운영합니다. 실제 프로세스는 `UserName=agent`로 실행되므로 `/Users/agent/project/notion-api-gateway-py`의 `.env`, `data/storage-state.json`, 로그 파일을 그대로 사용합니다.

구성:

- `com.worxphere.notion-api-gateway`: `notion-gateway poll`, `KeepAlive=true`
- `com.worxphere.notion-api-gateway-watchdog`: `notion-gateway watchdog`, `StartInterval=300`

설치:

```bash
cd /Users/agent/project/notion-api-gateway-py
sudo deploy/bin/install-macmini-launchdaemons.sh

sudo launchctl print system/com.worxphere.notion-api-gateway
sudo launchctl print system/com.worxphere.notion-api-gateway-watchdog
tail -f operations/logs/poll.err.log
```

검증:

```bash
notion-gateway doctor
notion-gateway watchdog
pgrep -af "notion-gateway poll"
```

재부팅 후에는 로그인 없이도 system launchd가 poller를 올립니다. poller가 crash하면 `KeepAlive`가 재시작하고, poller가 없거나 heartbeat를 포함한 로그가 5분 이상 멈추면 watchdog이 `WATCHDOG_ADMIN_EMAIL`로 Slack DM을 보냅니다. 장애 경고가 실제 전송된 뒤 건강 상태가 확인되면 정상 복구 DM을 한 번 보냅니다.

### 모니터링

- `notion-gateway doctor` — Notion API 연결, DB 접근, Slack 연결 진단
- `notion-gateway watchdog` — poller 프로세스 존재와 로그 fresh 상태 진단, 장애 및 정상 복구 시 Slack DM
- 폴링 루프는 요청 처리 전에 Notion 내부 API 세션을 점검하고, 실패 시 세션 refresh와 persistent profile 기반 재저장을 자동 시도합니다.
- 자동 복구가 실패하면 `SELF_HEALING_ADMIN_EMAIL`로 Slack DM을 보내고 해당 주기 요청 처리를 건너뜁니다. 이후 내부 API가 정상이 되면 복구 DM을 한 번 보냅니다.
- 로그 출력은 stdout/stderr로 전달되므로 컨테이너 로그 또는 journalctl로 확인
- `-v` 플래그로 디버그 로깅 활성화

### Graceful Shutdown

`SIGINT` 또는 `SIGTERM` 시그널로 안전하게 종료됩니다. 현재 처리 중인 요청이 있으면 완료 후 종료합니다.

### 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `auth` 후에도 통합 생성 실패 | 브라우저 세션 만료 | `notion-gateway auth` 재실행 |
| 관리자에게 자동 복구 실패 DM 수신 | 저장된 브라우저 세션과 persistent profile 모두 만료 | 맥미니에서 `notion-gateway auth` 재실행 후 `doctor` 확인 |
| Slack 알림 미발송 | Bot Token 미설정 또는 스코프 부족 | `NOTION_GATEWAY_SLACK_BOT_TOKEN` 확인, 스코프 확인 |
| SSL 에러 | 사내 프록시 인증서 문제 | `SSL_CA_FILE` 설정 또는 `NO_SSL_VERIFY=1` |
| "bot detected" 에러 | Notion 봇 감지 차단 | 잠시 후 재시도, 필요 시 수동 `auth` |
| 재시도 3회 초과 후 중단 | 반복 실패 | `doctor`로 진단 후 원인 해결, DB에서 재시도 횟수 초기화 |
| 폴링 중 1시간 대기 | 네트워크 연속 실패 | 네트워크 연결 확인, `NETWORK_BACKOFF_SECONDS` 조정 |

## 아키텍처

- **Playwright** — Notion 웹 UI 자동화 (통합 생성, 토큰 복사, 페이지 연결)
- **httpx** — Notion REST API (DB 조회/수정, 페이지 접근 검증)
- **Pydantic** — 설정 검증 및 데이터 모델
- **Slack API** — DM 알림 (신청 접수, 발급 완료, 발급 실패)

### 브라우저 세션 관리

로컬 Chromium을 사용하며 `storage-state.json`으로 쿠키를 복원합니다. 폴링 중에는 1시간마다 자동 세션 갱신을 시도합니다.

### 프로젝트 구조

```
src/notion_gateway/
├── __init__.py              # 패키지 (version: 2.0.0)
├── __main__.py              # CLI 진입점 (auth, poll, process, doctor 등)
├── config.py                # Pydantic Settings 기반 환경변수 검증
├── types.py                 # 데이터 모델, 예외 정의
├── doctor.py                # 진단 유틸리티
└── services/
    ├── notion_api.py        # Notion REST API 클라이언트 (httpx, 지수 백오프 재시도)
    ├── notion_browser.py    # Playwright 브라우저 자동화
    ├── notion_records.py    # DB 레코드 파싱, 상태 관리
    ├── request_processor.py # 메인 폴링 루프, 요청 처리 오케스트레이션
    ├── self_healing.py      # 내부 API 세션 자동 복구 및 관리자 Slack 에스컬레이션
    ├── notifier.py          # 알림 라우팅
    ├── slack_notifier.py    # Slack API 연동
    └── page_id.py           # 페이지 ID 파싱/정규화
```

### 핵심 설계

- **Async-first** — 모든 I/O가 `async/await` 기반 (httpx, Playwright async API)
- **2단계 브라우저 세션** — Ephemeral context(빠른 시작) → Persistent context(복구 대체)
- **멱등성 보장** — 알림 중복 방지 가드, 상태 재확인 후 완료 처리
- **Graceful shutdown** — SIGINT/SIGTERM 핸들링으로 안전 종료

## 개발

```bash
# 테스트
pytest

# 린트
ruff check src/

# 포맷
ruff format src/
```

## License

MIT
