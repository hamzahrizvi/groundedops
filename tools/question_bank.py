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
      "FOCUS <product>": Customer then writes this run's 40 about that product (the schedule moves
      the file into the run once the set is written). A focus whose product has no RUN/manuals/<product>.txt
      is ignored ("FOCUS IGNORED <product>: no manual") and take goes on as normal.
"""
import filecmp
import glob
import json
import os
import shutil
import sys


def _same(a, b):
    fa, fb = sorted(os.listdir(a)), sorted(os.listdir(b))
    _, differ, errors = filecmp.cmpfiles(a, b, fa, shallow=False)
    return fa == fb and not differ and not errors


def ready(ops):
    return sorted(d for d in glob.glob(os.path.join(ops, "bank", "*")) if os.path.exists(os.path.join(d, "READY")))


def focus(ops):
    p = os.path.join(ops, "state", "blind_focus.json")
    return json.load(open(p, encoding="utf-8")).get("product") if os.path.exists(p) else None


def take(ops, run):
    out, f = [], focus(ops)
    if f and not os.path.exists(os.path.join(run, "manuals", f + ".txt")):
        out.append(f"FOCUS IGNORED {f}: no manual")
        f = None
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
    return out + [f"FOCUS {f}" if f else "EMPTY"]


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
    os.remove(os.path.join(ops, "state", "blind_focus.json"))
    shutil.rmtree(t)
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]: selftest()
    elif a[:1] == ["take"] and len(a) == 3: print("\n".join(take(a[1], a[2])))
    elif a[:1] == ["need"] and len(a) == 2: print(need(a[1]))
    else: sys.exit(__doc__)
