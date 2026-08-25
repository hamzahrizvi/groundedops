# Deploying to staging

For putting GroundedOps on an internet-reachable staging host and testing it
internally, with the widget embedded on a WordPress site.

Read this in order. Steps 1–3 must happen **before** the host is reachable
from the internet — step 3 in particular, because the first-run setup
endpoint has to be unauthenticated and whoever reaches it first owns the
install.

---

## What changes when it leaves the LAN

Until now the admin console was unreachable by design. `main.py`'s surface
guard treats the presence of `X-Forwarded-For` / `X-Real-IP` /
`CF-Connecting-IP` as proof a request came from outside and 404s everything
except `/widget/*` and `/health`. That is still the default, and behind any
reverse proxy it means **the console returns 404 with no way to open it**.

That default was written when the admin surface had one shared password and
no accounts. It now has real accounts with levels, so opening it for internal
testing is reasonable — but it stays a deliberate act:

- `ADMIN_ALLOWED_IPS` — comma-separated IP **prefixes** allowed to reach the
  admin surface from outside. Empty (default) keeps it LAN-only.
- It is an allowlist **in front of** authentication, not instead of it. A
  request from an allowed address still has to sign in.

The alternative, and the better one if you have it: leave
`ADMIN_ALLOWED_IPS` empty and reach the console over VPN or Cloudflare
Access, so it never has a public door at all.

---

## 1. Fill in the environment

```bash
cd docker
cp .env.example .env
```

Generate the two secrets:

```bash
openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 32   # WIDGET_TOKEN_SECRET
```

Minimum to boot: `SESSION_SECRET`, `WIDGET_TOKEN_SECRET`, and one provider
key. Compose **refuses to start** without `SESSION_SECRET` rather than
defaulting it — a shared default signing key would mean a token minted
anywhere is valid here.

Also set, for a proxied deployment:

| Variable | Value | Why |
|---|---|---|
| `WIDGET_ALLOWED_ORIGINS` | `https://staging.yourcompany.com` | The widget runs on the WordPress page, so this lists **that** origin. Left at `*` it is a dev default that should not survive. |
| `TRUST_PROXY` | `1` | So per-IP quotas and `ADMIN_ALLOWED_IPS` see the real caller. Only with a proxy you control in front — otherwise the header is forgeable. |
| `ENABLE_HSTS` | `1` | TLS terminates at the proxy; this asks browsers to stay on HTTPS. |
| `ADMIN_ALLOWED_IPS` | your office egress IP, or empty | See above. |

## 2. Bring the documents

The vector index is derived and disposable; the documents are the durable
asset. `documents/` is **not in git** and is the only copy of some source
PDFs (see `PENDING.md`), so copy it to the staging host out of band:

```bash
rsync -av documents/ staging:/srv/groundedops/documents/
```

Compose bind-mounts it read-write at `/data/documents`, so the originals live
on the host and survive the container.

## 3. Create the root account — before exposing the host

Do this on the box. It needs no token and no exposed setup endpoint:

```bash
docker compose up -d backend
docker compose exec backend python manage_accounts.py create you@company.com --level root
```

Then build the index from the documents:

```bash
docker compose exec backend python reindex.py --from-store
```

`reindex.py --check` reports what it would do without writing.

**Why before exposing:** `POST /admin/auth/bootstrap` cannot require
authentication — there is no account yet to authenticate against. Once a
root account exists it refuses permanently. Creating it here closes that
window while the host is still private.

If you must do first-run setup over the network instead, set
`BOOTSTRAP_TOKEN` and pass it as an `X-Bootstrap-Token` header; external
bootstrap requests are refused without it. Clear it once the account exists.

## 4. Start everything and check

```bash
docker compose up -d
curl -fsS https://staging.yourcompany.com/health
```

Then, from an allowlisted address, open `/admin`, sign in, and:

- **API keys** — confirm a provider key is loaded, or paste one in. Takes
  effect on the next request; no restart.
- **Access & limits** — decide whether guests get AI. It is **off**, which
  means signed-out visitors get reviewed FAQs only. Leaving it off is the
  right call for a first staging pass: it keeps public cost exposure and the
  public prompt-injection surface at zero while you test everything else.
- **Widget design** — set the branding, and the **Send to** address plus the
  phone number on each of the sales and support forms.
- **Test chat** — the real widget on a stand-in page. Toggle
  *Viewing as guest* / *Viewing as signed-in* to see both tiers.

## 5. Point WordPress at it

The plugin is `src/widget/groundedops-widget.php`. Zip it as
`groundedops-widget/groundedops-widget.php`, upload under
**Plugins → Add New → Upload Plugin**, activate.

In `wp-config.php`, above *"That's all, stop editing"*:

```php
define('GROUNDEDOPS_API',    'https://staging.yourcompany.com');
define('GROUNDEDOPS_SECRET', 'the same value as WIDGET_TOKEN_SECRET');
```

`GROUNDEDOPS_SECRET` **must** equal `WIDGET_TOKEN_SECRET`. If they differ,
every signed-in visitor is silently treated as a guest — which presents as
"the AI does not work" rather than as an auth mismatch.

Branding is **not** configured in the plugin. It used to hardcode
`data-agent-name="Support"` and `data-accent="#E4002B"`; those were removed
in v15.3 because an explicitly-present `data-*` attribute overrides the
server, so they silently defeated the console's Widget design page. What the
plugin still supplies is only what WordPress can know: the backend URL, the
visitor's token, and the sign-in URL.

### If the widget does not appear or does not answer

| Symptom | Likely cause |
|---|---|
| Nothing renders | `GROUNDEDOPS_API` wrong, or the script 404s. Check the browser console. |
| CORS error in console | `WIDGET_ALLOWED_ORIGINS` does not list the WordPress origin. |
| Signed-in users treated as guests | `GROUNDEDOPS_SECRET` ≠ `WIDGET_TOKEN_SECRET`. |
| Branding is not what the console says | A `data-*` attribute is still set in the plugin, or the page is cached. |
| Answers refuse everything | No provider key, or no documents indexed (step 2/3). |

To prove the backend independently of WordPress, mint a token on the server
and paste it into a plain HTML page:

```bash
docker compose exec backend python mint_token.py --tier member
```

If that works and WordPress does not, the fault is in the plugin or the
secret, not the backend.

---

## 6. Take a backup before you start changing things

Once the documents are indexed and the console is configured, take one:

```bash
docker compose exec backend python manage_backup.py export --out /data/first-good-state.gobk
```

It prompts for a passphrase and encrypts the archive. Put that passphrase in
your password manager **now** — not in `docker/.env` next to the backup, and
not in the same folder. There is no recovery path if it is lost, by design.
For an unattended cron backup, set `BACKUP_PASSPHRASE` in the environment.

Or from the console: **Backup → Download backup**. Support and above can
export; only root can restore.

That archive carries the documents, the search index, the FAQ, the
catalogue, the widget config, the enquiries and (for a root export) the
accounts — so restoring it does **not** re-ingest anything, which is the
slow part. Restoring the index needs a server restart, because Chroma holds
those files open.

It contains password hashes and customer contact details, which is why it is
encrypted. Still put it somewhere access-controlled — encryption is a second
line, not a reason to be casual — and schedule it, because a backup feature
nobody runs is not a backup.

---

## Still open, going in

Known and deliberate, so nobody discovers them as surprises:

- **Nothing is emailed.** Enquiries land in the console's Enquiries page.
  Every lead records its destination address and `notified: false`, pending
  SMTP credentials.
- **`/widget/lead` is unauthenticated and not rate limited** — it has to be
  public, and bots will find it. Put it behind the proxy's rate limiting or a
  WAF before this is anything other than internal staging.
- **No lockout on `/admin/login`.** scrypt makes each attempt cost ~50–100ms,
  which blunts brute force but is not a lockout.
- **Per-session caps are a cost guard, not a security boundary.** The session
  id comes from the visitor's browser. The daily per-visitor and per-IP
  ceilings are what bound a determined caller.
- **The grounding threshold (0.55) has never been swept**, so the
  false-refusal rate is unknown. Worth measuring with `eval.py` once a
  provider key is live.
- **Nothing schedules a backup yet.** Step 6 is a manual command. Put it on
  cron on the host before this carries anything you would miss.
