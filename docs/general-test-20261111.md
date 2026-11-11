# General product test, 11 November 2026

**What was tested.** The general base product (`main`, built from
`experimental/v17.3` with the Innovative Technology specifics taken out) on
three other companies' public product manuals: Tesla, Microsoft and Huawei.
Ten hard questions per company, asked through a separate test install, each
answer graded by reading the manual pages.

**Result.**

| Company | Score | Notes |
|---|---|---|
| Tesla | 8/10 | |
| Microsoft | 10/10 | 8/10 on the first run; the two failures were one general bug, fixed on this branch and re-asked |
| Huawei | 8/10 | |

Of the six remaining failures, two are refusals where an answer existed,
three are wrong-shaped answers, one is a vague question answered instead of
asked back. **No answer stated a wrong fact.** All three questions the manuals
do not answer were refused, and two of the three vague questions were asked
back.

## How it was run

- A separate install: its own data folder (`C:/Users/hrizvi/gops-general-test/`),
  port 8020, code from the `general/base` worktree. The live install and its
  index were not touched.
- Answers by DeepSeek V4 Flash only (every model role pinned to DeepSeek; no
  OpenAI or Anthropic key present). Pictures were not described at upload.
- Each company is one product range in the catalogue, and every question was
  asked scoped to that range, the way a one-company install would see it.
  Follow-ups were asked in the same chat as the question before them.
- Spend: about 205,000 tokens for 33 answers (30 questions plus 3 re-asks),
  roughly $0.05-0.06 at DeepSeek V4 Flash prices (an estimate from the token
  counts; the lab cap tool does not count this install).

## Documents (kept outside the repository, never committed)

All are official manufacturer documents.

| Company | File | Pages | Size | Source |
|---|---|---|---|---|
| Tesla | Model3_Owners_Manual_NA_2024plus.pdf | 329 | 11.3 MB | tesla.com/ownersmanual/model3/en_us/, "Download PDF" (2024+, software 2026.38.3, North America) |
| Tesla | ModelY_Owners_Manual_NA_2025plus.pdf | 342 | 16.4 MB | tesla.com/ownersmanual/modely/en_us/, "Download PDF" (2025+, North America) |
| Tesla | gen-3-wall-connector-manual-en.pdf | 67 | 18.0 MB | digitalassets.tesla.com/tesla-contents/image/upload/gen-3-wall-connector-manual-en.pdf (Wall Connector 3 install manual, 6-32 A version) |
| Microsoft | en-us_Surface_Pro_User_Guide.pdf (Surface Pro and Surface Pro 2) | 100 | 3.4 MB | download.microsoft.com/download/B/D/4/BD44C612-D08E-4586-9345-ACA8AB978BC8/en-us_Surface_Pro_User_Guide.pdf |
| Microsoft | Surface-Pro-3-User-Guide-EN.pdf | 108 | 2.7 MB | download.microsoft.com/download/E/8/9/E8966106-0FAE-40C1-9823-2EC483AC7CF3/Surface-Pro-3-User-Guide-EN.pdf |
| Microsoft | Surface-3-User-Guide-EN.pdf | 52 | 1.5 MB | download.microsoft.com/download/3/3/7/337D0360-D608-4442-A17A-D897FD95FA6F/Surface-3-User-Guide-EN.pdf |
| Huawei | HUAWEI_WATCH_GT_4_User_Guide_PNX-B19_en-us.pdf | 66 | 1.0 MB | consumer.huawei.com/en/support/wearables/watch-gt4/ (Downloads, links to consumer-tkbdownload.huawei.com) |
| Huawei | HUAWEI_WATCH_GT_5_and_GT_5_Pro_User_Guide_en-us.pdf | 58 | 2.8 MB | consumer.huawei.com/en/support/wearables/watch-gt5/ (same) |
| Huawei | HUAWEI_WiFi_AX3_Quick_Start_Guide_WS7100-20_WS7200-20_en.pdf | 59 | 6.8 MB | consumer.huawei.com/sa-en/support/routers/ax3-quad-core/ (same; English, Thai and Chinese in one file) |

Newer Surface models have no PDF guide from Microsoft (online help only), so
the three Surface guides are older models. Tesla's site refuses plain
downloads, so its two owner's manuals were saved through a normal browser
from Tesla's own "Download PDF" link.

Indexed: 4,430 passages; one page of the Wall Connector manual had no
readable text (not OCR'd).

## The questions and how each was graded

Page numbers are PDF pages. Types per company: two comparisons, spec-table facts, multi-step procedures,
troubleshooting by symptom, one question the documents do not answer, one
vague question, one follow-up. The full list with reference answers and pages
is `src/eval_cases.json`, which is now `main`'s sample eval suite.

### Tesla 8/10

| # | Question (short) | Result |
|---|---|---|
| T1 | Bigger front trunk, Model 3 or Model Y Performance? | **Fail.** Refused, both times asked. |
| T2 | Model 3 Performance ground clearance | Pass: 5.0 in (128 mm), not the look-alike RWD table (p.229) |
| T3 | Follow-up: "And the rear-wheel drive one?" | Pass: 5.4 in (138 mm) |
| T4 | Model Y 7-seater, 19" wheels, 7 people: towing | Pass: 2,000 lbs (907 kg), tongue 200 lbs (p.112) |
| T5 | Charge cable stuck in a Model 3 | Pass: manual release cable steps, plus the frozen-latch advice (pp.190, 275-276) |
| T6 | Wall Connector blinks red 3 times | Pass: high temperature, all four checks (p.59) |
| T7 | Wall Connector current range and smallest cable | Pass: 6-32 A, nothing under 4 mm2 stranded (p.12) |
| T8 | Model Y Performance 0-60 time (not in the manual) | Pass: refused |
| T9 | "It won't charge. What should I do?" (vague) | Pass: asked back with the manual's charging troubleshooting sections |
| T10 | Make the Model Y liftgate open lower, and undo it | **Fail (incomplete).** Gave the undo path but not how to save the height (hold the button under the liftgate until the chime, or the touchscreen prompts) |

- **T1:** The comparison found both cars' manuals and the right pages, but the
  draft answer said the Model 3's front-trunk size is not given. It is (3.1 cu
  ft / 88 L, Model 3 manual p.230, against 4.1 cu ft / 116 L for the Model Y
  Performance, p.245). The checker caught the wrong claim, so the visitor got
  a refusal instead of a wrong answer.

### Microsoft 10/10 (8/10 before the fix)

| # | Question (short) | Result |
|---|---|---|
| M1 | Kickstand: Surface Pro 3 vs Surface Pro 2 | Pass after fix: Pro 3 any angle down to 30 degrees; Pro 2 two positions, 24 and 40 degrees. First run: refused |
| M2 | Surface Pro 3 screen | Pass: 12", 2160 x 1440, 3:2 |
| M3 | Follow-up: "And the Surface 3?" | Pass: 10.8", 1920 x 1280, 3:2 |
| M4 | Which power supply is bigger, Pro 2 or Pro 3? | Pass after fix: Pro 2 48 W, Pro 3 36 W. First run: refused |
| M5 | Re-pair the pen with a Surface Pro 3 | Pass: all four steps |
| M6 | Pen won't write, erase, right-click or pair | Pass: the two 319 coin cell batteries |
| M7 | Upgrade Surface 3 to Windows 11? (not in the guide) | Pass: refused |
| M8 | "It keeps disconnecting." (vague) | Pass: asked back, offering the Wi-Fi, network and VPN sections |
| M9 | Connect a Surface 3 to an HDMI TV | Pass: cable and adapter options, three steps |
| M10 | Surface 3 memory and storage | Pass: 2 GB/64 GB or 4 GB/128 GB |

- **M1, M4 (fixed):** The Surface Pro and Pro 2 share one manual, filed as
  one product named "Surface Pro and Surface Pro 2". A question naming
  "Surface Pro 2" matched no product, so the comparison searched only the
  Surface Pro 3 and refused. Fixed on this branch: a product whose name lists
  several models now answers to each model's name (`main._listed_models`,
  test `tests/test_listed_models.py`). Both re-asked and right.

### Huawei 8/10

| # | Question (short) | Result |
|---|---|---|
| H1 | ECG or diving on the WATCH GT 5, or only the GT 5 Pro? | Pass: both GT 5 Pro only |
| H2 | WATCH GT 4 is 5 ATM: dive 50 m? | Pass: 50 m static pressure for 10 minutes, not a 50 m dive; scuba listed as not allowed (pp.42, 48) |
| H3 | Follow-up: what to do after swimming | Pass: clean, dry, holes, drainage feature |
| H4 | Which AX3 has NFC, WS7100 or WS7200? | Pass: WS7200 |
| H5 | AX3 light steady red | Pass: not connected; cable or broadband error |
| H6 | Factory reset the AX3 | Pass: pin on RESET over 2 s, red light = restored, reconfigure |
| H7 | Add a second Huawei router wirelessly with the H button | **Fail.** Copied five loose lines from the indicator table instead of the steps |
| H8 | AX3 maximum speed in Mbps (not in the guide) | Pass: refused |
| H9 | "How do I reset it?" (vague; two watches and a router) | **Fail.** Answered for the router without asking which device |
| H10 | Charge the WATCH GT 4 and know when it's full | Pass: steps, full at 100% |

- **H7:** The answer came from the shortcut that copies a checklist straight
  out of the page, with no model call. It picked the indicator table's lines,
  not the add-on router steps on page 11 (place within 1 m, press H when the
  light flashes, wait for the second router's light).
- **H9:** The vague-question check did not fire although the results came
  from all three products; the answer assumed the router.

## What was removed as ITL-specific

Removed from `main` (all of it stays on `experimental/v17.3`):

- **Seed data.** The starting catalogue (Note Validators, Coin Hoppers,
  Biometrics, MyCheckr products) is now one empty "Products" range. The
  product-family table (SMART Hopper, SMART Coin System, NV200, NV9, ICU) now
  starts empty; families are set up in the console or drafted automatically.
- **Defaults.** Sign-in accounts were limited to `@innovative-technology.com`
  by default; now any domain unless set. Confluence kept links to
  `innovative-technology.com` by default; now no host unless set.
- **Rules that only fired on ITL wording.** "validator" and "hopper" in the
  device-noun and "which products" patterns; MyConnect and MyCheckr in the
  camel-case guard, the domain vocabulary and the checklist terms; a
  hardcoded MyCheckr/MyConnect source-name fallback (the catalogue supplies
  the names). The cross-product pattern now accepts "which of your
  <anything plural>", so "which of your watches" works the way "which of your
  validators" did.
- **Examples made neutral, logic kept.** The answer prompt's table, heading,
  short-name and IP examples; the follow-up rewriter's examples; the routing
  examples; console placeholders and hints; the widget and WordPress plugin
  text; the streaming test page and motion demo; the step-by-step comparison
  self-check. Comments in the code that record measurements on ITL questions
  were left as they are: they explain why a rule exists and change nothing.
- **Files.** `src/legacy` (old release archives), the ITL eval and blind
  question sets, their labels and baselines, `BENCHMARKS.md`,
  `test_queries.py`, the September stress-test transcripts, the 7-to-10
  upgrade plan, the decision-model trial, the website brief, and the office
  lab tools that ran on the ITL corpus. `PENDING.md` on `main` is now the
  general product's plan; ITL's plan stays on v17.3.
- **Tests.** No test needed the ITL documents. Tests that relied on the ITL
  seeds now bring their own sample data. They still use ITL product names as
  made-up test data, which does not depend on ITL's documents. Two
  ITL-only tests (camel-case for MyConnect and MyCheckr) were removed.
  Result: 413 of 414 pass, 1 skipped (the browser UI test, skipped before
  too).

Not changed: the README screenshots still show the ITL ranges (Coin Hoppers,
Biometrics, Note Validator) and need re-taking on a neutral demo corpus.

## General fixes to port back to v17.3

1. **A product named for several models answers to each name**
   (`main._listed_models`, `tests/test_listed_models.py`). Worth porting:
   any ITL product filed under a combined name gains the same.
2. **"which of your <plural>" as a cross-product question** (`sales._CROSS`).
   Optional on v17.3: there, "validators" and "hoppers" are already listed.

## Open (found here, not fixed; the same code is on v17.3)

1. **A comparison refused because the draft misread one side (T1).** Both
   products' pages were retrieved; the draft said a figure was missing that
   was in its passages. Repeated 2/2. The checker stops the wrong claim, so
   it fails safe, but the customer loses a correct answer.
2. **The copy-the-checklist shortcut picks the wrong lines (H7).** For a
   multi-step question it copied an unrelated table instead of the
   procedure. It answered in 2 seconds with no model call and no check.
3. **A vague question across products is answered for one of them (H9).**
   "How do I reset it?" with a watch range and a router got the router's
   reset.
4. **A procedure answer dropped the key step (T10).** The answer said "save
   it" without saying how (hold the button until the chime).
5. **README screenshots** show ITL ranges.
