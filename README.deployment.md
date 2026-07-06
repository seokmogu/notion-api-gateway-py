# 클라우드 배포

## Mac mini LaunchDaemon

맥미니 장기 운영은 사용자 로그인 세션에 의존하는 `LaunchAgent`가 아니라 system `LaunchDaemon`으로 구성합니다. plist는 root-owned로 `/Library/LaunchDaemons`에 설치하고, 실제 worker 프로세스는 `UserName=agent`로 실행합니다.

```bash
cd /Users/agent/project/notion-api-gateway-py
sudo deploy/bin/install-macmini-launchdaemons.sh
```

설치되는 job:

| Label | 역할 | 복구 방식 |
| --- | --- | --- |
| `com.worxphere.notion-api-gateway` | `notion-gateway poll` 장기 실행 | `KeepAlive=true`, crash/reboot 후 재기동 |
| `com.worxphere.notion-api-gateway-watchdog` | `notion-gateway watchdog` 5분 주기 실행 | poller 미실행/로그 정체 시 Slack DM |

검증:

```bash
sudo launchctl print system/com.worxphere.notion-api-gateway
sudo launchctl print system/com.worxphere.notion-api-gateway-watchdog
pgrep -af "notion-gateway poll"
tail -f operations/logs/poll.err.log
uv run notion-gateway watchdog
uv run notion-gateway comment-capabilities
```

운영 전제:

- `/Users/agent/project/notion-api-gateway-py/.env`에 gateway 전용 Notion/Slack secrets가 있어야 합니다.
  `NOTION_GATEWAY_*` 변수를 우선 사용하고, 기존 `NOTION_*`/`SLACK_BOT_TOKEN`은 fallback으로만 둡니다.
- `data/storage-state.json`은 `notion-gateway auth`로 미리 갱신되어 있어야 합니다.
- 새로 발급되는 API Access 통합은 신청 폼의 `댓글 권한 추가 요청`이 체크된 경우에만
  댓글 읽기/삽입 capability를 포함합니다. 기존 통합에 댓글 권한을 소급 추가해야 할 때는
  `uv run notion-gateway comment-capabilities`로 dry-run 진단 후, 승인된 작업 창에서
  `uv run notion-gateway comment-capabilities --execute`로 보정합니다.
- watchdog Slack 알림은 `WATCHDOG_ADMIN_EMAIL`과 `NOTION_GATEWAY_SLACK_BOT_TOKEN`을 사용합니다.

## Docker

프로젝트에 Dockerfile이 포함되어 있지 않습니다. 아래 Dockerfile을 참고하세요.

```dockerfile
FROM python:3.12-slim

# Playwright 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 설치
COPY pyproject.toml uv.lock ./
RUN uv venv && uv pip install --no-cache .

# Playwright Chromium 설치
RUN uv run playwright install chromium

# 소스 코드 복사
COPY src/ src/

# 브라우저 프로필 볼륨
VOLUME ["/app/data"]

# 환경변수 기본값
ENV NOTION_BROWSER_PROFILE_DIR=/app/data/notion-browser-profile
ENV NOTION_HEADLESS=true

ENTRYPOINT ["uv", "run", "notion-gateway"]
CMD ["poll"]
```

```bash
# 빌드
docker build -t notion-api-gateway .

# 실행 (환경변수 파일 사용)
docker run -d \
  --name notion-gateway \
  --env-file .env \
  -v notion-gateway-data:/app/data \
  --restart unless-stopped \
  notion-api-gateway

# 초기 인증 (최초 1회, 브라우저 표시 필요)
docker run -it --rm \
  --env-file .env \
  -e NOTION_HEADLESS=false \
  -v notion-gateway-data:/app/data \
  notion-api-gateway auth

# 로그 확인
docker logs -f notion-gateway
```

## Docker Compose

```yaml
version: "3.8"
services:
  notion-gateway:
    build: .
    env_file: .env
    environment:
      NOTION_BROWSER_PROFILE_DIR: /app/data/notion-browser-profile
      NOTION_HEADLESS: "true"
    volumes:
      - gateway-data:/app/data
    restart: unless-stopped
    # 헬스체크: doctor 명령으로 연결 상태 확인
    healthcheck:
      test: ["CMD", "uv", "run", "notion-gateway", "doctor"]
      interval: 5m
      timeout: 30s
      retries: 3

volumes:
  gateway-data:
```
