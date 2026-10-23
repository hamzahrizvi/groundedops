"""The per-day quota stop must point at a person (grep-style, runs without playwright)."""
import pathlib


def test_the_daily_allowance_message_points_at_our_team():
    src = (pathlib.Path(__file__).resolve().parent.parent / "widget_api.py").read_text(encoding="utf-8")
    assert "For help with your question, contact our team." in src


if __name__ == "__main__":
    test_the_daily_allowance_message_points_at_our_team()
    print("ok")
