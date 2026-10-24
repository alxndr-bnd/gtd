"""GTD-7: Google Fonts blocked by a visitor's extension or widget is not our code."""

import app


def _report(blocked, directive="font-src"):
    return {"blocked-uri": blocked, "effective-directive": directive}


def test_gstatic_font_is_noise():
    assert app.csp_noise(_report("https://fonts.gstatic.com/s/opensans.woff2"), "Chrome")


def test_googleapis_font_is_noise():
    assert app.csp_noise(_report("https://fonts.googleapis.com/css2"), "Chrome")


def test_gstatic_script_is_still_reported():
    assert not app.csp_noise(_report("https://fonts.gstatic.com/x.js", "script-src"), "Chrome")


def test_unknown_font_host_is_still_reported():
    assert not app.csp_noise(_report("https://evil.example/x.woff2"), "Chrome")
