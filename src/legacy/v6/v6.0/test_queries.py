import requests
import time
import uuid

URL = "http://127.0.0.1:8000/query"

SESSION_ID = str(uuid.uuid4())

# Queries that are EXPECTED to be refused (correct behaviour, not a failure)
EXPECTED_REJECTIONS = {
    "what is the capital of france",
}

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
]


def run_tests():
    print("=" * 65)
    print(f"RAG SYSTEM TEST RUN  (session={SESSION_ID[:8]}...)")
    print("=" * 65)

    answered = 0
    not_found = 0
    expected_rejected = 0
    errors = 0

    for i, q in enumerate(queries, 1):
        expected_reject = q in EXPECTED_REJECTIONS
        label = "(expected rejection) " if expected_reject else ""
        print(f"\n[{i:02d}] {label}Query : {q}")
        print("     " + "-" * 55)

        try:
            response = requests.post(
                URL, json={"q": q, "session_id": SESSION_ID}, timeout=180
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
            reason        = data.get("reason", "")
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

            is_refusal = "could not find" in answer.lower() or "unable" in answer.lower()

            if expected_reject:
                if is_refusal or role == "rejected":
                    expected_rejected += 1
                else:
                    not_found += 1
                    print("     WARN: Expected rejection but got an answer")
            elif is_refusal or role == "rejected":
                not_found += 1
                print("     FAIL: Unexpected refusal")
            else:
                answered += 1

        except Exception as e:
            print(f"     ERROR  : {e}")
            errors += 1

        time.sleep(1)

    print("\n" + "=" * 65)
    print(f"  answered={answered}  expected_rejected={expected_rejected}"
          f"  unexpected_refusals={not_found}  errors={errors}  total={len(queries)}")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
