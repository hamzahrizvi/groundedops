from router import route_model, requires_multi_hop


# ── Each test_queries.py query mapped to its expected role ─────────────────

def test_extract_checklist():
    role, _ = route_model("give me the checklist before leaving site after installation")
    assert role == "extract"


def test_fast_credentials():
    role, _ = route_model("default login credentials for myconnect")
    assert role == "fast"


def test_fast_what_is():
    role, _ = route_model("what is mycheckr")
    assert role == "fast"


def test_fast_hub_ip():
    role, _ = route_model("what is the hub ip")
    assert role == "fast"


def test_reasoning_how_does():
    role, _ = route_model("how does myconnect work with mycheckr")
    assert role == "reasoning"


def test_extract_how_to():
    role, _ = route_model("how to connect tablet to hub")
    assert role == "extract"


def test_reasoning_why():
    role, _ = route_model("why is multicast required for hub discovery")
    assert role == "reasoning"


def test_reasoning_explain_why():
    role, _ = route_model("explain why device registration might fail")
    assert role == "reasoning"


def test_fast_out_of_domain():
    role, _ = route_model("what is the capital of france")
    assert role == "fast"


def test_extract_sign_off():
    role, _ = route_model("post installation verification installer sign off")
    assert role == "extract"


def test_fast_introduction():
    role, _ = route_model("introduction of myconnect system")
    assert role == "fast"


def test_extract_install_and_verify_steps():
    """
    Regression test: "give me steps to install and verify system is
    working" previously matched the "and verify" multi-hop indicator and
    routed to "reasoning" — inconsistent with the near-identical
    "how to connect tablet to hub" which routes to "extract". Both are
    procedural checklist requests and should route the same way.
    """
    role, _ = route_model("give me steps to install and verify system is working")
    assert role == "extract"


# ── requires_multi_hop ──────────────────────────────────────────────────────

def test_multi_hop_relationship():
    assert requires_multi_hop("what is the relationship between hub and tablet") is True


def test_multi_hop_does_not_trigger_on_and_verify():
    assert requires_multi_hop("install and verify the system") is False


def test_multi_hop_works_with():
    assert requires_multi_hop("how myconnect works with mycheckr") is True