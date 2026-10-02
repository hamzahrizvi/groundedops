"""Generates the ten conversation scenarios in this directory.

Kept so the set can be regenerated or extended in one place rather than by
hand-editing ten JSON files. Run: python tests/scenarios/_write_scenarios.py

Each scenario is one customer's sequential conversation about one product.
Per turn we record what is DETERMINISTICALLY decidable without a language
model -- the routing decisions the 2026-09-19 fixes changed:

  followup   is_followup_turn -> does a refusal on this turn ask a question
             back, or stop dead? The 17 Sep bug was this being True for
             fresh questions.
  commercial sales.is_commercial_question -> price/availability, which no
             manual can answer and which must reach the operator's deflect
             rather than the model (15aae05).
  markers    has_reference_markers -> whether condensation runs at all.
             Only asserted where it is the point of the turn.

Plus two scenario-level checks: the clarify buttons this product's manual
produces, and that an unscoped refusal mid-conversation does not offer
another product's questions.

Ground truth is what a support engineer reading the transcript would say,
written before running anything. Where the code disagrees, the runner
reports it as a failure rather than the label being softened.
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

# (question, followup, commercial, note) -- markers given as a 5th item only
# where the turn exists to test the condensation gate.
SCENARIOS = [
    {
        "id": "01_nv9_spectral_installer",
        "product": "NV9 Spectral",
        "product_key": "nv9_spectral",
        "source": "NV9 Spectral Range User Manual-v1.pdf",
        "persona": "Integrator speccing a kiosk build. Names parts.",
        "expect_labels": ["NV9 Spectral"],
        "turns": [
            ["what note denominations does the NV9 Spectral accept", False, False,
             "opening, fully self-contained"],
            ["what is the current draw on the 12V rail during a stacking cycle",
             False, False, "technical, no reference to the prior turn"],
            ["does it need a separate supply from the host board", True, False,
             "short, 'it' is doing referential work"],
            ["how much does the NV9S validator weigh on its own", False, False,
             "the RRF case from Aug -- still standalone"],
            ["and what about the bezel options", True, False,
             "'what about' opener"],
            ["what would one of these cost us at fifty units", True, True,
             "price -> operator deflect; also a follow-up, since 'these' "
             "needs the previous turn (labelled standalone at first, and "
             "the rule was right)"],
        ],
    },
    {
        "id": "02_nv9usb_retrofit",
        "product": "NV9USB+",
        "product_key": "nv9usb",
        "source": "NV9USB+ Range User Manual-v1.pdf",
        "persona": "Engineer retrofitting an existing machine.",
        "expect_labels": ["NV9USB+"],
        "turns": [
            ["can the NV9USB+ be mounted upside down in a wall unit", False, False,
             "orientation, standalone"],
            ["what baud rate does the serial link default to", False, False,
             "standalone"],
            ["is that configurable from the host or only in firmware", True, False,
             "'that' refers to the previous answer"],
            ["which pinout do I need for the IF5 interface cable", False, False,
             "standalone despite naming no product"],
            ["can two units share one RS232 bus", False, False,
             "contains no reference marker"],
            ["ok and what about powering them from one PSU", True, False,
             "stacked opener -- the gap found 19 Sep"],
        ],
    },
    {
        "id": "03_nv200s_vending_newcomer",
        "product": "NV200S",
        "product_key": "nv200s",
        "source": "NV200S Range User Manual-v1.pdf",
        "persona": "Vending operator, has never installed a validator.",
        "expect_labels": ["NV200S"],
        "turns": [
            ["I am putting these in coffee machines, will the NV200S fit",
             False, False, "'these' but the subject is named -- opening turn"],
            ["how long does it take to check a note", False, False,
             "'it' buried in a short question about the machine"],
            ["what happens if someone feeds it a torn fiver", False, False,
             "'it' = the machine, self-contained"],
            ["does it need cleaning very often", True, False,
             "short, pronoun, follows on"],
            ["and the cashbox, how big is that", True, False,
             "'and' opener plus 'that'"],
            ["where can I buy one and how soon can you deliver", False, True,
             "availability -> deflect"],
        ],
    },
    {
        "id": "04_bv30_small_retail",
        "product": "BV30",
        "product_key": "bv30",
        "source": "BV30 User Manual-v1.pdf",
        "persona": "Shop owner, first note validator, plain language.",
        "expect_labels": ["BV30"],
        "turns": [
            ["is the BV30 any good with the new polymer notes", False, False,
             "opening, names the product"],
            ["will it take really crumpled ones", True, False,
             "short, 'it' + 'ones' referring back"],
            ["how do I empty the cash out of it at the end of the day",
             False, False, "'it' buried in a long self-contained question"],
            ["is it noisy", True, False,
             "three words, pronoun -- unmistakably a follow-up"],
            ["are there other bezel options for the BV30", False, False,
             "names the product again, standalone"],
            ["what sort of price are we talking about", False, True,
             "price -> deflect"],
        ],
    },
    {
        "id": "05_smart_coin_system",
        "product": "SMART Coin System",
        "product_key": "sku_scs",
        "source": "SMART Coin System Range User Manual-v1.pdf",
        "persona": "Arcade operator. The RMS conversation, extended.",
        "expect_labels": ["SMART Coin System"],
        "turns": [
            ["what is the power draw just for RMS?", False, False,
             "[logged 1970] answered in production"],
            ["how about RMS", True, False,
             "[logged 1968] genuine follow-up, refused in production"],
            ["what chute option is available for debris collection", False, False,
             "standalone"],
            ["how many coins a second can it pay out", True, False,
             "short, pronoun"],
            ["does the hopper need emptying manually or does it self-level",
             False, False, "long, 'it' about the machine"],
            ["from step 2 onward is the wiring the same", True, False,
             "'from step' reference marker"],
        ],
    },
    {
        "id": "06_mycheckr_retail",
        "product": "MyCheckr",
        "product_key": "mycheckr",
        "source": "MyCheckr User Manual-v7.pdf",
        "persona": "Convenience store manager buying age verification.",
        "expect_labels": ["MyCheckr"],
        "turns": [
            ["what is MyCheckr used for", False, False, "opening"],
            ["does it need an internet connection in the shop", True, False,
             "short, pronoun"],
            ["can my staff use it without any training", False, False,
             "'it' buried, self-contained"],
            ["can I get the age of a person from the device", False, False,
             "names the device, standalone"],
            ["what about under-18s trying it twice", True, False,
             "'what about' opener"],
            ["are there any recurring fees for using it", False, True,
             "fees -> commercial deflect"],
        ],
    },
    {
        "id": "07_mycheckr_mini_nested_name",
        "product": "MyCheckr mini",
        "product_key": "mycheckr_mini",
        "source": "MyCheckr Mini User Manual-v5.pdf",
        "persona": "Customer who owns the full MyCheckr, asking about the mini. The nested-name trap.",
        "expect_labels": ["MyCheckr mini"],  # the catalogue spells it lowercase
        "turns": [
            ["how is the MyCheckr Mini different from the full size one",
             False, False, "comparison, standalone"],
            ["is the mini one any smaller to mount", True, False,
             "'the mini one' refers back"],
            ["what is the screen size on the MyCheckr Mini", False, False,
             "standalone, names the product"],
            ["and what about the two of them side by side", True, False,
             "set anaphora + stacked opener"],
            ["can both run off the same power brick", True, False,
             "'both' -- the case de5a37f added markers for"],
            ["which one would you recommend for a corner shop", True, False,
             "advisory: no manual answers it, but it IS contextual"],
        ],
    },
    {
        "id": "08_myconnect_installer",
        "product": "MyConnect",
        "product_key": "myconnect",
        "source": "MyConnect Environment-v4.pdf",
        "persona": "Field installer working through prerequisites on site.",
        "expect_labels": ["MyConnect"],
        "turns": [
            ["what are the prerequisites for the MyConnect app", False, False,
             "opening"],
            ["which ports need opening on the customer firewall", False, False,
             "standalone, technical"],
            ["do those need to be open both ways", True, False,
             "'those' sentence-initial"],
            ["the device registration keeps failing after a factory reset",
             False, False, "long, no pronoun -- a fresh problem statement"],
            ["it keeps failing even after I rebooted the tablet", True, False,
             "sentence-initial pronoun, 9 words -- was missed before 19 Sep"],
            ["is there a checklist I can print for the engineer", False, False,
             "standalone"],
        ],
    },
    {
        "id": "09_biometrics_api_developer",
        "product": "Biometrics / ICU",
        "product_key": "biometrics_general",
        "source": "ICU_Network_API-v1.0.50.pdf",
        "persona": "Developer integrating the ICU network API.",
        "expect_labels": [],
        "expect_labels_note": ("the ICU docs sit under 'General (shared docs)', "
                               "which is a bucket rather than a product, so there "
                               "is deliberately no button to offer"),
        "turns": [
            ["what authentication does the ICU network API use", False, False,
             "opening"],
            ["what does the age result payload actually contain", False, False,
             "standalone"],
            ["is that the same in the Linux environment guide", True, False,
             "'that' refers back"],
            ["how do I reach the device from a Linux box on the same subnet",
             False, False, "long, standalone"],
            ["what about rate limits", True, False, "'what about' opener"],
            ["can I get a quote for a hundred licences", False, True,
             "quote -> commercial deflect"],
        ],
    },
    {
        "id": "10_cross_product_and_coverage_gap",
        "product": "SCS + NV9 (and NV22S, which has no documents)",
        "product_key": None,
        "source": None,
        "persona": "Operator running two machines, then asking about one we hold nothing on.",
        "expect_labels": ["SMART Coin System", "NV9 Spectral"],
        "expect_labels_sources": ["SMART Coin System Range User Manual-v1.pdf",
                                  "NV9 Spectral Range User Manual-v1.pdf"],
        "turns": [
            ["I run a SCS and an NV9 Spectral on the same counter", False, False,
             "statement of context, no markers"],
            ["what is the power required to run both at once", True, False,
             "'both' -- the case that died before de5a37f"],
            ["can the two of them share one earth", True, False,
             "'the two' set anaphora"],
            ["what is the combined footprint on the bench", False, False,
             "standalone, though unanswerable from either manual alone"],
            ["do you do anything for the NV22S as well", False, False,
             "a product with NO documents -- must refuse honestly"],
            ["and what about it in a damp cellar", True, False,
             "stacked opener plus pronoun"],
        ],
    },
]


def main():
    for s in SCENARIOS:
        turns = []
        for t in s["turns"]:
            turn = {"q": t[0], "followup": t[1], "commercial": t[2], "note": t[3]}
            if len(t) > 4:
                turn["markers"] = t[4]
            turns.append(turn)
        out = {k: v for k, v in s.items() if k != "turns"}
        out["turns"] = turns
        path = HERE / f"{s['id']}.json"
        path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.name}  ({len(turns)} turns)")


if __name__ == "__main__":
    main()
