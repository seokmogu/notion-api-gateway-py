# Notion API Gateway (Python)

Notion API token provisioning automation service.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
cp .env.example .env  # Configure your credentials
```

## Usage

```bash
# Bootstrap browser session (interactive login)
notion-gateway auth

# Run continuous polling loop
notion-gateway poll

# Process a single request
notion-gateway process --request <page-id>

# Refresh browser session
notion-gateway refresh

# Run diagnostics
notion-gateway doctor
```

## Environment Variables

See `.env.example` for all configuration options.
