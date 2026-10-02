"""Remove the website bot's 9 help pages from THIS store (9.7 decision 2).

They were stray copies: the bot keeps its own byte-identical copies in
../groundedops-site-engine/documents and its own index. Refuses unless the
sibling copy exists and matches, so nothing can be lost.
"""
import hashlib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "documents")
SIBLING = os.path.join(os.path.dirname(ROOT), "groundedops-site-engine", "documents")
STALE = ["MyCheckr User Manual-v7 (1).pdf"]  # manifest entry with no file


def sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    mpath = os.path.join(STORE, "manifest.json")
    with open(mpath, encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = [f for f in os.listdir(STORE) if f.endswith(".txt")]
    for f in pages:
        twin = os.path.join(SIBLING, f)
        if not os.path.isfile(twin) or sha(twin) != sha(os.path.join(STORE, f)):
            sys.exit(f"refusing: {f} has no identical copy in {SIBLING}")
    for f in pages:
        os.remove(os.path.join(STORE, f))
        manifest["documents"].pop(f, None)
        print("removed", f)
    for k in STALE:
        if k in manifest["documents"] and not os.path.exists(os.path.join(STORE, k)):
            manifest["documents"].pop(k); print("dropped stale manifest entry", k)
    with open(mpath, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"{len(pages)} page(s) removed; {len(manifest['documents'])} documents remain in the manifest")


if __name__ == "__main__":
    main()
