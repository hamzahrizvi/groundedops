"""Widget text, borders and buttons must pass WCAG contrast in the default look.

Static: reads the widget JS, widget_config.py and admin.html as text.
"""
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent
JS = (HERE / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
CFG = (HERE / "widget_config.py").read_text(encoding="utf-8")
ADMIN = (HERE / "admin.html").read_text(encoding="utf-8")


def _lum(h):
    def ch(i):
        v = int(h[1 + 2 * i:3 + 2 * i], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(0) + 0.7152 * ch(1) + 0.0722 * ch(2)


def ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def over(top, alpha, below):
    t = [int(top[1 + 2 * i:3 + 2 * i], 16) for i in range(3)]
    b = [int(below[1 + 2 * i:3 + 2 * i], 16) for i in range(3)]
    return "#" + "".join("%02x" % round(x * alpha + y * (1 - alpha)) for x, y in zip(t, b))


def var(name):
    return re.search("--" + name + r":(#[0-9a-fA-F]{6})", JS).group(1)


def rule(sel):
    i = JS.index(sel + "{")
    return JS[i:JS.index("}", i)]


def test_grey_text_passes_on_every_surface():
    for x in ("#ffffff", var("pane")):
        assert ratio(var("mut"), x) >= 4.5, x


def test_scope_bar_uses_the_pane_colour():
    assert "background:var(--pane)" in rule(".go-scope")


def test_wait_timer_has_no_opacity():
    assert "opacity" not in rule(".go-elapsed")


def test_field_borders_reach_3_to_1():
    assert ratio(var("fld"), "#ffffff") >= 3
    assert "var(--fld)" in rule(".go-in")
    assert "var(--fld)" in rule(".go-fin")


def test_script_default_accent_passes_everywhere():
    acc = "#E4002B"
    assert ratio("#ffffff", acc) >= 4.5
    assert ratio(acc, var("pane")) >= 4.5
    assert ratio("#ffffff", over("#000000", .15, acc)) >= 4.5
    assert "rgba(0,0,0,.15)" in rule(".go-av")


def test_server_default_accent_passes():
    acc = re.search(r'"color": "(#[0-9a-fA-F]{6})"', CFG).group(1)
    assert ratio("#ffffff", acc) >= 4.5
    assert ratio(acc, var("pane")) >= 4.5
    assert ratio(acc, "#121212") >= 3


def test_launcher_has_a_light_ring():
    assert "0 0 0 2px #fff" in rule(".go-launch")


def test_console_swatches_pass_and_warning_is_wired():
    m = re.search(r'\[(?:"#[0-9a-fA-F]{6}",\s*)+"#[0-9a-fA-F]{6}"\]\.forEach', ADMIN)
    assert m
    for h in re.findall(r"#[0-9a-fA-F]{6}", m.group(0)):
        assert ratio("#ffffff", h) >= 4.5, h
    line = next(l for l in ADMIN.splitlines() if "const setColor" in l)
    assert "drawColorWarn()" in line
    assert "swatchRow, colorWarn" in ADMIN


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
