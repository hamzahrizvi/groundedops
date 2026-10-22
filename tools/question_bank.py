"""Customer's question bank: 40 questions written in advance, so a test run starts asking at once.

  python tools/question_bank.py take <ops> <run>   RUN/manuals must exist (export_manuals.py)
  python tools/question_bank.py need <ops>
  python tools/question_bank.py --selftest

A bank is OPS/bank/<stamp>/ with manuals/ (exported when it was written), cases.json and READY.
take: the oldest ready bank whose manuals match RUN/manuals moves its cases.json into RUN and
      prints "TOOK <stamp>". A bank written from other manuals is deleted ("STALE <stamp>"): its
      references could be out of date. Nothing usable prints "EMPTY"; Customer then writes as before.
need: "FULL <stamp>" when a ready bank waits, else "EMPTY" (Customer writes the next one after its run).
focus: OPS/state/blind_focus.json {"product": "<manuals stem>", "pair": "<stem, optional>", "why": "..."}
      makes take keep every matching bank for a later run, still drop stale ones, and end with
      "FOCUS <product>" or "FOCUS <product> PAIR <pair>": Customer then writes this run's 40 about that
      product (the schedule moves the file into the run once the set is written, and reads the names
      only from this line). Each name must match ^[a-z0-9_]+$ and have RUN/manuals/<name>.txt; otherwise,
      or when the file is not a JSON object, take prints "FOCUS IGNORED <reason>" and goes on as normal.
"""
import filecmp
import glob
import json
import os
import re
import shutil
import sys


def _same(a, b):
    fa, fb = sorted(os.listdir(a)), sorted(os.listdir(b))
    _, differ, errors = filecmp.cmpfiles(a, b, fa, shallow=False)
    return fa == fb and not differ and not errors


def ready(ops):
    return sorted(d for d in glob.glob(os.path.join(ops, "bank", "*")) if os.path.exists(os.path.join(d, "READY")))


def focus(ops, run):
    """-> (product, pair, note) from OPS/state/blind_focus.json. Both names go into file paths and
    subagent prompts, so each must be a manual stem (^[a-z0-9_]+$) with a RUN/manuals/<stem>.txt;
    "pair" may be absent or null. Anything else gives (None, None, "FOCUS IGNORED <reason>")."""
    p = os.path.join(ops, "state", "blind_focus.json")
    if not os.path.exists(p):
        return None, None, None
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except ValueError:   # JSONDecodeError and UnicodeDecodeError are both ValueError
        return None, None, "FOCUS IGNORED not valid JSON"
    if not isinstance(d, dict):
        return None, None, "FOCUS IGNORED not a JSON object"
    for k in ("product", "pair"):
        v = d.get(k)
        if k == "pair" and v is None:
            continue
        if not isinstance(v, str) or not re.fullmatch(r"[a-z0-9_]+", v):
            return None, None, f"FOCUS IGNORED {k}: not a manual name"   # never echo an unchecked value
        if not os.path.exists(os.path.join(run, "manuals", v + ".txt")):
            return None, None, f"FOCUS IGNORED {v}: no manual"
    return d["product"], d.get("pair"), None


def take(ops, run):
    f, pair, note = focus(ops, run)
    out = [note] if note else []
    for d in ready(ops):
        name = os.path.basename(d)
        if os.path.isdir(os.path.join(d, "manuals")) and _same(os.path.join(d, "manuals"), os.path.join(run, "manuals")):
            if f:
                continue   # focus run: this bank waits for the run after
            shutil.move(os.path.join(d, "cases.json"), os.path.join(run, "cases.json"))
            open(os.path.join(run, "bank_source.txt"), "w", encoding="utf-8").write(name + "\n")
            shutil.rmtree(d)
            return out + [f"TOOK {name}"]
        shutil.rmtree(d)
        out.append(f"STALE {name}")
    return out + [(f"FOCUS {f} PAIR {pair}" if pair else f"FOCUS {f}") if f else "EMPTY"]


def need(ops):
    r = ready(ops)
    return f"FULL {os.path.basename(r[0])}" if r else "EMPTY"


def selftest():
    import tempfile
    t = tempfile.mkdtemp()
    def w(p, s):
        os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True)
        open(os.path.join(t, p), "w", encoding="utf-8").write(s)
    ops, run = os.path.join(t, "ops"), os.path.join(t, "run")
    w("run/manuals/a.txt", "manual v2")
    assert need(ops) == "EMPTY" and take(ops, run) == ["EMPTY"]
    w("ops/bank/20261001/manuals/a.txt", "manual v1"); w("ops/bank/20261001/cases.json", "old"); w("ops/bank/20261001/READY", "")
    w("ops/bank/20261002/manuals/a.txt", "manual v2"); w("ops/bank/20261002/cases.json", "new"); w("ops/bank/20261002/READY", "")
    w("ops/bank/20261003/manuals/a.txt", "manual v2"); w("ops/bank/20261003/cases.json", "half")   # still being written
    assert need(ops) == "FULL 20261001"
    assert take(ops, run) == ["STALE 20261001", "TOOK 20261002"], "old manuals dropped, the matching bank taken"
    assert open(os.path.join(run, "cases.json"), encoding="utf-8").read() == "new"
    assert need(ops) == "EMPTY", "a bank without READY is not ready"
    w("ops/bank/20261004/manuals/a.txt", "manual v2"); w("ops/bank/20261004/manuals/b.txt", "extra"); w("ops/bank/20261004/READY", "")
    assert take(ops, run) == ["STALE 20261004", "EMPTY"], "a product added or removed is a different set of manuals"
    # focus set: no bank taken; matching bank kept, stale bank dropped
    w("ops/bank/20261005/manuals/a.txt", "manual v2"); w("ops/bank/20261005/cases.json", "keep"); w("ops/bank/20261005/READY", "")
    w("ops/bank/20261006/manuals/a.txt", "manual v1"); w("ops/bank/20261006/READY", "")
    os.remove(os.path.join(run, "cases.json"))
    w("ops/state/blind_focus.json", '{"product": "a", "why": "test"}')
    assert take(ops, run) == ["STALE 20261006", "FOCUS a"], "focus set: no bank taken"
    assert not os.path.exists(os.path.join(run, "cases.json")) and need(ops) == "FULL 20261005"
    w("ops/state/blind_focus.json", '{"product": "zz", "why": "typo"}')
    assert take(ops, run) == ["FOCUS IGNORED zz: no manual", "TOOK 20261005"], "unknown product: normal take"
    # every value from the focus file is checked before it reaches a path or a prompt (D-20261022-12)
    w("run/x.txt", "not a manual: one folder above manuals/")
    for body, line in [('{"product": "../x"}', "FOCUS IGNORED product: not a manual name"),
                       ('{"product": "a", "pair": "zz"}', "FOCUS IGNORED zz: no manual"),
                       ('{"product": "a", "pair": "../x"}', "FOCUS IGNORED pair: not a manual name"),
                       ('{"product": "A"}', "FOCUS IGNORED product: not a manual name"),
                       ('{"product": 3}', "FOCUS IGNORED product: not a manual name"),
                       ('not json {', "FOCUS IGNORED not valid JSON"),
                       ('["a"]', "FOCUS IGNORED not a JSON object")]:
        w("ops/state/blind_focus.json", body)
        assert take(ops, run) == [line, "EMPTY"], body
    w("run/manuals/b.txt", "pair manual")
    w("ops/state/blind_focus.json", '{"product": "a", "pair": "b", "why": "pair"}')
    assert take(ops, run) == ["FOCUS a PAIR b"], "checked pair printed for the schedule"
    w("ops/state/blind_focus.json", '{"product": "a", "pair": null}')
    assert take(ops, run) == ["FOCUS a"], "null pair = no pair"
    os.remove(os.path.join(ops, "state", "blind_focus.json"))
    shutil.rmtree(t)
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]: selftest()
    elif a[:1] == ["take"] and len(a) == 3: print("\n".join(take(a[1], a[2])))
    elif a[:1] == ["need"] and len(a) == 2: print(need(a[1]))
    else: sys.exit(__doc__)
