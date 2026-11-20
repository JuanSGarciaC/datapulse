"""La landing estática (``site/``) no debe romperse ni desfasarse del repo.

Comprueba que sus enlaces locales existen, que las cifras coinciden con el README y que cada
fila del recorrido tiene fotogramas en ``main.js``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []
        self.counts: list[str] = []
        self.rows: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        for key in ("src", "href"):
            value = a.get(key)
            if value:
                self.refs.append(value)
        if a.get("data-count"):
            self.counts.append(a["data-count"] or "")
        if a.get("data-row"):
            self.rows.append(a["data-row"] or "")


def _parse() -> _Collector:
    c = _Collector()
    c.feed((SITE / "index.html").read_text(encoding="utf-8"))
    return c


def test_local_links_exist() -> None:
    local = [r for r in _parse().refs if not re.match(r"^(https?:|#|mailto:)", r)]
    assert local, "la página debería enlazar su CSS, su JS y las capturas"
    missing = [r for r in local if not (SITE / r).resolve().is_file()]
    assert missing == []


def test_figures_match_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## En números", 1)[1].split("\n## ", 1)[0]
    for n in _parse().counts:
        assert re.search(rf"\b{n}\b", section), f"la cifra {n} no aparece en «En números»"


def test_every_row_has_keyframes() -> None:
    js = (SITE / "main.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s{4}(\w+):\s*\[\[", js, flags=re.MULTILINE))
    assert set(_parse().rows) == keys
