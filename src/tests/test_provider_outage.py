"""A provider outage must not masquerade as a gap in the documentation,
and must not silently bill a provider nobody assigned.

All three behaviours were observed failing on 2026-09-16 against an on-prem
gateway whose hostname had stopped resolving: every question came back as
"I don't have that in the product documentation", /health?deep=1 reported
ready, and each turn escalated onto a personal DeepSeek key.
"""
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main


def test_unreachable_provider_is_reported_as_unreachable():
    """A hostname that does not resolve is a failure, not a green check."""
    ok, why = main._provider_reachable("openai", timeout=2.0)
    # The harness has no real gateway, so this must be honest about it rather
    # than defaulting to True.
    assert ok is False
    assert why, "an unreachable provider must say why"


def test_no_provider_configured_is_not_reachable():
    ok, why = main._provider_reachable(None)
    assert ok is False
    assert "no provider" in why.lower()


def test_health_ready_requires_reachability_not_just_a_key():
    """The old check OR'd in every provider's env var, so any key anywhere
    made `provider_key` true. Readiness must follow the CONFIGURED provider
    and whether it answers."""
    body = main.health(deep=1)
    payload = getattr(body, "body", None)
    import json
    data = json.loads(payload) if payload else body
    assert "provider_reachable" in data, "deep health must report reachability"
    # Nothing is reachable from the test harness, so ready must be False.
    assert data["ready"] is False
    assert data["status"] == "not-ready"


def test_escalation_does_not_fire_without_an_assigned_backup():
    """The escalation used to hardcode generate("deepseek", ...), so it
    billed a key the operator never chose -- hardest exactly when the
    configured provider was down and every question failed."""
    import inspect
    # Comments are stripped first: this function's own comment explains what
    # it replaced, and matching that text would fail on the explanation
    # rather than on the code.
    src = "\n".join(l for l in inspect.getsource(main.query).splitlines()
                    if not l.lstrip().startswith("#"))
    assert 'generate("deepseek"' not in src, \
        "escalation must not hardcode a provider; it follows the backup role"
    assert "_backup_provider" in src and 'keystore.get_role("backup")' in src, \
        "escalation must read the assigned backup role"
    # And it must be gated on that role being set, so an unassigned backup
    # means no automatic spend at all.
    assert "and _backup_provider)" in src, \
        "escalation must not run when no backup provider is assigned"


def test_timing_separates_verification_from_regeneration():
    """grounding_time used to span the escalation and the retry loop -- whole
    extra generations -- so it reported the model's cost as the verifier's."""
    import inspect
    src = inspect.getsource(main.query)
    assert '_spent["verify"]' in src and '_spent["regen"]' in src
    assert 'grounding_time = _spent["verify"]' in src, \
        "grounding_time must be verification only"
    assert "escalation_time" in src and "verify_stage_time" in src, \
        "regeneration and the full span must be reported separately"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
