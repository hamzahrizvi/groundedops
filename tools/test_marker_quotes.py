"""Self-check for the marker's quoted-pages section (D-20261019-05): blind_eval.py `prompt`, no network.

  python tools/lab_lock.py python tools/test_marker_quotes.py

An answer that copies a sentence from another page of the case's own manual gets that page shown to the
marker; a page nobody copies, and another product's manual, are not shown; a case with no copy says "none". The key-fact cap and the one clarify rule (D-20261020-04) are in the prompt.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P1 = "The escrow module holds each note in a temporary cashbox before it is stored for the host machine."
P2 = "Once the host machine confirms the credit the escrow module stacks every note into a cashbox below."
P3 = "Clean the optical sensors every month with a dry lint free cloth and never use any solvent on them."
OTHER = "The completely different gadget ships with a rechargeable lithium battery and a spare charging cable."


def main():
    with tempfile.TemporaryDirectory() as t:
        run = os.path.join(t, "run")
        os.makedirs(os.path.join(run, "manuals"))
        open(os.path.join(run, "manuals", "prod.txt"), "w", encoding="utf-8").write(
            f"[Prod Manual.pdf p1]\n{P1}\n\n[Prod Manual.pdf p2]\n{P2}\n\n[Prod Manual.pdf p3]\n{P3}")
        open(os.path.join(run, "manuals", "other.txt"), "w", encoding="utf-8").write(f"[Other Manual.pdf p1]\n{OTHER}")
        json.dump({"cases": [
            {"id": "c1", "product": "prod", "type": "absent_feature", "expected": "rejected", "pages": ["Prod Manual.pdf p1"],
             "q": "Does it have a cashbox?", "reference": "There is no cashbox."},
            {"id": "c2", "type": "vague", "expected": "clarify", "q": "How do I clean it?", "reference": "Ask which product."}]},
            open(os.path.join(run, "cases.json"), "w"))
        json.dump({"sys_normal": {"c1": {"answer": "No. " + P2 + " " + OTHER + " " + P1}, "c2": {"answer": P2}},
                   "baseline": {"c1": {"answer": "No."}, "c2": {"answer": "Which one?"}}},
                  open(os.path.join(run, "cases_state.json"), "w"))
        env = {**os.environ, "LAB_OPS": t, "GO_DATA": t, "PYTHONIOENCODING": "utf-8"}

        def prompt(cid):
            r = subprocess.run([sys.executable, os.path.join(HERE, "blind_eval.py"), run, "prompt", cid], env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
            assert r.returncode == 0, r.stderr
            return r.stdout

        out = prompt("c1")
        cited, rest = out.split("MANUAL PAGES THE ANSWERS QUOTE", 1)
        quoted = rest.split("The reference is an extract", 1)[0]
        assert "[Prod Manual.pdf p2]" in quoted and P2 in quoted, quoted
        assert "p3" not in quoted and "Other Manual" not in quoted and "Prod Manual.pdf p1" not in quoted, quoted
        assert P1 in cited, "cited page missing"
        assert "mark the verdict first" in out and "Where pages of the product's own manual disagree" in out
        assert "leaves out any one of them scores at most 2" in out and 'Verdict "clarified" always scores 3' in out, out
        assert "whether or not a product is selected in the chat" in out
        assert prompt("c2").split("MANUAL PAGES THE ANSWERS QUOTE", 1)[1].split("The reference is an extract", 1)[0].rstrip().endswith(": none")
    print("selftest ok")


if __name__ == "__main__":
    main()
