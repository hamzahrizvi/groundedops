"""Multilingual curated FAQs for anonymous visitors (Phase 1 of PENDING.md's
9.9 -- "full multilingual answers, behind a switch"). The anonymous widget
path never reaches the LLM at all, so a visitor's language has to be
matched with no model call: an entry now carries a `language` field
(default "en" when absent, so every pre-existing entry is untouched), and
`list_for_product`/`suggest_candidates` take an additive `language` filter
that every OLD caller keeps getting None (no filtering) from by default.

These tests pin the store-side half of that feature: the schema fields,
scoping, staleness detection for a translation whose English source has
since changed, and gap tracking by language. The translation call itself
(language.translate_faq_pair) and the no-LLM language guess
(language.detect_language_no_llm) are tested in test_multilingual.py,
which already owns everything in language.py.
"""
import _harness  # noqa: F401 -- scratch FAQ_STORE_PATH/FAQ_GAP_PATH before import

import faq_store


def test_norm_lang_accepts_bare_codes_and_rejects_everything_else():
    assert faq_store.norm_lang("es") == "es"
    assert faq_store.norm_lang("ES") == "es"
    assert faq_store.norm_lang("es-MX") == "es"
    assert faq_store.norm_lang("Spanish") == ""
    assert faq_store.norm_lang("") == ""
    assert faq_store.norm_lang(None) == ""


def test_hash_answer_is_stable_and_sensitive_to_content():
    a = faq_store.hash_answer("Use a dry cloth.")
    b = faq_store.hash_answer("Use a dry cloth.")
    c = faq_store.hash_answer("Use a damp cloth.")
    assert a == b and a != c


def test_list_for_product_language_none_is_unfiltered():
    """The default for every caller written before this field existed --
    it must keep seeing the whole pool, mixed languages and all."""
    en = faq_store.add_entry("Does it need WiFi?", "No.", products="nv9")
    faq_store.merge_questions("doc.pdf", "nv9",
                              [{"question": "¿Necesita wifi?", "answer": "No.",
                                "language": "es"}])
    items = faq_store.list_for_product("nv9")
    ids = {it["id"] for it in items}
    assert en["id"] in ids
    es = [it for it in items if it.get("language") == "es"]
    assert len(es) == 1 and es[0]["question"] == "¿Necesita wifi?"


def test_list_for_product_scopes_by_language_when_asked():
    faq_store.add_entry("What is the weight?", "1.05 kg", products="nv9s")
    faq_store.merge_questions("doc.pdf", "nv9s",
                              [{"question": "¿Cuál es el peso?", "answer": "1.05 kg",
                                "language": "es"}])
    en_only = faq_store.list_for_product("nv9s", language="en")
    es_only = faq_store.list_for_product("nv9s", language="es")
    assert all((it.get("language") or "en") == "en" for it in en_only)
    assert all(it.get("language") == "es" for it in es_only)
    assert any(it["question"] == "What is the weight?" for it in en_only)
    assert any(it["question"] == "¿Cuál es el peso?" for it in es_only)
    assert not any(it["question"] == "¿Cuál es el peso?" for it in en_only)


def test_suggest_candidates_does_not_cross_languages():
    """A Spanish entry must never surface for an English-scoped call, and
    vice versa -- scoring one language's words against another's is exactly
    the wrong-answer failure mode suggest_candidates exists to prevent."""
    faq_store.add_entry("How do I clean the BV30?", "Wipe with a dry cloth.",
                        products="bv30")
    faq_store.merge_questions("doc.pdf", "bv30",
                              [{"question": "¿Cómo limpio el BV30?",
                                "answer": "Limpie con un paño seco.",
                                "language": "es"}])

    out_en = faq_store.suggest_candidates(
        "How do I clean the BV30?", "bv30", record=False, language="en")
    assert out_en["mode"] == "answer", out_en
    assert (out_en["entry"].get("language") or "en") == "en"

    out_es = faq_store.suggest_candidates(
        "¿Cómo limpio el BV30?", "bv30", record=False, language="es")
    assert out_es["mode"] == "answer", out_es
    assert out_es["entry"].get("language") == "es"

    # Asking the SPANISH question but scoped to English finds nothing to
    # auto-serve -- the Spanish entry is filtered out of the pool entirely,
    # not merely ranked below the (absent) English one.
    out_miss = faq_store.suggest_candidates(
        "¿Cómo limpio el BV30?", "bv30", record=False, language="en")
    assert out_miss["mode"] != "answer" or (out_miss["entry"].get("language") or "en") == "en"


def test_is_stale_translation():
    en = faq_store.add_entry("What is the operating speed?", "12 notes/sec",
                             products="scs")
    merged = faq_store.merge_questions("doc.pdf", "scs", [{
        "question": "¿Cuál es la velocidad de funcionamiento?",
        "answer": "12 billetes/seg", "language": "es",
        "translation_of": en["id"],
        "source_answer_sha256": faq_store.hash_answer(en["answer"]),
    }])
    assert merged["added"] == 1
    translated = [it for it in faq_store.list_for_product("scs", language="es")
                  if it.get("translation_of") == en["id"]][0]

    # Fresh: the source hasn't changed since the translation was made.
    assert not faq_store.is_stale_translation(translated)

    # The English source is edited -- the translation is now stale.
    faq_store.update_entry(en["id"], answer="12.5 notes/sec")
    assert faq_store.is_stale_translation(translated)

    # The source is gone entirely -- also stale (nothing to compare against).
    faq_store.delete_entry(en["id"])
    assert faq_store.is_stale_translation(translated)


def test_merge_questions_upserts_a_translation_by_source_and_language():
    en = faq_store.add_entry("Does it support Bluetooth?", "No.", products="mini")
    r1 = faq_store.merge_questions("doc.pdf", "mini", [{
        "question": "¿Soporta Bluetooth?", "answer": "No.", "language": "es",
        "translation_of": en["id"],
        "source_answer_sha256": faq_store.hash_answer(en["answer"]),
    }])
    assert r1["added"] == 1 and r1.get("updated", 0) == 0

    # Re-translating (e.g. after the English source changed) REPLACES the
    # existing translation in place rather than duplicating it.
    r2 = faq_store.merge_questions("doc.pdf", "mini", [{
        "question": "¿Tiene Bluetooth?", "answer": "No, no lo tiene.",
        "language": "es", "translation_of": en["id"],
        "source_answer_sha256": faq_store.hash_answer(en["answer"]),
    }])
    assert r2.get("updated", 0) == 1 and r2["added"] == 0
    es_entries = [it for it in faq_store.list_for_product("mini", language="es")
                  if it.get("translation_of") == en["id"]]
    assert len(es_entries) == 1
    assert es_entries[0]["question"] == "¿Tiene Bluetooth?"


def test_merge_questions_keeps_a_hand_edited_translation():
    """edited=True is sacred everywhere else in this file (an admin's
    correction is never silently overwritten by regeneration) -- a
    re-translate must not be the one place that breaks that rule."""
    en = faq_store.add_entry("Is it waterproof?", "No, indoor use only.",
                             products="mycheckr")
    faq_store.merge_questions("doc.pdf", "mycheckr", [{
        "question": "¿Es resistente al agua?", "answer": "No, solo interior.",
        "language": "es", "translation_of": en["id"],
        "source_answer_sha256": faq_store.hash_answer(en["answer"]),
    }])
    translated = [it for it in faq_store.list_for_product("mycheckr", language="es")
                  if it.get("translation_of") == en["id"]][0]
    faq_store.update_entry(translated["id"], answer="No, solo para uso en interiores.")

    r = faq_store.merge_questions("doc.pdf", "mycheckr", [{
        "question": "¿Es a prueba de agua?", "answer": "wrong machine translation",
        "language": "es", "translation_of": en["id"],
        "source_answer_sha256": faq_store.hash_answer(en["answer"]),
    }])
    assert r.get("skipped_edited", 0) == 1
    kept = [it for it in faq_store.list_for_product("mycheckr", language="es")
            if it.get("translation_of") == en["id"]][0]
    assert kept["answer"] == "No, solo para uso en interiores."


def test_gaps_are_tagged_and_filtered_by_language():
    faq_store.record_gap("Was kostet der BV30?", "bv30", language="de")
    faq_store.record_gap("What does the BV30 cost?", "bv30", language="en")
    faq_store.record_gap("Quel est le prix?", "bv30")  # no language passed -> "en"

    de_gaps = faq_store.list_gaps("bv30", language="de")
    en_gaps = faq_store.list_gaps("bv30", language="en")
    all_gaps = faq_store.list_gaps("bv30")

    assert {g["question"] for g in de_gaps} == {"Was kostet der BV30?"}
    assert {g["question"] for g in en_gaps} == {
        "What does the BV30 cost?", "Quel est le prix?"}
    assert len(all_gaps) == 3
    assert all(g.get("language") for g in all_gaps)


def test_cluster_gaps_does_not_merge_across_languages():
    de = {"id": "d1", "question": "Was kostet der BV30", "scope": "bv30",
          "language": "de", "times_asked": 3, "ts": 0, "resolved": False, "spam": False}
    en = {"id": "e1", "question": "Was kostet der BV30", "scope": "bv30",
          "language": "en", "times_asked": 2, "ts": 0, "resolved": False, "spam": False}
    clusters = faq_store.cluster_gaps([de, en], threshold=0.0)
    assert len(clusters) == 2, clusters
