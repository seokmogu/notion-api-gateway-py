# 클라우드 배포

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
