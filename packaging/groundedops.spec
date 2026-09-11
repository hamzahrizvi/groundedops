# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GroundedOps desktop build.

Build it from the REPO ROOT, not from packaging/:

    .venv\Scripts\pyinstaller.exe packaging\groundedops.spec --noconfirm

Expect 15-30 minutes and about 2GB in dist\GroundedOps\. torch alone is
533MB on this machine and there is no trimming it without dropping the
local embedding and reranking models, which is the offline mode's whole
point.

ONE-FOLDER, NOT ONE-FILE, deliberately. A --onefile build of this size
unpacks ~2GB to a temp directory on every launch, which adds tens of
seconds to each start and defeats the background-service use. The installer
(installer.iss) is what turns the folder into a single downloadable .exe.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.getcwd())
SRC = os.path.join(ROOT, "src")

datas = [
    # The console is a single HTML file read from disk at request time, and
    # its stylesheet and the brand assets are served as static mounts. All
    # three are data, not code, so PyInstaller cannot infer them.
    (os.path.join(SRC, "admin.html"), "src"),
    (os.path.join(SRC, "nocturne"), "src/nocturne"),
    (os.path.join(SRC, "assets"), "src/assets"),
    (os.path.join(SRC, "widget"), "src/widget"),
    (os.path.join(SRC, "eval_cases.json"), "src"),
]
binaries, hiddenimports = [], []

# These four resolve plugins, models and metadata at runtime, so their data
# files and submodules are invisible to static analysis.
for pkg in ("chromadb", "sentence_transformers", "transformers", "tokenizers"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:          # a missing optional package is not fatal
        print(f"[spec] collect_all({pkg}) skipped: {exc}")

for pkg in ("pdfplumber", "pypdfium2", "certifi"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports += [
    # Imported by name inside the app rather than at module top level.
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "onnxruntime", "rank_bm25", "pysqlite3", "tiktoken_ext",
    "tiktoken_ext.openai_public",
]

excludes = [
    # Dev-only, and large: playwright is 108MB of browser plumbing used by
    # the UI test, and the notebook stack comes along for the ride otherwise.
    "playwright", "pytest", "IPython", "notebook", "jupyter",
    "matplotlib", "tkinter",
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT, SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GroundedOps",
    console=True,          # the first start prints model-download progress;
                           # hiding it makes a slow start look like a hang
    icon=os.path.join(SRC, "assets", "icon2.svg")
         if os.path.exists(os.path.join(SRC, "assets", "app.ico")) else None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,          # UPX on torch's DLLs breaks them
    name="GroundedOps",
)
