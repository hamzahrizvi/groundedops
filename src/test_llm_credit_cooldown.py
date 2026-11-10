"""A refused-for-credit reply starts the provider cooldown. Plain asserts."""
import llm
import credit_watch

credit_watch.report_exhausted = lambda p: None


class Res:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def cooled(provider, code, text=""):
    llm._provider_down.clear()
    llm._note_credit_failure(provider, Res(code, text))
    return llm.provider_cooling(provider)


def run_tests():
    assert cooled("openai", 429, '{"error":{"code":"insufficient_quota"}}')
    assert cooled("deepseek", 402)
    assert not cooled("openai", 429, "rate limit reached")
    assert cooled("anthropic", 400, "Your credit balance is too low")

    llm._provider_down.clear()
    llm._note_credit_failure("openai", Res(429, "insufficient_quota"))
    called = []
    llm._chain_for = lambda role: [("openai", "m"), ("deepseek", "m")]
    llm.generate = lambda provider, *a, **k: (called.append(provider) or {"text": "x"})
    llm.generate_with_fallback("accurate", "q")
    assert called == ["deepseek"], called
    llm._provider_down.clear()
    print("ok: llm credit cooldown")


if __name__ == "__main__":
    run_tests()
