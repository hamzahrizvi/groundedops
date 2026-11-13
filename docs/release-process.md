# Releasing to the server

How work gets from the office to the chatbot support staff and customers
actually use. Set by the CEO, 2026-11-13.

The server is `10.10.15.170` (see [linux-server.md](linux-server.md)). It
runs whatever is on `experimental/v17.3` **on GitHub** — pushing is what
makes a change releasable, and nothing reaches the server until someone runs
the update.

---

## 1. Every change is one of three things

The PO categorises each item as it comes in, from office work orders or
customer-reported issues.

| | What it is | When it ships |
|---|---|---|
| **Major fix** | Something wrong with the version on the server now, too serious to sit for a week: wrong answers customers see, a route down, data at risk, a security hole. | **Raises an alarm.** Goes to the CEO as an urgent update. The CEO decides: push now, or defer it into the weekly. |
| **Minor fix** | Something wrong, but it can wait a few days without anyone being harmed by the delay. | The weekly update. |
| **Feature** | New behaviour, not a correction. | **Never in a weekly update.** Accumulates for the next feature release. |

**The test for "does this belong in a weekly update" is whether it affects
the release that is on the server right now.** A fix to something only
present on a dev branch is not a weekly candidate — it ships when its
feature ships.

A major fix is an alarm, not a judgement about effort. A one-line change
can be major; a week of work can be minor.

---

## 2. Cadence

- **Weekly:** the major and minor fixes accumulated that week. Fixes only.
- **Feature release:** when the PO judges enough features are ready. Fixes
  waiting at the time go out with it.
- **Urgent:** a major fix the CEO chooses to push immediately, on its own.

---

## 3. Version numbers

`VERSION` at the repo root. It reads `17.0` today.

- **Weekly fix release** → patch: `17.0` → `17.0.1` → `17.0.2`
- **Feature release** → minor: `17.0.2` → `17.1`

Bump it **after** the server is updated and verified, not before: the number
records what is actually serving, not what was intended.

> The CEO's instruction was "move the minor version number after moving
> features to the server". The patch digit for weekly fix releases is this
> document's reading of that, so that a week of fixes is still
> distinguishable from the feature release before it. Say if you'd rather
> weekly fixes left the number alone.

---

## 4. Doing a release

On the server, as a sudo user:

```bash
sudo bash ~/setup.sh
```

That is the whole update. It **backs up before it changes anything** and
refuses to continue if the backup fails, so there is always a way back. It
records the move in `/srv/groundedops/releases.log`.

Then check it:

```bash
curl -s 'http://127.0.0.1:8000/health?deep=1'     # must say "ready": true
```

Ask it two or three real questions through `/admin` → **Test chat** before
telling anyone it is done. Allow a minute after the restart: until the
models are warm the bot asks clarifying questions instead of answering, and
`ready` stays false.

Then bump `VERSION`, and record in `PENDING.md` what went out.

---

## 5. Going back

`/srv/groundedops/releases.log` holds every update as `old -> new`.

**Code only** (the usual case):

```bash
sudo -u groundedops git -C /srv/groundedops/app checkout <old-sha>
sudo systemctl restart groundedops
```

**Code and data** (a bad reindex, a wrong bulk edit) — also restore the
backup taken just before the update, which is the newest file in
`/srv/groundedops/backups/`:

```bash
sudo -u groundedops -H bash -c 'cd /srv/groundedops/app/src && ../.venv/bin/python manage_backup.py restore /srv/groundedops/backups/<file> --accounts'
sudo systemctl restart groundedops
```

---

## 6. Who decides what

- **PO** — categorises every item, keeps the weekly list, and decides which
  features go into the next feature release.
- **CEO** — decides whether a major fix is pushed now or deferred to the
  weekly.

---

## 7. Known gap

Nothing automatically raises the alarm for a major fix yet: it depends on
the PO marking an item major and telling the CEO. If major fixes start
getting missed, that is the thing to automate first.
