"""Questions in other languages: search in English, verify in English,
answer in the customer's language.

From the stress test of 2026-09-25 (docs/stress-test-2026-09-25.md,
conversation 24): "¿Cómo limpio el BV30?" and "Wie setze ich den NV9USB+ auf
Werkseinstellungen zurück?" were both refused, and "Quel est le prix du
NV9USB+ ?" was answered by the model instead of reaching the sales deflect.
None of that was the model's fault. The manuals are English, BM25 matches
words, the embedder is English, so a Spanish question scores below the
retrieval gate before any model sees it; and every rule downstream --
commercial, handoff, greeting, document request -- reads English.

So the pipeline is left English end to end, and the language is handled at
the two edges:

  1. looks_foreign() -- a cheap check with no model call, built never to fire
     on English. Only when it fires does anything below cost a model call.
  2. to_english() -- one call returns the language's name and an English
     translation. The unchanged pipeline then runs on the translation, so
     retrieval, the commercial deflect and the intents all see English.
  3. from_english() -- the finished, VERIFIED English answer is translated
     for display.

Why translate the answer afterwards instead of telling the model to answer
in Spanish (which was the report's first idea): the grounding check is
cross-encoder/nli-deberta-v3-small, an English NLI model. A Spanish answer
checked against English passages scores as unsupported, so correct answers
would be flagged or refused. Verifying the English and translating the
verified text keeps the guarantee where it is.

Every function here fails open: a failed translation leaves the turn
exactly as it was before this module existed.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

ENABLED = os.getenv("MULTILINGUAL", "1") != "0"

# Characters English support questions do not contain. One of these is not
# enough on its own ("café", "naïve", a pasted "°C"), so they only count
# together with foreign function words -- except whole non-Latin scripts.
_ACCENTED = re.compile(r"[áéíóúñ¿¡äöüßàèìòùâêîôûçãõœëïÿ]", re.I)
_NON_LATIN = re.compile(
    r"[Ѐ-ӿͰ-Ͽ֐-׿؀-ۿऀ-ॿ"
    r"฀-๿぀-ヿ㐀-鿿가-힯]")

# Function words, not vocabulary: a question in the language is full of
# them whatever it is about, and product names never are. Words that are
# also common English ("a", "in", "die", "son", "come", "per", "have", "no",
# "me") are left out on purpose -- this list only has to be sure, not
# complete, because the model call that follows decides for real.
_FOREIGN = frozenset("""
de el la los las lo un una unos unas y o pero porque como cómo que qué
cuál cuando cuándo dónde donde quién es está están son hay puedo puede
tengo tiene mi mis tu su sus por para con sobre muy más también esto
este esta ese esa hola gracias favor ayuda necesito quiero cuánto cuanto
der das den dem des ein eine einen einem einer und oder aber weil wie
wer wo wann warum ist sind habe hat ich du sie wir ihr mein meine nicht
kein keine mit ohne für auf bei nach von zu zum zur auch noch schon kann
können muss bitte danke hallo gerät funktioniert
le les des du au aux une et ou mais parce quoi qui où quand
pourquoi est sont avoir suis je tu il elle nous vous ils mon ma mes
votre vos ce cette ces pas avec sans sur dans chez aussi très peut
puis dois merci bonjour aide prix quel quelle
il lo gli della dello delle degli nel nella una uno e o ma perché cosa
chi dove quando sono ho hai abbiamo mio mia non con senza anche molto
posso può grazie ciao buongiorno aiuto
o os uma uns umas é são tem tenho meu minha não sem também
muito posso pode obrigado obrigada olá ajuda você
het een en maar omdat hoe wat wie waar wanneer waarom zijn heb heeft
ik jij hij zij wij mijn niet geen zonder voor bij naar ook nog
kan kunnen moet alstublieft bedankt hallo
""".split())
# "no" is left out: it is Spanish, Italian and Portuguese too, and here it
# would only cancel a foreign word ("mi BV30 no funciona").
_ENGLISH = frozenset("""
the a an and or but because how what which who where when why is are was
were be been do does did have has had i you he she we they my your our
their this that these those not with without for on in at by from to
of can could should would will please thanks thank hello hi help need
want it its there here my me
""".split())

_WORD = re.compile(r"[^\W\d_]+", re.U)


def looks_foreign(text: str) -> bool:
    """True when `text` is probably not English. No model call.

    Tuned to be SURE rather than complete: a false positive costs two model
    calls and a translation of an English answer into English (which the
    model call then reports as English, and nothing happens); a false
    negative leaves the turn as it was before this module. So it fires on
    a non-Latin script, or on foreign function words outnumbering English
    ones -- and a single foreign word is enough only when there is no
    English function word at all ("Der NV9 blinkt rot", "hola").

    Measured 2026-09-27 against every English question in the eval sets
    and scenarios (364): fires on none of them.
    """
    if not ENABLED or not text or not text.strip():
        return False
    if _NON_LATIN.search(text):
        return True
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return False
    foreign = sum(1 for w in words if w in _FOREIGN)
    english = sum(1 for w in words if w in _ENGLISH)
    if foreign <= english:
        return False
    return (foreign >= 2 or english == 0
            or bool(_ACCENTED.search(text)))


def _generate(prompt: str, api_keys: dict | None) -> str | None:
    import llm
    keys = api_keys or {}
    out = llm.generate_with_fallback("fast", prompt,
                                     deepseek_api_key=keys.get("deepseek"),
                                     api_keys=keys)
    if not out or out.get("provider") in (None, "none"):
        return None
    text = (out.get("text") or "").strip()
    return text or None


_LANG_LINE = re.compile(r"^\s*LANGUAGE\s*:\s*(.+?)\s*$", re.I | re.M)
_EN_LINE = re.compile(r"^\s*ENGLISH\s*:\s*(.+)", re.I | re.M | re.S)


def to_english(text: str, api_keys: dict | None = None) -> tuple[str, str] | None:
    """(language name, English translation), or None when the text is
    English after all or no model answered.

    Product names and model codes are kept as written: "NV9USB+" and "BV30"
    are what retrieval matches on, and a translator that "helpfully"
    expanded them would lose the one exact term in the question."""
    msg = " ".join((text or "").split())[:600]
    prompt = (
        "A customer wrote to a product-support assistant. Identify the "
        "language of the message and translate it into English.\n"
        "Keep product names, model numbers, error codes and numbers exactly "
        "as written. Translate the meaning; do not answer the question.\n\n"
        f"Message: {msg}\n\n"
        "Reply in exactly this format and nothing else:\n"
        "LANGUAGE: <the language's name in English>\n"
        "ENGLISH: <the English translation>")
    try:
        out = _generate(prompt, api_keys)
    except Exception as exc:
        logger.warning("question translation failed: %s", exc)
        return None
    if not out:
        return None
    lang, en = _LANG_LINE.search(out), _EN_LINE.search(out)
    if not lang or not en:
        logger.warning("question translation unparseable: %r", out[:120])
        return None
    language = lang.group(1).strip().strip(".").strip()
    english = en.group(1).strip().strip('"').strip()
    if not english or language.lower() in ("english", "en", "unknown"):
        return None
    return language, english


def from_english(text: str, language: str,
                 api_keys: dict | None = None) -> str | None:
    """`text` translated into `language`, or None on any failure (the
    caller then shows the English, which is correct, just not translated).

    The answer has already passed the grounding check in English, so the
    instruction is to translate and add nothing: no new facts, no dropped
    steps, and every code, number, unit, link and markdown mark unchanged."""
    if not text or not text.strip() or not language:
        return None
    prompt = (
        f"Translate this product-support answer into {language}.\n"
        "Rules: translate only -- add nothing, remove nothing, do not "
        "summarise. Keep product names, model numbers, error codes, "
        "numbers, units, URLs and markdown formatting (lists, bold, "
        "tables, links) exactly as they are. Output only the translation.\n\n"
        "<answer>\n" + text + "\n</answer>")
    try:
        out = _generate(prompt, api_keys)
    except Exception as exc:
        logger.warning("answer translation to %s failed: %s", language, exc)
        return None
    if not out:
        return None
    out = re.sub(r"^\s*<answer>\s*|\s*</answer>\s*$", "", out).strip()
    # A translation a fraction of the length dropped content; a wildly
    # longer one added some. Either way the English is the safer answer.
    if not (0.4 <= len(out) / max(len(text), 1) <= 2.5):
        logger.warning("answer translation to %s rejected: length %d vs %d",
                       language, len(out), len(text))
        return None
    return out
