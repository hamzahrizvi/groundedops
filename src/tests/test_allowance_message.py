"""The per-day quota stop must point at a person (grep-style, runs without playwright)."""
import pathlib

src = (pathlib.Path(__file__).resolve().parent.parent / "widget_api.py").read_text(encoding="utf-8")
assert "It resets in 24 hours. \"\n                            \"For help with your question, contact our team.\"" in src
print("ok")
