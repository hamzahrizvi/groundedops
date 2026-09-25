"""Regenerate the derived notes in GroundedOps-Vault from the source tree.

Derived, and rewritten on every run:
  10 Backend/<module>.py.md   one note per src/*.py: docstring, import graph,
                              public functions, classes, HTTP endpoints
  50 API/API - <group>.md     one note per module that declares routes
  50 API/API Surface.md       the endpoint hub

Everything else in the vault (the hubs, 20 Console and Widget, 30
Infrastructure, 40 Concepts, Tooling.md) is hand-written and left alone.
Stale derived notes (a module that no longer exists) are deleted. The run
ends with a wikilink check across the whole vault and fails on a dangling
link, so a hand-written note that names a removed module is caught here.

Run from anywhere:  python tools/build_vault.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
VAULT = ROOT / "GroundedOps-Vault"
BACKEND = VAULT / "10 Backend"
API = VAULT / "50 API"

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "api_route"}
TOOLING = {
    "eval", "eval_retrieval", "reindex", "diagnose", "diag_scope",
    "sweep_grounding", "run_tests", "test_queries", "manage_accounts",
    "manage_backup", "mint_token", "release", "backfill_faq_tables", "_harness",
}
ROUTE_GROUP_BLURB = {
    "main": "The answer pipeline and the liveness probes. Everything else is a router.",
    "routes_admin_auth": "Console sign-in, accounts and levels, emailed links, SSO.",
    "routes_admin_data": "Backup export and restore, the access policy, quota resets.",
    "routes_admin_keys": "Provider API keys, per-role models, the credit watch.",
    "routes_catalog": "The product catalogue: public picker tree, console editing, champions, retagging.",
    "routes_console": "The console page itself, its static assets, the network report.",
    "routes_documents": "Upload and ingest, the source inventory, retagging, downloads, cross-references.",
    "routes_faq": "Reviewed answers, unanswered questions (gaps), drafting and import.",
    "routes_settings": "Generation mode and provider, local models, sessions, saved conversations.",
    "routes_widget": "The widget script, preview, config, leads, plugin export.",
    "widget_api": "The public widget API: quota, catalogue, FAQ, ask (plain and streamed), enquiry drafts.",
}


def _first_paragraph(doc: str) -> str:
    doc = (doc or "").strip()
    if not doc:
        return ""
    first = doc.split("\n\n", 1)[0]
    first = " ".join(line.strip() for line in first.splitlines())
    return first[:420]


class Module:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        text = path.read_text(encoding="utf-8", errors="ignore")
        self.lines = text.count("\n") + 1
        self.tree = ast.parse(text)
        self.doc = _first_paragraph(ast.get_docstring(self.tree) or "")
        self.imports: set[str] = set()
        self.functions: list[str] = []
        self.classes: list[str] = []
        self.endpoints: list[tuple[str, str, str]] = []
        self.prefix = ""
        self._scan()

    def _scan(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                self.imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Assign):
                # router = APIRouter(prefix="/widget")
                if (isinstance(node.value, ast.Call)
                        and getattr(node.value.func, "id", "") == "APIRouter"):
                    for kw in node.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            self.prefix = kw.value.value
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    self.functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                self.classes.append(node.name)
        # Routes wherever they are declared: routers at top level, and a few
        # of main's inside the function that registers them.
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._routes(node)

    def _routes(self, fn):
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr not in HTTP_METHODS:
                continue
            if getattr(dec.func.value, "id", "") not in {"app", "router"}:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = self.prefix + dec.args[0].value
            if dec.func.attr == "api_route":
                methods = []
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                method = ",".join(methods) or "ANY"
            else:
                method = dec.func.attr.upper()
            self.endpoints.append((method, path, fn.name))


def load_modules() -> dict[str, Module]:
    mods = {p.stem: Module(p) for p in sorted(SRC.glob("*.py"))}
    for m in mods.values():
        m.imports = {i for i in m.imports if i in mods and i != m.name}
    return mods


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def module_note(m: Module, mods: dict[str, Module]) -> str:
    tags = ["backend", "python"]
    if m.endpoints:
        tags.append("api")
    if m.name in TOOLING:
        tags.append("tooling")
    imported_by = sorted(n for n, o in mods.items() if m.name in o.imports)
    out = [
        "---",
        f"tags: [{', '.join(tags)}]",
        f"file: src/{m.name}.py",
        f"lines: {m.lines}",
        "---",
        "",
        f"# {m.name}.py",
        "",
        "## Purpose",
        "",
        m.doc or "_No module docstring._",
        "",
        "## Imports (outgoing edges)",
        "",
    ]
    out += [f"- [[{i}.py]]" for i in sorted(m.imports)] or ["_none_"]
    out += ["", "## Imported by (incoming edges)", ""]
    out += [f"- [[{i}.py]]" for i in imported_by] or ["_none_"]
    if m.endpoints:
        group = m.name.removeprefix("routes_")
        out += ["", "## HTTP endpoints", "", "| Method | Path | Handler |", "|---|---|---|"]
        out += [f"| `{meth}` | `{path}` | `{fn}()` |" for meth, path, fn in m.endpoints]
        out += ["", f"Listed with the other groups in [[API - {group}]] and [[API Surface]]."]
    if m.functions:
        out += ["", "## Public functions", ""]
        out += [f"- `{f}`" for f in m.functions]
    if m.classes:
        out += ["", "## Classes", ""]
        out += [f"- `{c}`" for c in m.classes]
    out += ["", "## Related", "", "- [[GroundedOps]] — vault index",
            "- [[Architecture]] — how the pieces fit"]
    if m.name in TOOLING:
        out.append("- [[Tooling]] — the other scripts outside the request path")
    return "\n".join(out)


def api_note(m: Module) -> str:
    group = m.name.removeprefix("routes_")
    blurb = ROUTE_GROUP_BLURB.get(m.name, m.doc)
    out = [
        "---",
        "tags: [api, endpoints]",
        "---",
        "",
        f"# API — {group}",
        "",
        f"> {blurb}",
        "",
        "| Method | Path | Handler |",
        "|---|---|---|",
    ]
    out += [f"| `{meth}` | `{path}` | `{fn}()` |" for meth, path, fn in m.endpoints]
    out += ["", f"All handlers live in [[{m.name}.py]].", "", "## Related", "",
            "- [[API Surface]]", "- [[GroundedOps]]"]
    return "\n".join(out)


def surface_note(routed: list[Module]) -> str:
    total = sum(len(m.endpoints) for m in routed)
    out = [
        "---",
        "tags: [api, moc]",
        "---",
        "",
        "# API Surface",
        "",
        f"{total} HTTP endpoints. [[main.py]] keeps the answer pipeline and the",
        "liveness probes; every other group is an `APIRouter` in its own",
        "`routes_*.py` module, registered by main at startup with its paths",
        "unchanged. The public widget API is [[widget_api.py]], under `/widget`.",
        "",
        "## Groups",
        "",
    ]
    for m in routed:
        group = m.name.removeprefix("routes_")
        out.append(f"- [[API - {group}]] — {len(m.endpoints)} endpoint(s): {ROUTE_GROUP_BLURB.get(m.name, '')}")
    out += [
        "",
        "## Consumers",
        "",
        "- [[admin.html]] — the console, same origin at `/admin`",
        "- [[groundedops-widget.js]] — the website widget, cross-origin, `/widget/*` only",
        "- [[groundedops-widget.php]] — the WordPress plugin that embeds the widget and signs member tokens",
        "- [[Tooling]] — eval and diagnostic scripts drive `/query` directly",
        "",
        "## Guards",
        "",
        "Admin routes require a console session ([[guards.py]], [[accounts.py]]);",
        "the widget routes are public but budgeted per caller ([[quota.py]],",
        "[[policy.py]]). The console surface is LAN-only unless",
        "`ADMIN_ALLOWED_IPS` says otherwise — see [[Access and Accounts]].",
        "",
        "## Related",
        "",
        "- [[GroundedOps]]",
        "- [[Architecture]]",
    ]
    return "\n".join(out)


LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def check_links() -> list[tuple[Path, str]]:
    names = {p.stem for p in VAULT.rglob("*.md")}
    dangling = []
    for p in VAULT.rglob("*.md"):
        for target in LINK_RE.findall(p.read_text(encoding="utf-8", errors="ignore")):
            if target.strip() not in names:
                dangling.append((p.relative_to(VAULT), target))
    return dangling


def main() -> int:
    mods = load_modules()
    BACKEND.mkdir(parents=True, exist_ok=True)
    API.mkdir(parents=True, exist_ok=True)

    wanted_backend = set()
    for m in mods.values():
        write(BACKEND / f"{m.name}.py.md", module_note(m, mods))
        wanted_backend.add(f"{m.name}.py.md")
    for p in BACKEND.glob("*.py.md"):
        if p.name not in wanted_backend:
            p.unlink()
            print("removed stale", p.relative_to(VAULT))

    routed = [m for m in mods.values() if m.endpoints]
    routed.sort(key=lambda m: (m.name != "main", m.name))
    wanted_api = {"API Surface.md"}
    for m in routed:
        name = f"API - {m.name.removeprefix('routes_')}.md"
        write(API / name, api_note(m))
        wanted_api.add(name)
    write(API / "API Surface.md", surface_note(routed))
    for p in API.glob("*.md"):
        if p.name not in wanted_api:
            p.unlink()
            print("removed stale", p.relative_to(VAULT))

    total = sum(len(m.endpoints) for m in routed)
    print(f"{len(mods)} module notes, {len(routed)} API groups, {total} endpoints")

    dangling = check_links()
    for where, target in dangling:
        print(f"DANGLING  {where}: [[{target}]]")
    if dangling:
        return 1
    print("every wikilink resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
