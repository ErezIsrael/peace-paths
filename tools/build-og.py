#!/usr/bin/env python3
"""
Render app/og.png — the social-preview card (1200x630) shown when the site is
shared on WhatsApp / Telegram / X / Facebook.

Usage:  python tools/build-og.py
Needs:  playwright (python -m playwright install chromium)

The template mirrors the site's dark palette and uses the same Hebrew subset
the site ships, so the preview looks like the product.
"""
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "app", "og.png")

HTML = """<!doctype html>
<html lang="he" dir="rtl">
<meta charset="utf-8">
<style>
  @font-face {
    font-family: 'Heebo'; font-style: normal; font-weight: 300 800;
    src: url('../app/fonts/heebo-hebrew.woff2') format('woff2');
  }
  @font-face {
    font-family: 'Heebo'; font-style: normal; font-weight: 300 800;
    src: url('../app/fonts/heebo-latin.woff2') format('woff2');
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1200px; height: 630px; background: #0a1628; }
  body {
    font-family: 'Heebo', system-ui, sans-serif;
    background: #0a1628;
    color: #e0e8f0; padding: 64px 72px; display: flex; flex-direction: column;
    justify-content: space-between; overflow: hidden;
  }
  /* Flat shapes instead of a gradient: a gradient costs 200 KB of PNG for a
     background nobody notices, flat colour compresses to a fraction of that. */
  .glow { position: absolute; border-radius: 50%; background: #16335c; }
  .glow.a { width: 900px; height: 900px; left: -260px; top: -420px; opacity: .55; }
  .glow.b { width: 520px; height: 520px; right: -180px; bottom: -260px; opacity: .35; background: #123157; }
  .brand { display: flex; align-items: center; gap: 18px; }
  .dove { font-size: 62px; line-height: 1; }
  .names h1 { font-size: 58px; font-weight: 800; letter-spacing: -0.5px; }
  .names .he { font-size: 34px; font-weight: 600; color: #4fc3f7; margin-top: 2px; }
  .lead { font-size: 40px; font-weight: 600; line-height: 1.35; max-width: 980px; }
  .lead b { color: #81d4fa; }
  .rail { display: flex; align-items: center; gap: 0; margin: 6px 0 2px; }
  .node { width: 20px; height: 20px; border-radius: 50%; flex: 0 0 auto; }
  .done { background: #4caf50; } .act { background: #4fc3f7; box-shadow: 0 0 0 6px rgba(79,195,247,.22); }
  .fut { background: #1e3a5f; }
  .seg { height: 4px; flex: 1 1 auto; background: #1e3a5f; }
  .seg.done { background: #4caf50; }
  .foot { display: flex; justify-content: space-between; align-items: center;
          font-size: 22px; color: #8ea8c8; font-weight: 400; }
  .foot .src { font-size: 19px; }
</style>
<body>
  <div class="glow a"></div>
  <div class="glow b"></div>
  <div>
    <div class="brand">
      <div class="dove">🕊</div>
      <div class="names"><h1>Peace Paths</h1><div class="he">נתיבי השלום</div></div>
    </div>
  </div>
  <div>
    <div class="lead">מעקב אחרי <b>יוזמות שלום קונקרטיות</b> במזרח התיכון —<br>
      מה התקדם השבוע, באיזה שלב נמצא כל מהלך, ומה הצעד הבא.</div>
    <div class="rail">
      <div class="node done"></div><div class="seg done"></div>
      <div class="node done"></div><div class="seg done"></div>
      <div class="node act"></div><div class="seg"></div>
      <div class="node fut"></div><div class="seg"></div>
      <div class="node fut"></div>
    </div>
  </div>
  <div class="foot">
    <div>72 מקורות RSS · סיווג וניתוח בעזרת AI</div>
    <div class="src" dir="ltr">peace-paths.pages.dev</div>
  </div>
</body>
</html>
"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is required: pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 1
    tmp = os.path.join(HERE, "_og_template.html")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(HTML)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(Path(tmp).as_uri(), wait_until="load")
            page.wait_for_timeout(600)   # let the webfont settle
            page.screenshot(path=OUT, type="png")
            browser.close()
    finally:
        os.remove(tmp)
    print(f"  og.png {os.path.getsize(OUT) / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
