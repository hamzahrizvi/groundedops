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


def test_deep_health_reports_every_role_and_no_hostname():
    """9.2/9.3: /health is public, so the reason must not name the internal
    gateway; and every role is reported so a dead backup shows up early."""
    import json
    from unittest.mock import patch
    import requests
    err = requests.ConnectionError(
        "HTTPSConnectionPool(host='gw.internal.example', port=443): "
        "NameResolutionError")
    with patch.object(requests, "get", side_effect=err), \
         patch.object(main.keystore, "get_key", return_value="k"), \
         patch.object(main.keystore, "has_key", return_value=True), \
         patch.object(main.keystore, "get_role",
                      side_effect=lambda r: None if r == "backup" else "openai"):
        data = json.loads(main.health(deep=1).body)
    assert set(data["roles"]) == {"default", "advanced", "backup"}
    assert data["roles"]["default"]["provider"] == data["provider"] == "openai"
    assert data["roles"]["backup"] == {"provider": None, "reachable": None}
    assert "gw.internal" not in json.dumps(data)
    assert "does not resolve" in data["provider_unreachable_reason"]
    assert data["ready"] is False


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
    assert ("_backup_provider" in src and "keystore.get_role(_esc_role)" in src
            and "escalation_role()" in src), \
        "escalation must read the role escalation_role picks for the mode"
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


def test_a_forced_model_that_returns_nothing_is_not_a_documentation_gap():
    """Observed 2026-09-24. The console's Pipeline check was set to
    gpt-4o-mini, which the on-prem gateway rejects with 400 Bad Request.
    Every question came back "I don't have that in the product
    documentation about the MyCheckr" -- under Sources listing the four
    manuals that do cover it -- while the same question through the widget
    answered correctly on deepseek.

    A forced model never reaches the fallback chain, so `provider` is the
    one that was chosen rather than the "none" sentinel, and the honest
    wording was skipped on exactly the path an operator uses to diagnose."""
    from unittest.mock import patch
    chunk = {"id": "u1", "text": "Connect the device with a USB-B cable.",
             "source": "MyCheckr User Manual-v7.pdf", "page": 9,
             "product": "mycheckr", "category": "", "rerank_score": 0.97}
    main.APP_STATE["ready"] = True

    with patch.object(main, "retrieve_from_db", return_value=[chunk]),          patch.object(main, "rerank", return_value=[chunk]),          patch.object(main.faq_store, "suggest_candidates",
                      return_value={"mode": "none"}),          patch.object(main, "generate", return_value=None),          patch.object(main, "_structures_for", return_value=[]),          patch.object(main.more_context, "build", return_value={"kind": "support"}),          patch.object(main.faq_store, "record_gap") as gap,          patch.object(main, "log_interaction"):
        r = main.query(main.QueryRequest(
            q="I can't see my MyCheckr on IMS local?",
            force_provider="openai", force_model="gpt-4o-mini"))

    low = r["answer"].lower()
    assert "product documentation" not in low,         "a model that returned nothing must not be reported as a corpus gap"
    assert "gpt-4o-mini" in r["answer"], "say WHICH model failed"
    assert r["service_degraded"] is True,         "a failed generation is degraded service, not a missing answer"
    gap.assert_not_called()  # nobody failed to answer it; the model never ran


def _escalation_stack(stack, roles):
    """Patch stack for a flagged local answer; returns the `generate` mock."""
    from unittest.mock import patch
    chunk = {"id": "u1", "text": "Connect the device with a USB-B cable.",
             "source": "MyCheckr User Manual-v7.pdf", "page": 9,
             "product": "mycheckr", "category": "", "rerank_score": 0.97}
    main.APP_STATE["ready"] = True
    po = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
    po(main, "retrieve_from_db", return_value=[chunk])
    po(main, "rerank", return_value=[chunk])
    po(main.faq_store, "suggest_candidates", return_value={"mode": "none"})
    po(main, "_structures_for", return_value=[])
    po(main.more_context, "build", return_value={"kind": "support"})
    po(main.faq_store, "record_gap")
    po(main, "log_interaction")
    po(main, "generate_with_fallback", return_value={
        "text": "Use a USB-B cable.", "provider": "local", "model": "mistral"})
    po(main, "check_grounding", return_value=(False, 0.1))
    po(main, "_lexically_supported", return_value=False)
    po(main, "_llm_verified", return_value=False)
    po(main, "grounding_retries", return_value=0)
    gen = po(main, "generate", return_value=None)
    po(main.keystore, "get_role", side_effect=lambda r: roles.get(r))
    return gen


def test_local_mode_escalates_to_the_default_role_and_api_mode_to_backup():
    import os
    from contextlib import ExitStack
    from unittest.mock import patch
    with ExitStack() as st:
        gen = _escalation_stack(st, {"default": "openai", "backup": "deepseek"})
        st.enter_context(patch.dict(os.environ, {
            "MODEL_ROLE_DEFAULT": "gpt-test", "MODEL_ROLE_BACKUP": "ds-test"}))
        for role, provider, model in (("default", "openai", "gpt-test"),
                                      ("backup", "deepseek", "ds-test")):
            with patch.object(main, "escalation_role", return_value=role):
                gen.reset_mock()
                main.query(main.QueryRequest(q="How do I connect the MyCheckr?"))
                called = [(c.args[0], c.args[2]) for c in gen.call_args_list]
                assert (provider, model) in called, (role, called)
                assert all(p == provider for p, _ in called), (role, called)


def test_no_escalation_when_the_mode_role_is_unassigned():
    from contextlib import ExitStack
    from unittest.mock import patch
    with ExitStack() as st:
        gen = _escalation_stack(st, {"backup": "deepseek"})
        with patch.object(main, "escalation_role", return_value="default"):
            main.query(main.QueryRequest(q="How do I connect the MyCheckr?"))
        assert gen.called is False


def test_local_mode_with_the_switch_off_refuses_and_sends_nothing_out():
    """D-20261016-13: the harness stubs llm, so the real escalation_role is
    covered in test_llm; here: when it returns None (switch off) a flagged
    answer calls no provider."""
    from contextlib import ExitStack
    from unittest.mock import patch
    with ExitStack() as st:
        gen = _escalation_stack(st, {"default": "openai", "backup": "deepseek"})
        with patch.object(main, "escalation_role", return_value=None):
            r = main.query(main.QueryRequest(q="How do I connect the MyCheckr?"))
        assert gen.called is False
        assert "USB-B" not in r["answer"]


def test_deep_health_is_private_but_plain_health_is_public():
    """D-20261010-24: from outside, ?deep=1 is a 404 and spends no provider
    call; from the box, the LAN, the docker bridge or with a staff-tier bearer token (a website member token is a 404, D-20261010-33)
    it answers in full, PUBLIC_ONLY included. Plain /health stays public."""
    from unittest.mock import patch
    import requests
    import quota
    from fastapi.testclient import TestClient
    calls = []

    def fake_get(*a, **k):
        calls.append(a)
        raise requests.ConnectionError("down")

    ext = {"cf-connecting-ip": "203.0.113.9"}
    full = lambda r: (r.status_code in (200, 503)
                      and "provider_reachable" in r.json() and "roles" in r.json())
    with patch.object(requests, "get", side_effect=fake_get), \
         patch.object(main.keystore, "get_key", return_value="k"), \
         patch.object(main.keystore, "has_key", return_value=True), \
         patch.object(main.keystore, "get_role",
                      side_effect=lambda r: None if r == "backup" else "openai"):
        c = TestClient(main.app)                                  # socket "testclient" (private in the harness)
        for url in ("/health?deep=1", "/api/health?deep=1", "/health?deep=0"):
            assert c.get(url, headers=ext).status_code == 404, url
        assert c.get("/health?deep=1",
                     headers={**ext, "authorization": "Bearer bad.token"}).status_code == 404
        member = quota.issue_token("m1", "member")                 # a website customer's token
        assert c.get("/health?deep=1",
                     headers={**ext, "authorization": "Bearer " + member}).status_code == 404
        pub = TestClient(main.app, client=("198.51.100.4", 50000))  # no proxy, public socket
        assert pub.get("/health?deep=1").status_code == 404
        r = c.get("/health", headers=ext)
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert calls == [], "an outside caller must not reach a provider"

        for host in ("127.0.0.1", "172.17.0.1"):                   # the box; docker's bridge
            r = TestClient(main.app, client=(host, 50000)).get("/health?deep=1")
            assert full(r), (host, r.status_code)
        assert calls, "the local deep probe must still contact the provider"

        tok = quota.issue_token("uptime-monitor", "staff")
        r = c.get("/health?deep=1", headers={**ext, "authorization": "Bearer " + tok})
        assert full(r), r.status_code
        from types import SimpleNamespace
        req = lambda t: SimpleNamespace(headers={"authorization": "Bearer " + t})
        assert main._bearer_ok(req(member)) and main._bearer_ok(req(tok))
        assert main._bearer_ok(req(tok), min_tier="staff")
        assert not main._bearer_ok(req(member), min_tier="staff")

        with patch.object(main, "PUBLIC_ONLY", True):
            r = TestClient(main.app, client=("127.0.0.1", 50000)).get("/health?deep=1")
            assert full(r), r.status_code
            assert c.get("/health?deep=1", headers=ext).status_code == 404
            assert c.get("/health", headers=ext).status_code == 200


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
