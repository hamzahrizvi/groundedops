# Packaging GroundedOps as a Windows app

Produces `GroundedOps-Setup-16.3.exe` — an installer that puts the console
on another machine, runs it from the Start menu, and optionally keeps it
running in the background so other machines on the network can reach it.

## Build

From the **repo root**, not from here:

```
.venv\Scripts\pip.exe install pyinstaller pystray
.venv\Scripts\pyinstaller.exe packaging\groundedops.spec --noconfirm
iscc packaging\installer.iss
```

`iscc` is [Inno Setup](https://jrsoftware.org/isdl.php). The first command is
the long one: **15–30 minutes and roughly 2 GB** in `dist\GroundedOps\`.
`pystray` is optional and only adds the tray icon; without it the app still
runs, it just has no tray menu.

### Why it is that big

`torch` is 533 MB on its own, plus `transformers` at 115 MB and `scipy` at
116 MB. They are there for the local embedding and reranking models, which
is what makes the offline mode work. Dropping them means dropping that, so
the size is the feature.

The spec builds **one folder, not one file**, on purpose: a `--onefile` build
of this size unpacks ~2 GB to a temp directory on *every* launch, which adds
tens of seconds to each start and makes the background mode pointless. The
installer is what turns the folder into a single download.

## What the installer offers

| Option | Effect |
|---|---|
| Desktop shortcut | Normal app launch; opens the console in the browser |
| Run in the background at startup | Adds a `--headless` Run entry, so it starts with Windows and opens no browser |
| Allow other machines to reach it | Opens TCP 8000 in the firewall |

Uninstalling removes the program and the firewall rule but **not the data** —
that directory holds the indexed documents, the accounts and the curated
answers, and a spent afternoon uninstalling should not cost weeks of filing.

## Where data lives

`%LOCALAPPDATA%\GroundedOps\` — index, documents, config, and the model
cache. Deliberately outside the install directory: a frozen app's own folder
is a temp directory that is wiped on exit, and keeping the path stable across
versions is what lets an upgrade keep its corpus.

Override with `GROUNDEDOPS_DATA` to point somewhere else (a network share, a
second disk).

## Running it

```
GroundedOps.exe                 start, open the console, sit in the tray
GroundedOps.exe --headless      start silently (what the startup task uses)
GroundedOps.exe --local         bind 127.0.0.1 only; nothing else can reach it
GroundedOps.exe --port 9000     another port
```

It binds `0.0.0.0` by default, so with the firewall rule in place the console
and the embeddable widget are both reachable at `http://<machine-ip>:8000`
from anywhere on the LAN. A second copy detects the first and opens the
browser at it instead of failing on the port.

**First start downloads the language models** and takes a few minutes on a
cold machine — that is why the window is not hidden. It needs internet once;
after that it runs offline. To ship it genuinely offline, copy a populated
`%LOCALAPPDATA%\GroundedOps\models\` onto the target machine before the first
run.

## Verified

The launcher itself was run from source before packaging: it serves `/admin`
in about 30 seconds, binds `0.0.0.0`, is reachable on the LAN address, and
creates every store inside its data directory rather than next to the
executable. What remains untested is PyInstaller's own bundling — expect to
add `hiddenimports` to the spec if a module turns up missing at runtime,
which is normal for this stack.
