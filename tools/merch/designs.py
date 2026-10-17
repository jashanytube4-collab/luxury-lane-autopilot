"""Store designs for The Luxury Lane (Spreadshop). Original vector artwork + Google Fonts (OFL, free for commercial use),
rendered by Chrome to transparent 4000x4000 PNG print files, in a gold version (dark garments) and a black version
(light garments, mugs, stickers).

    python -m tools.merch.designs          # -> D:/Jashan/Desktop/Store Designs (Spreadshop)/
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"D:\Jashan\Desktop") / "Store Designs (Spreadshop)"
CHROME = next((p for p in (Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                           Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")) if p.exists()), None)
FONTS = ("https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700;900&family=Cinzel+Decorative:wght@700"
         "&family=Aref+Ruqaa:wght@700&family=Reem+Kufi:wght@600;700&family=Montserrat:wght@500;700;800"
         "&family=Playfair+Display:ital,wght@1,600&display=block")
GOLD = "#D4AF5A"
GOLD_DARK = "#B8913A"


# ---- artwork -------------------------------------------------------------------------------------------------
def falcon(cx: float, cy: float, s: float, fill: str) -> str:
    """A heraldic falcon, wings spread, head turned right: faceted feathers like a crest. s = wingspan / 1000."""
    def pt(x, y):
        return f"{cx + x * s:.1f},{cy + y * s:.1f}"
    parts = []
    # wings: 7 primary feathers fanning upward on each side, each a tapered blade
    for side in (-1, 1):
        for i in range(7):
            a = math.radians(-8 - i * 11.5)                 # 8° .. 77° above the horizon
            root = (side * (45 + i * 6), -30 - i * 9)
            length = 470 - i * 38
            tip = (root[0] + side * length * math.cos(a), root[1] + length * math.sin(a))
            w = 34 - i * 2
            nx, ny = -math.sin(a) * w, math.cos(a) * w
            base1 = (root[0] + side * nx * 0.2, root[1] + ny * 0.9)
            base2 = (root[0] - side * nx * 0.2, root[1] - ny * 0.9)
            mid1 = (root[0] + side * length * 0.72 * math.cos(a) + side * nx * 0.55,
                    root[1] + length * 0.72 * math.sin(a) + ny * 0.55)
            parts.append(f'<polygon points="{pt(*base1)} {pt(*mid1)} {pt(*tip)} {pt(*base2)}"/>')
        # covert band along the wing root
        parts.append(f'<polygon points="{pt(side*40,-10)} {pt(side*170,-95)} {pt(side*250,-60)} {pt(side*120,40)}"/>')
    # body
    parts.append(f'<polygon points="{pt(0,-120)} {pt(52,-60)} {pt(60,60)} {pt(30,170)} {pt(-30,170)} {pt(-60,60)} {pt(-52,-60)}"/>')
    # tail: three feathers
    for dx, tilt in ((-34, -14), (0, 0), (34, 14)):
        parts.append(f'<polygon points="{pt(dx-18,160)} {pt(dx+18,160)} {pt(dx+tilt+14,300)} {pt(dx+tilt,320)} {pt(dx+tilt-14,300)}"/>')
    # head with hooked beak, facing right
    parts.append(f'<polygon points="{pt(-34,-118)} {pt(-28,-178)} {pt(4,-214)} {pt(44,-208)} {pt(70,-184)} '
                 f'{pt(92,-176)} {pt(80,-156)} {pt(64,-160)} {pt(48,-140)} {pt(34,-112)}"/>')
    # eye (cut out)
    eye = f'<circle cx="{cx + 30 * s:.1f}" cy="{cy - 184 * s:.1f}" r="{9 * s:.1f}" fill="var(--cut)"/>'
    return f'<g fill="{fill}">{"".join(parts)}</g>{eye}'


def falcon_head(cx: float, cy: float, size: float, fill: str) -> str:
    """The emblem falcon head (profile, facing left), original artwork; drawn in a 1000 box centred on (500, 560)."""
    k = size / 1000
    art = """
<path fill="{fill}" d="
M 742 860
L 700 800 L 668 862 L 630 796 L 590 860 L 552 792 L 512 856 L 478 788
C 470 700, 440 640, 398 590
C 372 560, 352 540, 336 532
C 318 528, 300 532, 288 546
C 278 560, 272 580, 268 602
C 252 580, 236 548, 230 512
C 224 474, 238 440, 262 410
C 290 372, 316 336, 360 308
C 420 272, 500 262, 572 286
C 660 316, 724 390, 750 500
C 772 600, 770 720, 742 860 Z"/>
<!-- fierce brow -->
<path fill="var(--cut)" d="M 352 350 C 400 320, 470 318, 530 336 C 490 340, 440 352, 404 372 C 386 366, 368 360, 352 350 Z"/>
<!-- eye -->
<circle cx="452" cy="392" r="36" fill="var(--cut)"/>
<circle cx="452" cy="392" r="24" fill="{fill}"/>
<circle cx="452" cy="392" r="15" fill="var(--cut)"/>
<circle cx="458" cy="385" r="5" fill="{fill}"/>
<!-- malar stripe -->
<path fill="var(--cut)" d="M 436 438 C 446 486, 470 540, 512 600 C 462 566, 432 510, 420 446 Z"/>
<!-- cere and nostril -->
<path fill="none" stroke="var(--cut)" stroke-width="10" stroke-linecap="round" d="M 336 340 C 352 366, 358 392, 352 420"/>
<circle cx="318" cy="392" r="9" fill="var(--cut)"/>
<!-- gape: straight, slightly down-back (stern, not smiling) -->
<path fill="none" stroke="var(--cut)" stroke-width="10" stroke-linecap="round" d="M 262 470 L 396 470"/>
<!-- chest feathers -->
<g fill="none" stroke="var(--cut)" stroke-width="9" stroke-linecap="round">
<path d="M 560 650 l 28 34 l 28 -34"/><path d="M 630 650 l 28 34 l 28 -34"/><path d="M 595 720 l 28 34 l 28 -34"/><path d="M 665 720 l 28 34 l 28 -34"/>
</g>
""".replace("{fill}", fill)
    return f'<g transform="translate({cx - 500 * k:.1f},{cy - 560 * k:.1f}) scale({k:.4f})">{art}</g>'


def skyline(x0: float, base: float, w: float, fill: str) -> str:
    """Dubai skyline: Burj Khalifa, Burj Al Arab, Museum of the Future, Dubai Frame, Emirates Towers, Address."""
    u = w / 1000
    def X(v): return x0 + v * u
    def Y(v): return base - v * u
    sh = []
    # Dubai Frame (left)
    sh.append(f'<path d="M{X(20)},{Y(0)} V{Y(260)} H{X(150)} V{Y(0)} H{X(122)} V{Y(232)} H{X(48)} V{Y(0)} Z"/>')
    # Emirates Towers pair
    sh.append(f'<polygon points="{X(185)},{Y(0)} {X(185)},{Y(330)} {X(215)},{Y(390)} {X(235)},{Y(330)} {X(235)},{Y(0)}"/>')
    sh.append(f'<polygon points="{X(248)},{Y(0)} {X(248)},{Y(290)} {X(272)},{Y(335)} {X(288)},{Y(290)} {X(288)},{Y(0)}"/>')
    # Burj Khalifa (stepped, centre)
    steps = [(70, 260), (58, 380), (47, 470), (37, 560), (28, 640), (20, 710), (13, 770), (8, 820)]
    cxk = 470
    pts = [f"{X(cxk-80)},{Y(0)}"]
    for half, top in steps:
        pts += [f"{X(cxk-half-10)},{Y(top-110)}", f"{X(cxk-half)},{Y(top)}"]
    pts += [f"{X(cxk-3)},{Y(820)}", f"{X(cxk)},{Y(960)}", f"{X(cxk+3)},{Y(820)}"]
    for half, top in reversed(steps):
        pts += [f"{X(cxk+half)},{Y(top)}", f"{X(cxk+half+10)},{Y(top-110)}"]
    pts.append(f"{X(cxk+80)},{Y(0)}")
    sh.append(f'<polygon points="{" ".join(pts)}"/>')
    # Address / tall tower right of Khalifa
    sh.append(f'<polygon points="{X(575)},{Y(0)} {X(575)},{Y(360)} {X(600)},{Y(420)} {X(625)},{Y(360)} {X(625)},{Y(0)}"/>')
    # Museum of the Future: an elliptical ring on a short plinth
    sh.append(f'<path fill-rule="evenodd" d="M{X(645)},{Y(105)} a{78*u:.1f},{100*u:.1f} 0 1 0 {156*u:.1f},0 a{78*u:.1f},{100*u:.1f} 0 1 0 {-156*u:.1f},0 Z '
              f'M{X(683)},{Y(105)} a{40*u:.1f},{58*u:.1f} 0 1 0 {80*u:.1f},0 a{40*u:.1f},{58*u:.1f} 0 1 0 {-80*u:.1f},0 Z"/>')
    # Burj Al Arab (sail, right)
    sh.append(f'<path d="M{X(850)},{Y(0)} L{X(862)},{Y(400)} C{X(930)},{Y(330)} {X(975)},{Y(180)} {X(985)},{Y(0)} Z"/>')
    sh.append(f'<rect x="{X(856)}" y="{Y(440)}" width="{6*u:.1f}" height="{60*u:.1f}"/>')
    # ground line
    sh.append(f'<rect x="{X(0)}" y="{Y(0)}" width="{w:.1f}" height="{10*u:.1f}"/>')
    return f'<g fill="{fill}">{"".join(sh)}</g>'


def sun_dunes(cx: float, cy: float, r: float, fill: str) -> str:
    rays = "".join(
        f'<rect x="{cx - 3:.1f}" y="{cy - r * 1.55:.1f}" width="6" height="{r * 0.32:.1f}" '
        f'transform="rotate({a} {cx} {cy})"/>' for a in range(-80, 81, 16))
    dunes = (f'<path d="M{cx - r*2.2},{cy + r*0.55} C{cx - r*1.2},{cy - r*0.1} {cx - r*0.4},{cy + r*0.15} {cx + r*0.2},{cy + r*0.25} '
             f'C{cx + r*0.9},{cy - r*0.2} {cx + r*1.6},{cy + r*0.1} {cx + r*2.2},{cy + r*0.55} Z"/>')
    return (f'<g fill="{fill}">{rays}<circle cx="{cx}" cy="{cy}" r="{r}"/></g>'
            f'<g fill="var(--cut)">{dunes}</g>'
            f'<path fill="none" stroke="{fill}" stroke-width="10" d="M{cx - r*2.2},{cy + r*0.55} '
            f'C{cx - r*1.2},{cy - r*0.1} {cx - r*0.4},{cy + r*0.15} {cx + r*0.2},{cy + r*0.25} '
            f'C{cx + r*0.9},{cy - r*0.2} {cx + r*1.6},{cy + r*0.1} {cx + r*2.2},{cy + r*0.55}"/>')


def arc_text(text: str, cx: float, cy: float, r: float, size: int, font: str, fill: str, top: bool = True,
             spacing: int = 18, ident: str = "a") -> str:
    d = (f"M{cx - r},{cy} A{r},{r} 0 0 1 {cx + r},{cy}" if top else f"M{cx - r},{cy} A{r},{r} 0 0 0 {cx + r},{cy}")
    return (f'<path id="{ident}" d="{d}" fill="none"/><text font-family="{font}" font-size="{size}" fill="{fill}" '
            f'letter-spacing="{spacing}" text-anchor="middle"><textPath href="#{ident}" startOffset="50%">{text}</textPath></text>')


# ---- the designs (SVG 1000x1000) -----------------------------------------------------------------------------
def d_crest(c: str) -> str:
    return f"""
<circle cx="500" cy="500" r="455" fill="none" stroke="{c}" stroke-width="10"/>
<circle cx="500" cy="500" r="432" fill="none" stroke="{c}" stroke-width="3"/>
{arc_text("HAMDANIANS", 500, 500, 362, 92, "Cinzel", c, True, 22, "t1").replace('font-size', 'font-weight="900" font-size')}
{arc_text("✦  DUBAI  ·  EST. 2025  ✦", 500, 500, 392, 38, "Montserrat", c, False, 12, "t2").replace('font-size', 'font-weight="700" font-size')}
{falcon_head(500, 535, 560, c)}
"""


def d_calligraphy(c: str) -> str:
    return f"""
<text x="500" y="500" text-anchor="middle" font-family="Aref Ruqaa" font-weight="700" font-size="400" fill="{c}">فزاع</text>
<rect x="270" y="690" width="460" height="6" fill="{c}"/>
<text x="520" y="800" text-anchor="middle" font-family="Cinzel" font-weight="700" font-size="92" letter-spacing="40" fill="{c}">FAZZA</text>
<text x="500" y="862" text-anchor="middle" font-family="Montserrat" font-weight="600" font-size="28" letter-spacing="9" fill="{c}">THE ONE WHO COMES TO HELP</text>
"""


def d_fam(c: str) -> str:
    return f"""
{falcon_head(500, 300, 300, c)}
<text x="512" y="590" text-anchor="middle" font-family="Cinzel" font-weight="900" font-size="170" letter-spacing="22" fill="{c}">FAZZA</text>
<text x="514" y="725" text-anchor="middle" font-family="Montserrat" font-weight="800" font-size="150" letter-spacing="40" fill="{c}">FAM</text>
<rect x="240" y="740" width="520" height="4" fill="{c}"/>
<text x="500" y="805" text-anchor="middle" font-family="Montserrat" font-weight="700" font-size="40" letter-spacing="16" fill="{c}">MEMBERS CLUB · DUBAI</text>
"""


def d_skyline(c: str) -> str:
    return f"""
{skyline(80, 640, 840, c)}
<text x="500" y="800" text-anchor="middle" font-family="Cinzel" font-weight="900" font-size="150" letter-spacing="60" fill="{c}">DUBAI</text>
<text x="500" y="880" text-anchor="middle" font-family="Reem Kufi" font-weight="700" font-size="70" fill="{c}">دبي</text>
"""


def d_royalty(c: str) -> str:
    return f"""
{sun_dunes(500, 360, 150, c)}
<text x="500" y="640" text-anchor="middle" font-family="Cinzel" font-weight="900" font-size="128" letter-spacing="18" fill="{c}">DESERT</text>
<text x="500" y="770" text-anchor="middle" font-family="Cinzel" font-weight="900" font-size="128" letter-spacing="18" fill="{c}">ROYALTY</text>
<text x="500" y="840" text-anchor="middle" font-family="Montserrat" font-weight="700" font-size="27" letter-spacing="7" fill="{c}">BORN IN THE DUNES · RAISED BY FALCONS</text>
"""


def d_sky(c: str) -> str:
    return f"""
{falcon_head(500, 400, 560, c)}
<text x="500" y="790" text-anchor="middle" font-family="Playfair Display" font-style="italic" font-weight="600" font-size="88" fill="{c}">The sky belongs</text>
<text x="500" y="880" text-anchor="middle" font-family="Playfair Display" font-style="italic" font-weight="600" font-size="88" fill="{c}">to those who dare.</text>
"""


DESIGNS = {
    "01 Hamdanians Crest": d_crest,
    "02 Fazza Calligraphy": d_calligraphy,
    "03 Fazza Fam": d_fam,
    "04 Dubai Skyline": d_skyline,
    "05 Desert Royalty": d_royalty,
    "06 The Sky Belongs": d_sky,
}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{fonts}">
<style>html,body{{margin:0;background:transparent}} svg{{display:block}} :root{{--cut:{cut}}}</style></head>
<body><svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000">{body}</svg></body></html>"""


def render(page, svg_body: str, out: Path, cut: str) -> None:
    page.set_content(PAGE.format(fonts=FONTS, body=svg_body, cut=cut), wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(600)
    page.locator("svg").screenshot(path=str(out), omit_background=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROME) if CHROME else None)
        page = browser.new_page(viewport={"width": 1000, "height": 1000}, device_scale_factor=4)
        for name, fn in DESIGNS.items():
            # "cut" colour = the garment showing through (eyes, dunes); transparent PNG needs a real colour, so the
            # gold version cuts in near-black (prints as fabric on black tees) and the black version in white
            render(page, fn(GOLD), OUT / f"{name} - gold (dark shirts).png", "#111111")
            render(page, fn("#111111"), OUT / f"{name} - black (light items).png", "#ffffff")
            print("rendered", name)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
