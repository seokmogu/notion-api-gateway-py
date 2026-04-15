# Deployment — business-automation EC2

Operational recipes for the `notion-gateway` poller running on the
`business-automation` EC2 instance (`i-0dbdd40c8efb59a07`, region `ap-northeast-2`).

## Runtime Layout

- User: `app-notion` (UID 992)
- Working dir: `/home/app-notion/notion-api-gateway-py`
- Python env: `uv` (binary at `/home/app-notion/bin/uv`)
- Session state: `data/storage-state.json` (Notion login cookies)
- Browser: AWS Bedrock AgentCore (`BROWSER_CONNECTION=remote-bedrock`)
- IAM: EC2 instance profile `AI-SVC-DEV-BUSINESS-AUTOMATION-EC2`

## First-Time Install

```bash
# 1. Copy the unit file
sudo cp deploy/notion-gateway.service \
    /home/app-notion/.config/systemd/user/notion-gateway.service
sudo chown -R app-notion:app-notion /home/app-notion/.config

# 2. Keep the user manager alive after logout
sudo loginctl enable-linger app-notion

# 3. Enable + start via the user manager
sudo systemctl --machine=app-notion@.host --user daemon-reload
sudo systemctl --machine=app-notion@.host --user enable notion-gateway
sudo systemctl --machine=app-notion@.host --user start notion-gateway
```

## Day-to-Day Operations

| Action | Command |
|---|---|
| Status | `sudo systemctl --machine=app-notion@.host --user status notion-gateway` |
| Restart | `sudo systemctl --machine=app-notion@.host --user restart notion-gateway` |
| Stop | `sudo systemctl --machine=app-notion@.host --user stop notion-gateway` |
| Follow logs | `sudo journalctl -t notion-gateway -f` |
| Last 100 logs | `sudo journalctl -t notion-gateway --no-pager -n 100` |

## Updating Code

```bash
sudo -u app-notion git -C /home/app-notion/notion-api-gateway-py pull --ff-only
sudo systemctl --machine=app-notion@.host --user restart notion-gateway
```

## Refreshing the Notion Session

Triggered when logs show `401 Unauthorized` on `/api/v3/*` or
`Session expired or unauthorized`. Re-logs in via the Bedrock browser and
rewrites `data/storage-state.json`.

```bash
sudo systemctl --machine=app-notion@.host --user stop notion-gateway

sudo -u app-notion env \
    AWS_REGION=ap-northeast-2 \
    HOME=/home/app-notion \
    PATH=/home/app-notion/bin:/usr/local/bin:/usr/bin:/bin \
    bash -c 'cd /home/app-notion/notion-api-gateway-py && uv run notion-gateway auth'

sudo systemctl --machine=app-notion@.host --user start notion-gateway
```

Auto-login uses `NOTION_EMAIL` / `NOTION_PASSWORD` from `.env`. If 2FA is
enabled on the bot account the command will fail; disable 2FA for the
bot account or run `auth` interactively with `BROWSER_CONNECTION=local`.

## Gotchas

- Do **not** use `sudo -u app-notion tmux ...` to inspect a running
  tmux server owned by `app-notion`. A socket-ownership mismatch can
  abort the tmux server (SIGABRT) and kill every child (including this
  poller). Use `sudo machinectl shell app-notion@` or SSH as the user.
- `systemctl --user` from `sudo -u` fails because no DBus session is
  set. Always use `--machine=app-notion@.host --user`.
- EC2 instance profile already grants the Bedrock AgentCore browser
  permissions (`Start/Stop/ConnectBrowserAutomationStream`). Only the
  `AWS_REGION` env var is needed in the unit file.

## Required IAM (for reference)

The EC2 role has an inline policy with:

```json
{
  "Action": [
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:StopBrowserSession"
  ],
  "Resource": "arn:aws:bedrock-agentcore:*:aws:browser/aws.browser.v1"
},
{
  "Action": "bedrock-agentcore:ConnectBrowserAutomationStream",
  "Resource": "*"
}
```
