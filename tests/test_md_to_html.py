import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "md_to_html.py"
SPEC = importlib.util.spec_from_file_location("md_to_html", SCRIPT_PATH)
assert SPEC is not None
md_to_html = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(md_to_html)
inline_theme_css_in_html = md_to_html.inline_theme_css_in_html


class InlineThemeCSSTests(unittest.TestCase):
    def test_replaces_local_stylesheet_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            css = Path(tmp) / "theme.css"
            css.write_text("body { color: red; }\n", encoding="utf-8")
            html = f'<html><head><link rel="stylesheet" href="{css.resolve().as_uri()}" /></head><body></body></html>'

            result = inline_theme_css_in_html(html, css)

            self.assertIn("<style>\nbody { color: red; }\n\n</style>", result)
            self.assertNotIn("<link", result)
            self.assertNotIn(str(css), result)

    def test_keeps_remote_stylesheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            css = Path(tmp) / "theme.css"
            css.write_text("body { color: red; }\n", encoding="utf-8")
            html = (
                '<html><head><link rel="stylesheet" href="https://example.com/font.css" />'
                f'<link rel="stylesheet" href="{css.as_posix()}" /></head><body></body></html>'
            )

            result = inline_theme_css_in_html(html, css)

            self.assertIn('href="https://example.com/font.css"', result)
            self.assertNotIn(css.as_posix(), result)
            self.assertIn("body { color: red; }", result)


if __name__ == "__main__":
    unittest.main()
