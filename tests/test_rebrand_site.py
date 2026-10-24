"""scripts/rebrand_site.py rewrites a copied site and refuses one that still names the brand."""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rebrand_site", ROOT / "scripts" / "rebrand_site.py")
rebrand_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebrand_site)

MAP = {
    "replace": [["The Acme App", "The Demo Vendor App"], ["Acme", "Demo Vendor"], ["ACME_CODE", "OPX"]],
    "files": {"logo.svg": "<svg/>"},
    "forbid": ["acme"],
}


def site(tmp_path: pathlib.Path, js: str) -> pathlib.Path:
    src = tmp_path / "src"
    (src / "assets").mkdir(parents=True)
    (src / "index.html").write_text("<title>The Acme App</title>")
    (src / "assets" / "app.js").write_text(js)
    (src / "logo.svg").write_text("<svg><text>Acme</text></svg>")
    return src


def test_rewrites_text_files_and_overwrites_named_files(tmp_path):
    src = site(tmp_path, 'const c="ACME_CODE";alert("Welcome to Acme")')
    dst = tmp_path / "dst"

    left = rebrand_site.rebrand(src, dst, MAP)

    assert left == []
    assert (dst / "index.html").read_text() == "<title>The Demo Vendor App</title>"
    assert (dst / "assets" / "app.js").read_text() == 'const c="OPX";alert("Welcome to Demo Vendor")'
    assert (dst / "logo.svg").read_text() == "<svg/>"
    assert "Acme" in (src / "index.html").read_text(), "the source copy is left as it was"


def test_reports_a_forbidden_word_the_map_missed(tmp_path):
    src = site(tmp_path, 'const u="https://acme.example/terms"')

    left = rebrand_site.rebrand(src, tmp_path / "dst", MAP)

    assert left == ["assets/app.js: 'acme' x1"]
