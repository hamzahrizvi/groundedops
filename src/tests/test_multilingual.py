"""Questions in other languages: searched in English, answered in theirs.

From the stress test of 2026-09-25 (conversation 24): a Spanish and a German
question were refused because retrieval is English, and a French price
question skipped the sales deflect because the commercial rule reads
English. language.py translates at the edges; these pin the edges.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import language                     # noqa: E402
import _harness                     # noqa: E402,F401
import main                         # noqa: E402


def _english_questions():
    src = os.path.dirname(HERE)
    out = []
    for f in glob.glob(os.path.join(src, "eval_cases*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for c in (d if isinstance(d, list) else d.get("cases", [])):
            # A case that says it is not English is asked in that language
            # on purpose (8.15's Spanish price question).
            if isinstance(c, dict) and c.get("language", "en") == "en":
                for k in ("question", "q", "query"):
                    if isinstance(c.get(k), str):
                        out.append(c[k])
    for f in glob.glob(os.path.join(HERE, "scenarios", "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        if not d["id"].startswith("24_"):
            out += [t["q"] for t in d["turns"]]
    return out


def test_no_english_question_in_the_eval_sets_looks_foreign():
    """THE COST GUARD. A false positive spends two model calls on an English
    turn, so the detector must stay silent on every English question we
    hold. 364 at the time of writing."""
    qs = _english_questions()
    assert len(qs) > 100
    fired = [q for q in qs if language.looks_foreign(q)]
    assert not fired, fired[:5]


def test_tricky_english_is_not_foreign():
    # "El Paso office hours" DOES fire (one foreign word, no English
    # function word). That is the accepted trade: it costs one model call,
    # which answers "English", and the turn is otherwise untouched.
    for q in ("hello", "thanks", "die cut bezel options",
              "de-jam the note path", "what is the price of the café model",
              "the NV9 blinks red", "www.example.com contact page"):
        assert not language.looks_foreign(q), q


def test_the_stress_test_questions_look_foreign():
    for q in ("¿Cómo limpio el BV30?",
              "Wie setze ich den NV9USB+ auf Werkseinstellungen zurück?",
              "Quel est le prix du NV9USB+ ?",
              "Der NV9 blinkt rot", "mi BV30 no funciona", "hola",
              "quiero hablar con una persona",
              "Il mio BV30 non accetta le banconote",
              "Hoe reset ik de NV9USB+?",
              "Как почистить BV30?", "NV9USB+ の価格はいくらですか"):
        assert language.looks_foreign(q), q


def test_to_english_parses_and_declines_english(monkeypatch=None):
    real = language._generate
    try:
        language._generate = lambda p, k: ("LANGUAGE: Spanish\n"
                                           "ENGLISH: How do I clean the BV30?")
        assert language.to_english("¿Cómo limpio el BV30?") == (
            "Spanish", "How do I clean the BV30?")
        # The model is the real judge: if it says English, nothing happens.
        language._generate = lambda p, k: "LANGUAGE: English\nENGLISH: hola"
        assert language.to_english("hola") is None
        language._generate = lambda p, k: "Sure! Here is the translation."
        assert language.to_english("hola") is None
        language._generate = lambda p, k: None
        assert language.to_english("hola") is None
    finally:
        language._generate = real


def test_product_codes_are_kept_in_the_prompt():
    seen = {}
    real = language._generate
    try:
        def fake(prompt, keys):
            seen["p"] = prompt
            return "LANGUAGE: German\nENGLISH: How do I reset the NV9USB+?"
        language._generate = fake
        language.to_english("Wie setze ich den NV9USB+ zurück?")
        assert "NV9USB+" in seen["p"]
        assert "model numbers" in seen["p"]
    finally:
        language._generate = real


def test_a_translation_that_drops_content_is_rejected():
    real = language._generate
    try:
        long_en = "1. Power off the unit. 2. Remove the bezel. 3. Wipe the sensors."
        language._generate = lambda p, k: "Apague."
        assert language.from_english(long_en, "Spanish") is None
        language._generate = lambda p, k: ("<answer>\n1. Apague la unidad. 2. Retire "
                                           "el bisel. 3. Limpie los sensores.\n</answer>")
        assert language.from_english(long_en, "Spanish").startswith("1. Apague")
    finally:
        language._generate = real


def _with_pipeline(fake_pipeline, fake_generate):
    # The harness never finishes loading models, so capability("ask") is
    # False there and the wrapper would pass everything straight through.
    main.capability = lambda name: True
    real_p, real_g = main.query, language._generate
    main.query, language._generate = fake_pipeline, fake_generate
    return real_p, real_g


def test_the_pipeline_runs_on_the_english_and_the_answer_comes_back_translated():
    seen = {}

    def pipeline(payload, x_user_id=None):
        seen["q"] = payload.q
        return {"answer": "Wipe the sensors with a dry cloth.", "role": "accurate"}

    def generate(prompt, keys):
        if prompt.startswith("A customer wrote"):
            return "LANGUAGE: Spanish\nENGLISH: How do I clean the BV30?"
        return "Limpie los sensores con un paño seco."

    real_p, real_g = _with_pipeline(pipeline, generate)
    try:
        out = main.query_any_language(main.QueryRequest(q="¿Cómo limpio el BV30?"))
    finally:
        main.query, language._generate = real_p, real_g
    assert seen["q"] == "How do I clean the BV30?"
    assert out["answer"] == "Limpie los sensores con un paño seco."
    assert out["answer_english"] == "Wipe the sensors with a dry cloth."
    assert out["language"] == "Spanish"
    assert out["question_original"] == "¿Cómo limpio el BV30?"


def test_a_german_refusal_also_comes_back_translated():
    """8.14's other half: "spot ... refusals" too, not only answers. The
    wrapper translates result["answer"] regardless of role, so a refusal
    the English pipeline decided on is translated the same way a real
    answer is -- this pins that a German refusal is not left in English."""
    seen = {}

    def pipeline(payload, x_user_id=None):
        seen["q"] = payload.q
        return {"answer": "I don't have that in the documentation.",
                "role": "rejected", "offer_support": True}

    def generate(prompt, keys):
        if prompt.startswith("A customer wrote"):
            return "LANGUAGE: German\nENGLISH: Does the BV30 have Bluetooth?"
        return "Das steht nicht in der Dokumentation."

    real_p, real_g = _with_pipeline(pipeline, generate)
    try:
        out = main.query_any_language(
            main.QueryRequest(q="Hat der BV30 Bluetooth?"))
    finally:
        main.query, language._generate = real_p, real_g
    assert seen["q"] == "Does the BV30 have Bluetooth?"
    assert out["role"] == "rejected" and out["offer_support"] is True
    assert out["answer"] == "Das steht nicht in der Dokumentation."
    assert out["answer_english"] == "I don't have that in the documentation."
    assert out["language"] == "German"


def test_english_makes_no_model_call():
    calls = []

    def pipeline(payload, x_user_id=None):
        return {"answer": "ok", "q": payload.q}

    def generate(prompt, keys):
        calls.append(prompt)
        return "LANGUAGE: Spanish\nENGLISH: x"

    real_p, real_g = _with_pipeline(pipeline, generate)
    try:
        out = main.query_any_language(main.QueryRequest(q="How do I clean the BV30?"))
    finally:
        main.query, language._generate = real_p, real_g
    assert calls == [] and out["q"] == "How do I clean the BV30?"
    assert "language" not in out


def test_a_failed_translation_leaves_the_turn_as_it_was():
    seen = {}

    def pipeline(payload, x_user_id=None):
        seen["q"] = payload.q
        return {"answer": "No encontré eso.", "role": "rejected"}

    real_p, real_g = _with_pipeline(pipeline, lambda p, k: None)
    try:
        out = main.query_any_language(main.QueryRequest(q="¿Cómo limpio el BV30?"))
    finally:
        main.query, language._generate = real_p, real_g
    assert seen["q"] == "¿Cómo limpio el BV30?"
    assert out["answer"] == "No encontré eso." and "language" not in out


def test_the_french_price_question_reaches_the_commercial_rule_in_english():
    """The deflect itself reads English; what matters is that the
    translation is what it reads."""
    import sales
    assert not sales.is_commercial_question("Quel est le prix du NV9USB+ ?")
    assert sales.is_commercial_question("What is the price of the NV9USB+?")


def test_spanish_and_german_price_questions_also_look_foreign_and_translate():
    """8.14: the first multilingual step is spotting a price question in
    each of the three named languages, not just French. The deflect is one
    English-only rule (sales.is_commercial_question) reached through the
    same to_english() edge as any other question -- these pin that a
    Spanish or German price question is recognised as foreign, and that
    ITS English translation (what a real translator would produce) is what
    the commercial rule actually sees."""
    import sales
    es_q, es_en = "¿Cuál es el precio del NV9S?", "What is the price of the NV9S?"
    de_q, de_en = "Was kostet der BV30?", "What does the BV30 cost?"
    for q in (es_q, de_q):
        assert language.looks_foreign(q), q
    assert not sales.is_commercial_question(es_q)
    assert not sales.is_commercial_question(de_q)
    assert sales.is_commercial_question(es_en)
    assert sales.is_commercial_question(de_en)


def test_translated_requests_for_a_person_are_handoffs():
    """"Ich möchte mit einem Mitarbeiter sprechen" comes back from the
    translator as "...speak to an employee", which the handoff rule did not
    know (found in the live pairs run of 2026-09-27)."""
    import intents
    for q in ("I would like to speak to an employee",
              "I want to speak with a staff member",
              "Can I talk to a member of your team"):
        assert intents.is_handoff_request(q), q
