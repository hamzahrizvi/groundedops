import requests
import time

URL = "http://127.0.0.1:8000/query"

queries = [
    # --- structured extraction ---
    "give me the checklist before leaving site after installation",

    # --- fast ---
    "default login credentials for myconnect",
    "what is mycheckr",
    "what is the hub ip",

    # --- accurate ---
    "how does myconnect work with mycheckr",
    "how to connect tablet to hub",

    # --- reasoning ---
    "why is multicast required for hub discovery",
    "explain why device registration might fail",

    # --- out-of-domain (should be rejected) ---
    "what is the capital of france",

    # --- edge / bad chunk ---
    "post installation verification installer sign off",
    "introduction of myconnect system",

    # --- mixed multi-step ---
    "give me steps to install and verify system is working",
]


def run_tests():
    print("=" * 65)
    print("RAG SYSTEM TEST RUN")
    print("=" * 65)

    passed = 0
    not_found = 0
    errors = 0

    for i, q in enumerate(queries, 1):
        print(f"\n[{i:02d}] Query : {q}")
        print("     " + "-" * 55)

        try:
            response = requests.post(URL, params={"q": q}, timeout=90)
            response.raise_for_status()
            data = response.json()

            answer         = data.get("answer", "")
            model          = data.get("model", "none")
            role           = data.get("role", "none")
            provider       = data.get("provider", "")
            grounding      = data.get("grounding_score")
            flagged        = data.get("flagged", False)
            fallback_used  = data.get("fallback_used", False)
            reason         = data.get("reason", "")
            timing         = data.get("timing", {})

            # Answer preview
            preview = answer[:220] + ("…" if len(answer) > 220 else "")
            print(f"     Answer : {preview}")

            # Model / role line
            model_line = f"     Model  : {model}  |  Role: {role}"
            if provider:
                model_line += f"  |  Provider: {provider}"
            if fallback_used:
                model_line += "  ⚠ FALLBACK"
            print(model_line)

            # Grounding line
            if grounding is not None:
                flag_str = "  ⚠ FLAGGED" if flagged else "  ✓ grounded"
                print(f"     Ground : {grounding:.3f}{flag_str}")

            # Rejection reason
            if reason:
                print(f"     Reason : {reason}")

            # Timing
            if timing:
                t = timing.get("total_time", 0)
                llm = timing.get("llm_time", 0)
                ret = timing.get("retrieval_time", 0)
                print(f"     Timing : total={t:.2f}s  llm={llm:.2f}s  retrieval={ret:.2f}s")

            if answer and "could not find" not in answer.lower() and "unable" not in answer.lower():
                passed += 1
            else:
                not_found += 1

        except Exception as e:
            print(f"     ERROR  : {e}")
            errors += 1

        time.sleep(1)

    print("\n" + "=" * 65)
    print(f"  RESULTS  answered={passed}  not_found={not_found}  errors={errors}")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()