#!/usr/bin/env python3
"""Check WCAG 2.2 contrast for Minnal's design tokens.

Usage: python contrast_check.py path/to/globals.css

Reads hex colour custom properties from `:root` (dark) and `.light` blocks and
checks the text/background and UI pairs that Minnal relies on. Exits 1 if any
pair fails, so it can run in CI or a Kiro hook. Standard library only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# (foreground token, background token, minimum ratio, why)
PAIRS: list[tuple[str, str, float, str]] = [
    ("foreground", "background", 4.5, "body text"),
    ("foreground", "surface-2", 4.5, "text on cards"),
    ("muted-foreground", "surface-1", 4.5, "secondary text"),
    ("muted-foreground", "surface-2", 4.5, "secondary text on cards"),
    ("brand-foreground", "brand", 4.5, "primary button label"),
    ("status-critical", "surface-2", 3.0, "critical icon/label on card"),
    ("status-warning", "surface-2", 3.0, "warning icon/label on card"),
    ("status-ok", "surface-2", 3.0, "ok icon/label on card"),
    ("status-info", "surface-2", 3.0, "info icon/label on card"),
    ("status-neutral", "surface-2", 3.0, "neutral icon/label on card"),
    ("ring", "background", 3.0, "focus ring"),
    ("border", "background", 1.3, "divider (decorative, informational only)"),
]


def _channel(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def parse_block(css: str, selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    if not match:
        return {}
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", match.group(1)))


def main(path: str) -> int:
    css = Path(path).read_text(encoding="utf-8")
    dark = parse_block(css, ":root")
    light = {**dark, **parse_block(css, ".light")}
    failures = 0
    for theme, tokens in (("dark", dark), ("light", light)):
        print(f"\n{theme} theme")
        for fg, bg, minimum, why in PAIRS:
            if fg not in tokens or bg not in tokens:
                print(f"  ?  {fg} on {bg}: token missing")
                failures += 1
                continue
            r = ratio(tokens[fg], tokens[bg])
            ok = r >= minimum
            failures += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'}  {fg:<17} on {bg:<11} {r:5.2f}:1 (min {minimum}) {why}")
    print(f"\n{'all pairs pass' if not failures else f'{failures} pair(s) fail'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
