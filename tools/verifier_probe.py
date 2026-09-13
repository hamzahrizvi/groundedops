"""Prototype: can an LLM verify answers that NLI cannot?

Scored against real retrieved context. Includes the adversarial cases the
lexical rescue could not separate -- notably the flash-code recombination,
which reuses a real row's wording but asserts the wrong pairing.
"""
import os, sys, re, time; sys.path.insert(0,'.')
from dotenv import load_dotenv; load_dotenv(".env")
import retrieval_db, llm
from main import CHUNK_CHAR_CAP

MODEL = os.getenv("ONLINE_DEEPSEEK_MODEL", "deepseek-v4-flash")

VERIFY = """You are checking whether an ANSWER is fully supported by SOURCE text \
extracted from a product manual.

The SOURCE may contain tables that have been flattened into pipe-separated or \
label:value rows. Read those rows as data: a row like "1 | 1 | Note path open" \
means the values in that row belong together.

Reply with exactly one word on the first line:
SUPPORTED   - every factual claim in the ANSWER appears in the SOURCE
UNSUPPORTED - any claim is absent from the SOURCE, or pairs values that the \
SOURCE does not pair together

Then one short line saying why.

SOURCE:
{ctx}

ANSWER:
{ans}
"""

# (product, question, answer, expected_verdict)
CASES = [
 # --- correct answers that NLI scored ~0 and suppressed ---
 ("nv9_spectral","How do I put the validator into SSP programming mode?",
  "For the NV9 Spectral validator: with the unit powered ON, press and hold the configuration button for more than 3 seconds until the bezel illuminates, then release. This switches the validator to SSP programming mode.","SUPPORTED"),
 ("nv9_spectral","My bezel is flashing one long flash then one short flash - what's wrong?",
  "On an NV9 Spectral, one long flash followed by one short flash means the note path is open. Ensure the upper assembly is closed.","SUPPORTED"),
 ("sku_scs","What options are available for the pay-in chute on the SMART Coin System?",
  "There are two pay-in chute options: the PM01106 Funnel, which is standard, and the PA04138 Chute with Debris Collection.","SUPPORTED"),
 ("sku_scs","The status LED is flashing red four times - what does that mean?",
  "Four red flashes indicates a Feeder Calibration Error. Remove power and check the SMART Hopper and Coin Feeder connection.","SUPPORTED"),
 ("sku_scs","How much does the SMART Coin System weigh empty and when it's full?",
  "The SMART Coin System weighs 4 kg empty and approximately 18 kg full.","SUPPORTED"),
 ("sku_scs","Can I plug the USB straight into my host PC to talk to the machine?",
  "No. Direct USB should not be used for host communications. If USB is required, use an IF17 (TTL to USB) interface.","SUPPORTED"),

 # --- must be rejected ---
 ("nv9_spectral","My bezel flashes four times - what's wrong?",
  "On the NV9 Spectral, four long flashes means the note path is open. Ensure the upper assembly is closed.","UNSUPPORTED"),   # recombination: real words, wrong row
 ("sku_scs","The status LED is flashing red twice - what does that mean?",
  "Two red flashes indicates a Feeder Calibration Error.","UNSUPPORTED"),                                                      # wrong count -> wrong fault
 ("nv9_spectral","What features does the NV9S have?",
  "The NV9S has a built-in thermal printer.","UNSUPPORTED"),
 ("nv9_spectral","Does it have 5G?",
  "The NV9S includes 5G connectivity for remote monitoring.","UNSUPPORTED"),
 ("sku_scs","How much does the SMART Coin System weigh?",
  "The SMART Coin System weighs 8 kg empty and approximately 30 kg full.","UNSUPPORTED"),                                      # plausible wrong numbers
 ("mycheckr","How do I reset my Cisco router password?",
  "To reset the password, hold the reset button for 10 seconds and log in with the default admin credentials.","UNSUPPORTED"),
]

ok = 0; fp = 0; fn = 0; secs = 0.0
print("%-11s %-11s %s" % ("EXPECTED","GOT","ANSWER"))
print("-"*104)
for prod,q,ans,expect in CASES:
    ch = retrieval_db.retrieve_from_db(q, top_k=5, scope={"product":prod})
    ctx = "\n\n".join(c["text"][:CHUNK_CHAR_CAP] for c in ch)
    t0=time.time()
    out = llm.generate("deepseek", VERIFY.format(ctx=ctx, ans=ans), MODEL)
    secs += time.time()-t0
    raw = ((out or {}).get("text") or "").strip()
    got = "SUPPORTED" if re.match(r"^\W*SUPPORTED", raw, re.I) else (
          "UNSUPPORTED" if re.search(r"UNSUPPORTED", raw[:40], re.I) else "??")
    good = (got == expect)
    ok += good
    if not good and expect=="SUPPORTED": fn += 1
    if not good and expect=="UNSUPPORTED": fp += 1
    print("%-11s %-11s %s %s" % (expect, got, "OK " if good else "MISS", ans[:56]))
    if not good:
        print("            reason: %s" % raw[:150].replace("\n"," "))
print("-"*104)
print("correct %d/%d   false-accepts(dangerous) %d   false-rejects %d   avg %.1fs/call"
      % (ok, len(CASES), fp, fn, secs/len(CASES)))
