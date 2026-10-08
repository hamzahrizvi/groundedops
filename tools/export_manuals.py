"""Export each product's manual text from the index, one file per product.

The blind question writer reads ONLY these files: never the repo, the eval
cases or the FAQs. It is also what blind_eval.py's whole-manual baseline
pastes in. Same scope rule as answering (retrieval_db._matches_scope) and
the same internal-only exclusion (main.EXCLUDED_SOURCES default).

    cd src && ../.venv/Scripts/python.exe ../tools/export_manuals.py <out_dir>

Run from the src/ that holds the live chroma_db and catalog_config.json.
"""
import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import catalog  # noqa: E402
import db  # noqa: E402
from retrieval_db import _matches_scope, _order_key  # noqa: E402

EXCLUDED = [s.strip().lower() for s in
            os.getenv("EXCLUDED_SOURCES", "icu_network_api").split(",") if s.strip()]


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    got = db.get_collection().get(include=["documents", "metadatas"])
    rows = [(i, d, m) for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])
            if not any(x in (m.get("source") or "").lower() for x in EXCLUDED)]
    # "<category>_general" is a shared bucket already inside each product's scope.
    keys = [p["key"] for c in catalog.catalog().get("categories", [])
            for p in c.get("products", []) if p.get("key") and not p["key"].endswith("_general")]
    for key in keys:
        mine = sorted((r for r in rows if _matches_scope(r[2], None, {"product": key})),
                      key=lambda r: (r[2].get("source", ""), r[2].get("page") or 0, _order_key(r[0])))
        if not mine:
            continue
        text = "\n\n".join(f"[{m.get('source')} p{m.get('page')}]\n{d}" for _, d, m in mine)
        with open(os.path.join(out_dir, f"{key}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        print(key, len(mine), "chunks")


if __name__ == "__main__":
    main(sys.argv[1])
