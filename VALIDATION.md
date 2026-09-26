# Toolserver validation — 2026-09-14

Tested the running service through HTTPS at `toolserver.hugpy.ai`, using certificate
verification and explicit source-address binding. No credentials were logged.

## Login

- POST `/gpt/login_start` returned HTTP 200 and `{ok: true, state: authenticated}`.
- `/gpt/oauth_status` returned HTTP 200 and `authenticated: true` for
  `/srv/vm_mgr/.codex`.
- This confirms recognition of the existing service-account login. A fresh browser
  approval cycle and model inference were not performed.

## Access-control parity

All four routes were tested under each condition below (16 passing checks):
`/claude/oauth_status`, `/gpt/oauth_status`, `/gpt/login_start`, `/gpt/login_poll`.

| Source and credentials | Expected and observed |
| --- | --- |
| LAN 192.168.1.100, valid operator token | 200 |
| LAN 192.168.1.100, no operator token | 401 |
| Loopback source through HTTPS, valid operator token | 403 |
| Same disallowed source, spoofed X-Forwarded-For and X-Real-IP with LAN address | 403 |

`/etc/nginx/sites-enabled/hugpy.ai.toolserver.conf` enforces the same server-level
allowlist for both categories: 192.168.1.0/24 (LAN), 192.168.2.0/24 (VPN), then deny
all. One shared location proxies every route. No nginx changes were needed.
Gunicorn listens only on 127.0.0.1:7004. The VPN range was verified in configuration;
a live request from that range was not available on this host.

These IP restrictions belong to this deployment's nginx configuration, not to the
portable Python package. Other deployments must supply equivalent proxy controls
and toolserver authentication. Publishing the wheel alone does not install them.

PyPI publishing was not performed.
