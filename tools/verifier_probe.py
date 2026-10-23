"""Probe the answer-checker (M8): does the LLM verifier accept right answers
and reject wrong ones, run by run, and would a second vendor do better?

It calls the PRODUCTION verifier, main._llm_verified -- the same prompt
(SUPPORT and RELEVANCE judged separately), the same thinking gate
(_needs_judgement) and the same fail-closed parsing -- against chunks
retrieved live for each question. The earlier prototype carried its own
one-word prompt and called llm.generate outside judging(), so it measured a
verifier that no customer answer ever went through.

Case groups, reported separately:
  correct  -- right answers (several are NLI-suppressed table rows); must pass
  wrong    -- fabrications, mispaired table rows, a relevant-but-unresponsive
              answer; must be rejected
  absence  -- a confident "No" the manual never states. Rejecting these is the
              8.3 policy, not today's prompt, so they are reported as a
              baseline and never counted as false accepts
  control  -- a "No" the manual DOES state; must pass (8.3 must not break it)

Judges:
  production  generate_with_fallback("accurate", ...) as configured (default)
  deepseek    llm.generate("deepseek", ..., --model or ONLINE_DEEPSEEK_MODEL)
  anthropic   llm.generate("anthropic", ..., --model, default claude-sonnet-5;
              needs ANTHROPIC_API_KEY. Sonnet 5 thinks adaptively when no
              thinking field is sent, which is what _call_anthropic sends.
              Its max_tokens is 2048, thinking included.)
  openai      llm.generate("openai", ..., --model or ONLINE_OPENAI_MODEL);
              the OPENAI_BASE_URL slot, i.e. the ITL gateway when set

Run from anywhere; it switches to src/ and reads src/.env:
  python tools/verifier_probe.py --repeats 3
  python tools/verifier_probe.py --judge anthropic --json out.json
"""
import argparse, json, os, sys, time
from collections import defaultdict

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
os.chdir(SRC)
sys.path.insert(0, SRC)
from dotenv import load_dotenv; load_dotenv(".env")

# (group, product, question, answer, why)
CASES = [
 # --- correct: must pass -------------------------------------------------
 ("correct","nv9_spectral","How do I put the validator into SSP programming mode?",
  "For the NV9 Spectral validator: with the unit powered ON, press and hold the configuration button for more than 3 seconds until the bezel illuminates, then release. This switches the validator to SSP programming mode.",
  "table row NLI scored ~0"),
 ("correct","nv9_spectral","My bezel is flashing one long flash then one short flash - what's wrong?",
  "On an NV9 Spectral, one long flash followed by one short flash means the note path is open. Ensure the upper assembly is closed.",
  "flash table p.61"),
 ("correct","nv9usb","bezel is doing 1 long flash then 2 short flashes on the nv9usb, whats that",
  "On the NV9USB+, 1 long flash followed by 2 short flashes means Note Path Jam.",
  "p.56; the row thinking-off once inverted"),
 ("correct","sku_scs","What options are available for the pay-in chute on the SMART Coin System?",
  "There are two pay-in chute options: the PM01106 Funnel, which is standard, and the PA04138 Chute with Debris Collection.",
  "parts table"),
 ("correct","sku_scs","The status LED is flashing red four times - what does that mean?",
  "Four red flashes indicates a Feeder Calibration Error. Remove power and check the SMART Hopper and Coin Feeder connection.",
  "p.85"),
 ("correct","sku_scs","How much does the SMART Coin System weigh empty and when it's full?",
  "The SMART Coin System weighs 4 kg empty and approximately 18 kg full.",
  "p.19 weights"),
 ("correct","sku_scs","What's the limit for Euro 1 cent coins in the SMART Coin System before it dumps them?",
  "Euro 1 cent coins can make up 25% of all coins before the excess is dumped to the cashbox; if no other coins are in the device, the maximum is 20 coins.",
  "p.65, keeps the 'or 20 coins' condition (8.16)"),
 ("correct","nv9_spectral","what speed class does the microSD card need to be for the NV9 Spectral",
  "For the NV9 Spectral, the microSD card should be Speed Class 4 at minimum and Speed Class 10 at maximum.",
  "p.19; the blind key had this wrong once"),

 # --- wrong: must be rejected --------------------------------------------
 ("wrong","nv9_spectral","My bezel flashes four times - what's wrong?",
  "On the NV9 Spectral, four long flashes means the note path is open. Ensure the upper assembly is closed.",
  "recombination: real words, wrong row"),
 ("wrong","sku_scs","The status LED is flashing red twice - what does that mean?",
  "Two red flashes indicates a Feeder Calibration Error.",
  "wrong count -> wrong fault (2 = Calibration Fault)"),
 ("wrong","nv9usb","My NV9USB+ bezel is doing 2 long flashes then 1 short flash, what's wrong?",
  "On the NV9USB+, 2 long flashes followed by 1 short flash indicates a cashbox jam.",
  "p.56 mispairing: cashbox jam is 2 long 2 short; 2 long 1 short is N/A"),
 ("wrong","bv30","the BV30 bezel is showing 1 red flash then 4 blue flashes, what does that mean",
  "On the BV30, 1 red flash followed by 4 blue flashes means a Note Path Jam. Clear any jammed notes.",
  "p.31 mispairing: 4 blue = Sensor Covered, 2 blue = Note Path Jam"),
 ("wrong","nv9_spectral","What features does the NV9S have?",
  "The NV9S has a built-in thermal printer.",
  "fabrication"),
 ("wrong","nv9_spectral","Does it have 5G?",
  "The NV9S includes 5G connectivity for remote monitoring.",
  "fabrication"),
 ("wrong","sku_scs","How much does the SMART Coin System weigh?",
  "The SMART Coin System weighs 8 kg empty and approximately 30 kg full.",
  "plausible wrong numbers"),
 ("wrong","mycheckr","How do I reset my Cisco router password?",
  "To reset the password, hold the reset button for 10 seconds and log in with the default admin credentials.",
  "off-corpus"),
 ("wrong","nv9usb","what are the pin assignments for the IF5 interface on the NV9USB+ for MDB",
  "The NV9USB+ connects to an MDB machine using the IF5 interface box, which regulates the power supply and opto-isolates the communication lines. IF5 Interface Box - ITL part number: PA02061 - IF5 KIT - MDB Voltage Converter",
  "M8 label #20: true but does not answer; the pinout is on p.44. NLI passed it at 0.747"),

 # --- absence: a 'No' the manual never gives (8.3 baseline) --------------
 ("absence","mycheckr","Does the MyCheckr support Bluetooth?",
  "No. The MyCheckr does not support Bluetooth.", "the logged Bluetooth row"),
 ("absence","nv9usb","can the NV9USB+ take coins as well as notes",
  "No. The NV9USB+ is a banknote validator and does not accept coins.",
  "M8 label #30 (served; person-rule: right by inference)"),
 ("absence","bv30","Does the BV30 have Bluetooth?",
  "No, the BV30 does not have Bluetooth.", ""),
 ("absence","sku_scs","does the SMART Coin System support facial recognition",
  "No. The SMART Coin System does not support facial recognition.", ""),
 ("absence","mycheckr_mini","does mycheckr mini do ethernet or only wifi",
  "The MyCheckr Mini supports neither; it connects only over USB.",
  "cf. M8 label #45, which the person marked right"),

 # --- control: a 'No' the manual states; must pass -----------------------
 ("control","sku_scs","Can I plug the USB straight into my host PC to talk to the machine?",
  "No. Direct USB should not be used for host communications. If USB is required, use an IF17 (TTL to USB) interface.",
  "stated 'should not be used'"),
 ("control","bv30","Can I use an IF5 with the MDB adapter on the BV30?",
  "No. An IF5 cannot be used with the MDB Adapter.",
  "p.22 'An IF5 cannot be used with MDB Adapter'"),
 ("control","nv9usb","Can I use a configuration card with UK1 firmware on the NV9USB+?",
  "No. UK1 firmware does not support the configuration card option.",
  "p.36 'UK1 Firmware does not support the configuration card option'"),
]

EXPECT = {"correct": True, "wrong": False, "absence": False, "control": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--judge", default="production",
                    choices=["production", "deepseek", "anthropic", "openai"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--group", default=None, choices=sorted(EXPECT))
    ap.add_argument("--json", default=None, help="write per-case results here")
    args = ap.parse_args()

    import main as m, llm, retrieval_db
    if args.judge != "production":
        model = args.model or {
            "deepseek": os.getenv("ONLINE_DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "anthropic": "claude-sonnet-5",
            "openai": os.getenv("ONLINE_OPENAI_MODEL", "gpt-4o-mini"),
        }[args.judge]
        m.generate_with_fallback = (
            lambda role, prompt, **kw: llm.generate(args.judge, prompt, model, **kw))
        judge = f"{args.judge}:{model}"
    else:
        judge = "production"

    cases = [c for c in CASES if not args.group or c[0] == args.group]
    rows, secs, calls = [], 0.0, 0
    print(f"judge {judge}, {args.repeats} run(s) per case\n")
    for grp, prod, q, ans, why in cases:
        chunks = retrieval_db.retrieve_from_db(q, top_k=5, scope={"product": prod})
        verdicts = []
        for _ in range(args.repeats):
            t0 = time.time()
            ok = m._llm_verified(ans, chunks, question=q)
            secs += time.time() - t0; calls += 1
            verdicts.append(ok)
        acc = sum(verdicts)
        good = acc if EXPECT[grp] else args.repeats - acc
        mark = "ok  " if good == args.repeats else ("MISS" if good == 0 else "FLIP")
        print(f"{grp:8} {mark} accepted {acc}/{args.repeats}  "
              f"{'think' if m._needs_judgement(q, ans) else 'fast '}  {ans[:60]}")
        rows.append({"group": grp, "product": prod, "question": q, "answer": ans,
                     "why": why, "accepted": verdicts,
                     "thinking": m._needs_judgement(q, ans)})

    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["group"]][0] += sum(r["accepted"])
        by[r["group"]][1] += len(r["accepted"])
    print()
    for g in ("correct", "wrong", "absence", "control"):
        if g in by:
            a, n = by[g]
            print(f"{g:8} accepted {a}/{n}  (want {'all' if EXPECT[g] else 'none'})")
    fa = by["wrong"][0]
    fr = (by["correct"][1] - by["correct"][0]) + (by["control"][1] - by["control"][0])
    print(f"\nfalse accepts (wrong) {fa}   false rejects (correct+control) {fr}   "
          f"absence accepted {by['absence'][0]}   avg {secs / max(calls, 1):.1f}s/call")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"judge": judge, "repeats": args.repeats, "cases": rows,
                       "false_accepts": fa, "false_rejects": fr,
                       "absence_accepted": by["absence"][0],
                       "avg_secs": round(secs / max(calls, 1), 2)}, f, indent=1)


if __name__ == "__main__":
    main()
