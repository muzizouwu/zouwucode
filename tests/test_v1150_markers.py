"""Trajectory rail (v1.15.0): hover previews, click to jump.

The dots used to expose only "你 / ZOUWUCODE" through the native title
attribute, so hovering told you nothing. Now hovering (or focusing) a dot
renders a styled preview with the message text; clicking still jumps.
"""

import re

from zouwucode.webui import server as web_server

INDEX = web_server.read_static("index.html")[0]
CSS = web_server.read_static("style.css")[0]
JS = web_server.read_static("app.js")[0]


def _ids(markup: str) -> set:
    return set(re.findall(r'id="([^"]+)"', markup))


class TestMarkerTipDom:
    def test_tooltip_node_exists(self):
        ids = _ids(INDEX)
        assert "marker-tip" in ids
        assert "markers" in ids

    def test_rail_is_not_hidden_from_assistive_tech(self):
        """It used to be aria-hidden, which hid every clickable dot."""
        markers_tag = re.search(r'<div id="markers"[^>]*>', INDEX).group(0)
        assert "aria-hidden" not in markers_tag
        assert 'role="navigation"' in markers_tag
        assert "aria-label=" in markers_tag

    def test_tooltip_is_announced(self):
        assert re.search(r'id="marker-tip"[^>]*role="tooltip"', INDEX)


class TestMarkerTipBehaviour:
    def test_hover_and_focus_show_the_preview(self):
        assert "mouseenter" in JS and "focus" in JS
        assert "showMarkerTip" in JS and "hideMarkerTip" in JS
        assert "mouseleave" in JS and "blur" in JS

    def test_preview_uses_message_text(self):
        assert "function markerPreview" in JS
        assert "innerText" in JS, "预览应读取消息正文"
        assert re.search(r"slice\(0,\s*179\)", JS), "预览应有长度上限"
        assert "（空消息）" in JS

    def test_no_native_title_tooltip(self):
        assert "dot.title" not in JS, "原生 title 提示会与自定义浮层重复"

    def test_preview_is_positioned_and_clamped(self):
        assert "getBoundingClientRect" in JS
        assert "window.innerHeight" in JS and "Math.max(12" in JS, "浮层不得溢出视口"

    def test_click_still_jumps(self):
        assert "scrollIntoView" in JS
        assert re.search(r"dot\.addEventListener\(\"click\"", JS)

    def test_cleared_and_repositioned_safely(self):
        assert "hideMarkerTip()" in JS
        assert re.search(r'chat\.addEventListener\("scroll"', JS)
        assert re.search(r'window\.addEventListener\("resize"', JS)
        assert "clearMarkers" in JS and "markers.innerHTML" in JS


class TestMarkerTipStyle:
    def test_styles_exist(self):
        assert ".marker-tip" in CSS
        assert ".marker-tip[hidden]" in CSS
        assert "pointer-events: none" in CSS
        assert "position: fixed" in CSS

    def test_hover_and_focus_are_visible(self):
        assert ".marker:hover" in CSS and ":focus-visible" in CSS
        assert "outline" in CSS
