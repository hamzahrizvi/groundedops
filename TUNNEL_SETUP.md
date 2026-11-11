# Named Cloudflare tunnel (fixed public address)

A quick tunnel gets a new `*.trycloudflare.com` address on every restart,
which breaks the WordPress plugin's baked-in address. A named tunnel keeps
one hostname forever. One-time setup, on the machine that will serve
(PENDING.md 9.1: a company server, not yet provisioned).

Needs: a Cloudflare account that manages the DNS zone (e.g.
`example.com`), and `cloudflared`
(`winget install --id Cloudflare.cloudflared`).

```powershell
cloudflared tunnel login                     # browser; writes ~\.cloudflared\cert.pem
cloudflared tunnel create chat.example.com
                                             # writes ~\.cloudflared\<UUID>.json
cloudflared tunnel route dns chat.example.com chat.example.com
```

Name the tunnel **exactly the hostname**: `serve.ps1` runs
`cloudflared tunnel run --url http://127.0.0.1:<port> <TUNNEL_HOSTNAME>`, so
the value is used as the tunnel name as well as the address.

Then add one line to `src\.env`:

```
TUNNEL_HOSTNAME=chat.example.com
```

and install the service as the same Windows user that ran `login`
(the credentials live in that user's `~\.cloudflared`):

```powershell
.\serve.ps1 -Install        # must say "starts at boot, no sign-in needed" (S4U)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
.\serve.ps1 -Status
```

With `TUNNEL_HOSTNAME` set, the backend listens on `127.0.0.1` only: the
tunnel is the way in, and other machines on this network cannot reach
`host:8000` directly. `logs\service.log` says `backend started ... on
127.0.0.1:8000`. To open it to the LAN anyway, install with
`.\serve.ps1 -Install -Lan`; for the console from another machine, prefer
the tunnel URL with `ADMIN_ALLOWED_IPS` or Cloudflare Access (below).

Check `logs\service.log` says `starting NAMED tunnel`, not `QUICK`, and
`https://<host>/health` answers `{"status":"ok"}`.

## Go-live checklist (from STAGING.md "Still open")

- Re-export the WordPress plugin from the console once the named address
  is live (the export bakes the address in); `php -l` the exported file.
- Single uvicorn worker only: every JSON store uses a process-local lock.
- Console access: set `ADMIN_ALLOWED_IPS` or put Cloudflare Access in front
  of `/admin*`. Through the tunnel every request looks external, so the
  console 404s without one of these.
- Who is calling: CF-Connecting-IP is believed only from this box, the LAN
  (`PRIVATE_NETWORKS`) or an address in `TRUSTED_PROXIES` (comma-separated addresses or
  ranges). The named tunnel needs nothing. If anything else sits in front (the website
  relaying widget calls, an nginx, Cloudflare proxying to an open port), list its address in
  `TRUSTED_PROXIES`, and set `TRUST_PROXY=1` if it passes the visitor in X-Forwarded-For;
  otherwise every visitor shares one per-IP limit (30 leads a day by default).
- Cloudflare rate limiting / WAF rule on `/widget/lead` and `/admin/login`.
- Default role pinned to DeepSeek on the public instance, so a gateway
  outage is not read as hosting downtime.
- Nightly backup re-registered on the new host:
  `.\backup_daily.ps1 -Install -Dest <second disk>`; `BACKUP_PASSPHRASE`
  stored in the password manager before `BACKUP_ALLOW_PLAINTEXT` is removed.
- External uptime pinger (>= 5 min): alert on plain `/health` failing
  ("is it up"). `/health?deep=1` (`provider_reachable`, "is the provider
  up") answers only on the box or the LAN, or through the tunnel with
  `Authorization: Bearer <staff token>` (mint one with
  `quota.issue_token("uptime", "staff", ttl_seconds=...)`; a website member token gets a 404); without it, it is a 404.
  Never restart on the deep one.
