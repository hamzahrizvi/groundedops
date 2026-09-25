"""The manual's running page footer is not content.

64 of 1749 chunks (3.7%) had a page footer as their ENTIRE body. They are
not inert: "[NV200S Range User Manual-v1 - WR00147 - SMART Payout to NV200
Adapter] NV200S Range User Manual - 107" reranked #1 at 0.9974 for "can the
NV200 be used with a SMART Payout?" -- it won on the heading and then
carried no answer.

The stripper has to be conservative: a short chunk is not automatically
noise. 113 chunks (6.5%) are short AND informative ("MyCheckr: 586 g /
MyCheckr Mini: 152 g", "The API is available via HTTPS only.") and must
survive untouched.
"""
import ingest


def test_strips_the_real_footers_seen_in_the_corpus():
    cases = [
        ("NV200S Range User Manual-v1.pdf", "NV200S Range User Manual – 107"),
        ("MyCheckr Mini User Manual-v5.pdf", "MyCheckr Mini User Manual – 5"),
        ("MyConnect Environment-v4.pdf", "MyConnect Environment – 13"),
        ("NV9 Spectral Range User Manual-v1.pdf", "NV9 Spectral Range User Manual – 49"),
        ("BV30 User Manual-v1.pdf", "BV30 User Manual - 24"),
    ]
    for filename, footer in cases:
        page = "Real content about the device.\n" + footer
        out = ingest._strip_running_footer(page, filename)
        assert footer not in out, f"footer survived for {filename}: {out!r}"
        assert "Real content about the device." in out


def test_a_footer_only_page_becomes_empty():
    """This is the class that produced breadcrumb-only chunks."""
    out = ingest._strip_running_footer(
        "NV200S Range User Manual – 107", "NV200S Range User Manual-v1.pdf")
    assert out.strip() == ""


def test_does_not_eat_content_that_merely_ends_in_a_number():
    """A spec line ending in a number is not a footer -- its words are
    nothing like the document title."""
    keep = [
        ("Supply Voltage - 24", "NV200S Range User Manual-v1.pdf"),
        ("Maximum note length - 167", "NV9 Spectral Range User Manual-v1.pdf"),
        ("MyCheckr Mini: 152 g", "MyCheckr Mini User Manual-v5.pdf"),
        ("Press and hold for more than 3", "BV30 User Manual-v1.pdf"),
    ]
    for line, filename in keep:
        out = ingest._strip_running_footer(line, filename)
        assert out.strip() == line, f"content was eaten: {line!r} -> {out!r}"


def test_short_but_informative_content_survives():
    page = ("The API is available via HTTPS only.\n"
            "ICU_Network_API-v1.0.50 – 7")
    out = ingest._strip_running_footer(page, "ICU_Network_API-v1.0.50.pdf")
    assert "The API is available via HTTPS only." in out
    assert "– 7" not in out


def test_unknown_filename_is_left_alone():
    """No title to compare against means no confident footer detection."""
    page = "Something - 12"
    assert ingest._strip_running_footer(page, ".pdf").strip() == "Something - 12"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
