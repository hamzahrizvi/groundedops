# GroundedOps — internal 10.14.0 (FAQ Q&A generation, cache-answers, model select, gating)

GO_v2.0 line. Base groundedops/. Files:
  src/main.py            (+ FAQ-cache short-circuit in /query; catalog doc_count)
  src/faq_store.py       (+ match_answer lexical matcher)
  src/frontend/src/api.js                       (generateFaqQA: Q&A + count + model)
  src/frontend/src/components/AdminPanel.jsx     (depth + model UI, Q&A review)
  src/frontend/src/App.jsx                        (clickable chips, docless gating, FAQ icon)
  src/frontend/src/styles.css                     (themed icon handle, docless styles)
Rebuild both: docker compose up -d --build

## 1. Generates ANSWERS with questions (+ depth selector)
Generate FAQ now produces question+answer pairs (strict-JSON prompt),
answers pre-filled and editable for manual review before save. Depth
selector: Basic (10) / Thorough (20) / Deep (30) / Custom(x).

## 2. Local/Ollama FAQ generation — dropped, as agreed (browser can't
reach Ollama). Providers are DeepSeek / OpenAI / Claude, browser-side.

## 3. Starter chips now CLICKABLE
Bug: useSuggestion() was referenced but undefined (I'd removed it), so
clicking a chip threw and did nothing. Restored — chips now ask the
question.

## 4. Similar-question answers come from saved FAQ (cache)
New faq_store.match_answer: on each /query, if the question closely
matches a saved FAQ that has a reviewed answer (lexical Jaccard >= 0.6,
scoped to the product/category), that curated answer is served directly —
instant, no LLM call, response tagged from_faq / model "faq-cache". Falls
through to normal RAG otherwise. This is the FAQ cache we'd planned.

## 5. FAQ side panel -> themed ICON
The right-edge handle is now a help icon that follows dark/light theme
(uses surface/accent vars; inverts on hover). Same slide-out drawer.

## 6. Block chat for products with NO documents
/catalog now returns doc_count per product AND category. The picker
disables (greys out, "· no documents") any product/category with zero
docs, so you can't start a chat that would only ever say "which product?"

## 7. Model selection — curated per provider
Provider dropdown + model dropdown in the FAQ generator:
  DeepSeek: deepseek-chat (default) / deepseek-reasoner (advanced)
  OpenAI:   gpt-4o-mini / gpt-4o / gpt-4.1
  Claude:   claude-3-5-haiku / claude-sonnet-4.5
(Edit the MODELS map in AdminPanel.jsx to adjust.)

## Caveats (honest)
- match_answer is LEXICAL (word overlap), not embeddings — great for
  near-duplicate phrasings, won't catch heavy paraphrases. If you want
  semantic matching I can wire it to your embeddings module (it wasn't in
  the packaged set, so I kept this dependency-free). Threshold 0.6 is
  tunable in faq_store.match_answer.
- Browser generation still depends on provider CORS; DeepSeek is safest,
  Claude uses the direct-browser-access header, OpenAI generally allows it.
- Generated answers are model output from the doc excerpt — REVIEW them
  before relying on the cache to serve them verbatim to customers.

## Verify
1. Admin > FAQ > pick Mini doc > provider+model+Deep(30) > Generate ->
   Q&A pairs appear with answers -> edit one -> Save.
2. New chat > docless product is greyed "· no documents", can't start.
3. Product chat > starter chip click -> asks it.
4. Ask a question close to a saved FAQ -> instant answer (from_faq).
5. FAQ icon on right edge follows your dark/light theme.
