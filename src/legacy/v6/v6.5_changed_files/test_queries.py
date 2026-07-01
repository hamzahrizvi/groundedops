import os
import requests
import time
import uuid

URL = "http://127.0.0.1:8000/query"

SESSION_ID = str(uuid.uuid4())

# DeepSeek key comes from the environment (per project decision). Set it
# before running, e.g.  (PowerShell)  $env:DEEPSEEK_API_KEY="sk-..."
# The forced-DeepSeek query below is the only one that requires it; if
# it's unset that single query is reported as SKIPPED rather than failed,
# so the rest of the suite still runs without a key.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Queries that are EXPECTED to be refused (correct behaviour, not a failure)
EXPECTED_REJECTIONS = {
    "what is the capital of france",
}

# Queries that are EXPECTED to come back as a clarifying question rather
# than either a flat refusal or an answer. Two distinct kinds:
#   1. in-context follow-ups whose rewritten query still doesn't retrieve
#      (reason=low_retrieval_confidence_followup)
#   2. standalone but under-specified in-domain queries — they clearly
#      concern this domain (device/registration/installer/system) but
#      never say WHICH device/product, so retrieval misses and the right
#      response is to ask which one, not a flat "not found"
#      (reason=ambiguous_in_domain_query)
EXPECTED_CLARIFICATIONS = {
    "give me step 1 from that",
    "is there anything else? i have checked the above and they are alright",
    "explain why device registration might fail",
    "post installation verification installer sign off",
    "give me steps to install and verify system is working",
}

# The one query we deliberately force onto DeepSeek (force_provider/
# force_model), so every run exercises the DeepSeek path end-to-end and
# we can confirm a real remote answer comes back — the "I've never seen
# an answer from DeepSeek" gap. Requires DEEPSEEK_API_KEY to be set.
FORCED_DEEPSEEK_QUERY = "explain how myconnect and mycheckr communicate over rest"

queries = [
    "give me the checklist before leaving site after installation",
    "default login credentials for myconnect",
    "what is mycheckr",
    "what is the hub ip",
    "how does myconnect work with mycheckr",
    "how to connect tablet to hub",
    # Real follow-up — "step 1" is a clear reference marker, should
    # trigger condensation and resolve against the previous query
    "give me step 1 from that",
    "why is multicast required for hub discovery",
    "explain why device registration might fail",
    "what is the capital of france",
    "post installation verification installer sign off",
    "introduction of myconnect system",
    "give me steps to install and verify system is working",

    # ── Conversational follow-up sequence (the bug from this session) ──
    # Step 1: set real context with a question the corpus DOES answer.
    "explain why MyCheckr registration might fail when connecting it to MyConnect",
    # Step 2: a genuine in-context follow-up ("the above" = clear
    # reference marker, history exists) whose rewritten query still
    # won't retrieve cleanly against the corpus. Before the fix this came
    # back as the identical flat "I could not find that in the knowledge
    # base." used for "capital of France" — indistinguishable from a
    # completely out-of-domain query and the source of the "conversation
    # style isn't working" complaint. Should now be role=clarify.
    "is there anything else? i have checked the above and they are alright",
    # Step 3: negative control — capital of France AGAIN, but now late in
    # the session with plenty of unrelated history present. Must stay a
    # flat rejection, NOT get treated as a follow-up just because history
    # exists. This is the regression check that the fix isn't over-firing.
    "what is the capital of france",
]



def run_tests():
    print("=" * 65)
    print(f"RAG SYSTEM TEST RUN  (session={SESSION_ID[:8]}...)")
    print("=" * 65)

    answered = 0
    not_found = 0
    expected_rejected = 0
    clarified = 0
    errors = 0

    for i, q in enumerate(queries, 1):
        expected_reject = q in EXPECTED_REJECTIONS
        expected_clarify = q in EXPECTED_CLARIFICATIONS
        label = "(expected rejection) " if expected_reject else ("(expected clarify) " if expected_clarify else "")
        print(f"\n[{i:02d}] {label}Query : {q}")
        print("     " + "-" * 55)

        try:
            response = requests.post(
                URL,
                json={
                    "q": q,
                    "session_id": SESSION_ID,
                    "deepseek_api_key": DEEPSEEK_API_KEY,
                },
                timeout=180,
            )
            response.raise_for_status()
            data = response.json()

            answer        = data.get("answer", "")
            model         = data.get("model", "none")
            role          = data.get("role", "none")
            provider      = data.get("provider", "")
            grounding     = data.get("grounding_score")
            flagged       = data.get("flagged", False)
            fallback_used = data.get("fallback_used", False)
            escalated     = data.get("escalated_to_deepseek", False)
            reason        = data.get("reason", "")
            needs_clarify = data.get("needs_clarification", False)
            resolved      = data.get("resolved_query")
            ret_score     = data.get("retrieval_score")
            timing        = data.get("timing", {})
            sources       = [s.get("source", "") for s in data.get("sources", [])]

            preview = answer[:220] + ("..." if len(answer) > 220 else "")
            print(f"     Answer : {preview}")

            model_line = f"     Model  : {model}  |  Role: {role}"
            if provider:
                model_line += f"  |  Provider: {provider}"
            if fallback_used:
                model_line += "  FALLBACK"
            if escalated:
                model_line += "  ESCALATED→DeepSeek"
            print(model_line)

            if resolved:
                print(f"     Resolved: {resolved}")

            if ret_score is not None:
                print(f"     Retrieval score: {ret_score:.4f}")

            if grounding is not None:
                flag_str = "  FLAGGED" if flagged else "  grounded"
                print(f"     Ground : {grounding:.3f}{flag_str}")

            if reason:
                print(f"     Reason : {reason}")

            if sources:
                print(f"     Sources: {', '.join(s for s in sources if s)}")

            if timing:
                t   = timing.get("total_time", 0)
                llm = timing.get("llm_time", 0)
                ret = timing.get("retrieval_time", 0)
                print(f"     Timing : total={t:.2f}s  llm={llm:.2f}s  retrieval={ret:.2f}s")

            is_clarify = role == "clarify" or needs_clarify
            is_refusal = (not is_clarify) and (
                "could not find" in answer.lower() or "unable" in answer.lower()
            )

            if expected_clarify:
                if is_clarify:
                    clarified += 1
                else:
                    not_found += 1
                    print("     FAIL: Expected a clarifying question but got a different outcome")
            elif expected_reject:
                if is_refusal or role == "rejected":
                    expected_rejected += 1
                else:
                    not_found += 1
                    print("     WARN: Expected rejection but got an answer")
            elif is_clarify:
                not_found += 1
                print("     FAIL: Unexpected clarification request (treated a standalone query as a follow-up)")
            elif is_refusal or role == "rejected":
                not_found += 1
                print("     FAIL: Unexpected refusal")
            else:
                answered += 1

        except Exception as e:
            print(f"     ERROR  : {e}")
            errors += 1

        time.sleep(1)

    # ── Dedicated forced-DeepSeek check ──────────────────────────────
    # Verifies the DeepSeek path actually returns a real remote answer,
    # end-to-end, by force-routing one query straight to it (no local
    # model, no fallback). Skipped (not failed) if no key is configured.
    print(f"\n[DS] (forced DeepSeek) Query : {FORCED_DEEPSEEK_QUERY}")
    print("     " + "-" * 55)
    deepseek_ok = None
    if not DEEPSEEK_API_KEY:
        print("     SKIP: DEEPSEEK_API_KEY not set in environment")
    else:
        try:
            response = requests.post(
                URL,
                json={
                    "q": FORCED_DEEPSEEK_QUERY,
                    "session_id": str(uuid.uuid4()),  # isolate from convo history
                    "deepseek_api_key": DEEPSEEK_API_KEY,
                    "force_provider": "deepseek",
                    "force_model": "deepseek-chat",
                },
                timeout=180,
            )
            response.raise_for_status()
            data = response.json()
            answer   = data.get("answer", "")
            provider = data.get("provider", "")
            model    = data.get("model", "")
            grounding = data.get("grounding_score")

            preview = answer[:220] + ("..." if len(answer) > 220 else "")
            print(f"     Answer : {preview}")
            print(f"     Model  : {model}  |  Provider: {provider}")
            if grounding is not None:
                print(f"     Ground : {grounding:.3f}")

            # Success = came back from deepseek with real content that
            # isn't the not-found/failure sentinel.
            answer_l = answer.lower()
            deepseek_ok = (
                provider == "deepseek"
                and bool(answer.strip())
                and "could not find" not in answer_l
                and "unable to generate" not in answer_l
            )
            if deepseek_ok:
                print("     PASS: real DeepSeek answer received")
            else:
                print(f"     FAIL: expected a DeepSeek answer, got provider={provider!r}")
        except Exception as e:
            print(f"     ERROR  : {e}")
            deepseek_ok = False

    print("\n" + "=" * 65)
    ds_str = ("skipped" if deepseek_ok is None else ("ok" if deepseek_ok else "FAIL"))
    print(f"  answered={answered}  expected_rejected={expected_rejected}"
          f"  clarified={clarified}  unexpected_refusals={not_found}"
          f"  errors={errors}  deepseek={ds_str}  total={len(queries)}")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
