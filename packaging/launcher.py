"""GroundedOps desktop launcher — the entry point the packaged .exe runs.

WHAT THIS SOLVES THAT `python main.py` DOES NOT

  1. A frozen app's own folder is read-only (PyInstaller unpacks to a temp
     directory that is wiped on exit). Every store this app writes -- the
     index, the documents, the accounts file -- therefore has to live
     somewhere else, and it has to be the SAME somewhere else after an
     upgrade or the install silently loses its data. %LOCALAPPDATA% it is,
     set below before anything imports the app.

  2. The models are downloaded at first run by sentence-transformers, into
     whatever cache the library defaults to. Left alone that is the user's
     profile, outside our data directory and invisible to backup; pointed
     here it sits with everything else.

  3. It binds 0.0.0.0, not 127.0.0.1, so the console and the widget are
     reachable from other machines on the LAN -- which is the whole point
     of leaving it running in the background.

Run modes:
    groundedops.exe              start, open the console, sit in the tray
    groundedops.exe --headless   start and stay silent (for Task Scheduler)
    groundedops.exe --port 9000  another port
    groundedops.exe --local      bind 127.0.0.1 only, no LAN
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

APP_NAME = "GroundedOps"
DEFAULT_PORT = 8000


def data_root() -> str:
    base = os.environ.get("GROUNDEDOPS_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA")
        or os.path.expanduser("~/.local/share"), APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def configure_paths(root: str) -> None:
    """Point every store at the writable data directory.

    setdefault throughout: an operator who has already set one of these in
    the environment meant it, and a launcher that overrides real config is
    worse than one that does nothing.
    """
    def d(*parts):
        p = os.path.join(root, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    docs, files, models = d("documents"), d("source_files"), d("models")
    cfg = d("config")

    pairs = {
        "CHROMA_DIR": os.path.join(root, "chroma_db"),
        "SOURCE_FILE_DIR": files,
        "DOCS_DIR": docs,
        "ACCOUNTS_PATH": os.path.join(cfg, "accounts.json"),
        "CATALOG_CONFIG": os.path.join(cfg, "catalog_config.json"),
        "POLICY_PATH": os.path.join(cfg, "policy.json"),
        "FAQ_STORE_PATH": os.path.join(cfg, "faq_store.json"),
        "FAQ_GAP_PATH": os.path.join(cfg, "faq_gaps.json"),
        "WIDGET_CONFIG_PATH": os.path.join(cfg, "widget_config.json"),
        "WIDGET_LEADS_PATH": os.path.join(cfg, "widget_leads.json"),
        "CONVO_DB_PATH": os.path.join(root, "conversations.db"),
        "QUOTA_DB_PATH": os.path.join(root, "quota.db"),
        # Model cache: with these unset the weights land in the user profile,
        # outside the data directory and outside any backup of it.
        "HF_HOME": models,
        "SENTENCE_TRANSFORMERS_HOME": models,
        "TRANSFORMERS_CACHE": models,
    }
    for k, v in pairs.items():
        os.environ.setdefault(k, v)


def app_src() -> str:
    """Where the application package lives.

    Two layouts. Frozen, PyInstaller unpacks the tree into sys._MEIPASS and
    the spec places the app under src/ there. From source, this file sits in
    packaging/ NEXT TO src/, not above it -- resolving "src" relative to
    this file's own directory looks for packaging/src, which is how the
    first run of this launcher died with ModuleNotFoundError: main.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "src")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "src")


def lan_ip() -> str:
    """This machine's address on the LAN, for the URL we print. Uses a UDP
    socket to a public address so the OS picks the interface it would really
    route over; nothing is sent."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def already_running(port: int) -> bool:
    """A second copy would fail on the port anyway, with a stack trace
    instead of an explanation. Check first and say so."""
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def wait_until_up(port: int, timeout: float = 600.0) -> bool:
    """First start loads the embedding and reranker models, which on a cold
    machine is minutes, not seconds. Poll rather than guess."""
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(0.5)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            s.close()
        time.sleep(1.0)
    return False


def tray(url: str, stop) -> bool:
    """A tray icon, if pystray is available. Optional on purpose: the app
    must run without it rather than refuse to start over an icon."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return False

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse((4, 4, 60, 60), fill=(37, 39, 40, 255))
    dr.ellipse((18, 18, 46, 46), outline=(183, 122, 53, 255), width=7)

    icon = pystray.Icon(
        APP_NAME, img, APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("Open console", lambda: webbrowser.open(url), default=True),
            pystray.MenuItem("Copy address", lambda: None, enabled=False),
            pystray.MenuItem(url, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: (icon.stop(), stop())),
        ))
    threading.Thread(target=icon.run, daemon=True).start()
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="groundedops")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", DEFAULT_PORT)))
    ap.add_argument("--local", action="store_true",
                    help="bind 127.0.0.1 only; no other machine can reach it")
    ap.add_argument("--headless", action="store_true",
                    help="do not open a browser (for Task Scheduler / startup)")
    args = ap.parse_args()

    root = data_root()
    configure_paths(root)

    host = "127.0.0.1" if args.local else "0.0.0.0"
    url = f"http://127.0.0.1:{args.port}/admin"
    lan = f"http://{lan_ip()}:{args.port}/admin"

    if already_running(args.port):
        print(f"{APP_NAME} is already running on port {args.port}.")
        if not args.headless:
            webbrowser.open(url)
        return 0

    # Imported only now: configure_paths() must have run first, because these
    # modules resolve their storage locations at import time.
    sys.path.insert(0, app_src())
    import uvicorn                      # noqa: E402
    from main import app                # noqa: E402

    print(f"{APP_NAME}")
    print(f"  data       {root}")
    print(f"  console    {url}")
    if not args.local:
        print(f"  on the LAN {lan}")
    print("  first start downloads the language models; that takes a few minutes.")

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=args.port,
                                           log_level="warning"))

    def stop():
        server.should_exit = True

    if not args.headless:
        def opener():
            if wait_until_up(args.port):
                webbrowser.open(url)
        threading.Thread(target=opener, daemon=True).start()
        tray(url, stop)

    try:
        server.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
