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

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
cp .env.example .env  # 환경변수 설정
```

### 요구사항

- Python 3.12+
- Chromium (Playwright)
- Notion API 토큰 (Internal Integration)
- Slack Bot Token (선택)

## 사용법

```bash
# 브라우저 세션 초기화 (최초 1회, 수동 로그인)
notion-gateway auth

# 지속 폴링 실행
notion-gateway poll

# 단건 처리
notion-gateway process

# 특정 요청 처리
notion-gateway process --request <page-id>

# 브라우저 세션 갱신
notion-gateway refresh

# 설정 및 연결 진단
notion-gateway doctor
```

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `NOTION_TOKEN` | O | Notion API 토큰 (Internal Integration) |
| `NOTION_REQUESTS_DATABASE_ID` | O | 신청 DB ID |
| `NOTION_WORKSPACE_NAME` | - | 워크스페이스 이름 (통합 생성 시 선택) |
| `NOTION_EMAIL` / `NOTION_PASSWORD` | - | 자동 로그인용 (SSO 미지원) |
| `SLACK_BOT_TOKEN` | - | Slack DM 알림용 Bot Token |
| `NO_SSL_VERIFY` | - | SSL 검증 비활성화 (`1`로 설정) |
| `SSL_CA_FILE` | - | 커스텀 CA 인증서 경로 |
| `REQUEST_POLL_INTERVAL_MS` | - | 폴링 간격 (기본: 15000ms) |

전체 목록은 `.env.example` 참조.

## 아키텍처

- **Playwright** — Notion 웹 UI 자동화 (통합 생성, 토큰 복사, 페이지 연결)
- **httpx** — Notion REST API (DB 조회/수정, 페이지 접근 검증)
- **Pydantic** — 설정 검증 및 데이터 모델
- **Slack API** — DM 알림 (신청 접수, 발급 완료, 발급 실패)

### 브라우저 세션 관리

- **Ephemeral context** — `storage-state.json`으로 쿠키 복원 (빠른 시작)
- **Persistent context** — 브라우저 프로필 디렉토리로 자동 복구 (세션 만료 대응)
- 1시간마다 자동 세션 갱신

### 상태 흐름

```
Requested → Processing → Issued → 완료
                ↓
              Failed (최대 10회 재시도)
```

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
