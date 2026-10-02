"""9.12 gate: send the repeat-refused phrasings through /query once and
count how each one ends (faq / answer / clarify / refuse).

    ../.venv/Scripts/python.exe ../tools/faq_gap_pass.py   (from src, backend on :8000)

Sessions are 'eval-' prefixed so the turns log as origin=eval, not traffic.
"""
import json, os, sys, uuid, urllib.request

URL = os.getenv("EVAL_URL", "http://127.0.0.1:8000/query")
PHRASINGS = [
    "why is multicast required for hub discovery",
    "what are the steps to change the protocol on the nv9 spectral to cctalk",
    "show me the operating temperature table for mycheckr mini",
    "what is the cashbox capacity of the bv30",
    "what is mycheckr and what does it do",
    "do you have anything that sorts and pays out coins",
    "installer checklist for myconnect",
    "show me the coin media requirements table",
    "what is the default wifi password",
    "how many years of warranty am i given and what does it cover",
    "what is the maximum coin capacity in litres of the SMART Coin System",
]


def kind(r: dict) -> str:
    if r.get("from_faq"):
        return "faq"
    a = (r.get("answer") or "").lower()
    if a.startswith(("i could not find", "i don't have that", "i do not have", "i was unable")):
        return "refuse"
    if a.startswith(("i'm not sure which", "that could apply to more than one")) or r.get("clarification_options"):
        return "clarify"
    if a.startswith("i can only answer technical"):
        return "handoff"
    return "answer"


def main() -> int:
    counts = {}
    for q in PHRASINGS:
        body = json.dumps({"q": q, "session_id": f"eval-912-{uuid.uuid4()}"}).encode()
        req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=180))
        k = kind(r)
        counts[k] = counts.get(k, 0) + 1
        print(f"{k:8} | {q[:60]:60} | {', '.join(s.get('source', str(s)) if isinstance(s, dict) else s for s in r.get('sources') or [])[:70]}")
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
