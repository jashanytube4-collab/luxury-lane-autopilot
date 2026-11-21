"""Icon-style profile pictures for the 13 brand channels (no Claude). python -m tools.channel_avatars
Writes rocky/channels/brand/<handle>_avatar.png (800x800) and a contact sheet work/wm/avatars_new.png."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BR = ROOT / "rocky" / "channels" / "brand"
S = 1600          # draw size, downscaled to 800
C0 = S // 2
BG = (10, 13, 18, 255)


def hexc(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def mix(c, t, k):
    return tuple(int(c[i] * (1 - k) + t[i] * k) for i in range(3)) + (255,)


def poly(d, pts, fill):
    d.polygon([(C0 + x, C0 + y) for x, y in pts], fill=fill)


def circ(d, x, y, r, fill=None, outline=None, w=0):
    d.ellipse((C0 + x - r, C0 + y - r, C0 + x + r, C0 + y + r), fill=fill, outline=outline, width=w)


def line(d, pts, fill, w):
    d.line([(C0 + x, C0 + y) for x, y in pts], fill=fill, width=w, joint="curve")


def waves(d, y, col, w=34, amp=26, x0=-380, x1=380):
    line(d, [(x, y + amp * math.sin((x - x0) / 55)) for x in range(x0, x1 + 1, 8)], col, w)


def bez(p0, p1, p2, n=24):
    out = []
    for i in range(n + 1):
        t = i / n
        out.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]))
    return out


def chain(*segs):
    pts = list(segs[0])
    for s in segs[1:]:
        pts += list(s)[1:]
    return pts


def icon(d, kind, col, wh):
    imp = "C:/Windows/Fonts/impact.ttf"
    if kind == "AA":      # shark fin over waves
        poly(d, chain(bez((-170, 130), (-60, -60), (60, -330)), bez((60, -330), (70, -120), (230, 130))), wh)
        waves(d, 150, col)
        waves(d, 240, mix(col, (0, 0, 0), .35), 28)
    elif kind == "LC":    # magnifier with question mark
        circ(d, -50, -60, 250, outline=wh, w=56)
        line(d, [(130, 120), (290, 290)], wh, 72)
        d.text((C0 - 50, C0 - 40), "?", font=ImageFont.truetype(imp, 320), fill=col, anchor="mm")
    elif kind == "NS":    # lightning bolt in a node ring
        for a in range(0, 360, 60):
            x, y = 330 * math.cos(math.radians(a)), 330 * math.sin(math.radians(a))
            line(d, [(0, 0), (x, y)], mix(col, (0, 0, 0), .5), 14)
            circ(d, x, y, 28, fill=col)
        poly(d, [(40, -300), (-150, 40), (-10, 40), (-60, 300), (160, -80), (20, -80)], wh)
    elif kind == "MA":    # flame
        poly(d, chain(bez((0, 320), (-330, 200), (-150, -60)), bez((-150, -60), (-120, -160), (0, -330)),
                      bez((0, -330), (40, -120), (160, -50)), bez((160, -50), (330, 150), (0, 320))), wh)
        poly(d, chain(bez((0, 280), (-150, 200), (-60, 40)), bez((-60, 40), (-20, 100), (20, 0)),
                      bez((20, 0), (140, 150), (0, 280))), col)
    elif kind == "FA":    # compass rose
        poly(d, [(0, -340), (50, -50), (0, 0), (-50, -50)], wh)
        poly(d, [(0, 340), (50, 50), (0, 0), (-50, 50)], col)
        poly(d, [(-340, 0), (-50, -50), (0, 0), (-50, 50)], col)
        poly(d, [(340, 0), (50, -50), (0, 0), (50, 50)], wh)
        circ(d, 0, 0, 230, outline=wh, w=22)
        circ(d, 0, 0, 40, fill=wh)
    elif kind == "BO":    # </> brackets
        line(d, [(-90, -190), (-290, 0), (-90, 190)], wh, 70)
        line(d, [(90, -190), (290, 0), (90, 190)], wh, 70)
        line(d, [(40, -250), (-40, 250)], col, 60)
    elif kind == "PR":    # open book + cross
        poly(d, [(-340, -60), (-20, 0), (-20, 260), (-340, 200)], wh)
        poly(d, [(340, -60), (20, 0), (20, 260), (340, 200)], mix(wh, col, .25))
        poly(d, [(-30, -300), (30, -300), (30, -230), (100, -230), (100, -170), (30, -170), (30, -100), (-30, -100),
                 (-30, -170), (-100, -170), (-100, -230), (-30, -230)], col)
    elif kind == "HM":    # eye with rising arrow
        poly(d, chain(bez((-370, 0), (0, -330), (370, 0)), bez((370, 0), (0, 330), (-370, 0))), wh)
        circ(d, 0, 0, 150, fill=col)
        line(d, [(-70, 60), (-10, 0), (30, 30), (90, -50)], wh, 28)
        poly(d, [(60, -75), (115, -75), (115, -20)], wh)
    elif kind == "LL":    # whale tail over waves
        poly(d, chain(bez((-30, 200), (-20, 40), (-40, -60)), bez((-40, -60), (-120, -80), (-340, -200)),
                      bez((-340, -200), (-200, 0), (-10, 60)), [(10, 60)], bez((10, 60), (200, 0), (340, -200)),
                      bez((340, -200), (120, -80), (40, -60)), bez((40, -60), (20, 40), (30, 200))), wh)
        waves(d, 210, col)
    elif kind == "EE":    # temple
        poly(d, [(-330, -120), (0, -320), (330, -120)], wh)
        poly(d, [(-330, -90), (330, -90), (330, -50), (-330, -50)], col)
        for x in (-250, -80, 90, 260):
            poly(d, [(x - 36, -30), (x + 36, -30), (x + 30, 220), (x - 30, 220)], wh)
        poly(d, [(-350, 240), (350, 240), (350, 300), (-350, 300)], wh)
    elif kind == "BW":    # chess rook
        poly(d, [(-190, -300), (-110, -300), (-110, -240), (-40, -240), (-40, -300), (40, -300), (40, -240),
                 (110, -240), (110, -300), (190, -300), (190, -170), (120, -120), (140, 130), (230, 200), (230, 280),
                 (-230, 280), (-230, 200), (-140, 130), (-120, -120), (-190, -170)], wh)
        poly(d, [(-230, 200), (230, 200), (230, 235), (-230, 235)], col)
    elif kind == "MF":    # silhouette in crosshair
        circ(d, 0, -60, 90, fill=wh)
        poly(d, [(-200, 230), (-170, 70), (-90, 40), (90, 40), (170, 70), (200, 230)], wh)
        circ(d, 0, 0, 330, outline=col, w=26)
        for a in (0, 90, 180, 270):
            x, y = math.cos(math.radians(a)), math.sin(math.radians(a))
            line(d, [(x * 280, y * 280), (x * 390, y * 390)], col, 26)
    elif kind == "SA":    # warning triangle with dollar
        poly(d, [(0, -330), (360, 270), (-360, 270)], wh)
        poly(d, [(0, -250), (285, 235), (-285, 235)], mix(col, (0, 0, 0), .55))
        d.text((C0, C0 + 60), "$", font=ImageFont.truetype(imp, 330), fill=col, anchor="mm")


def make(mono, handle, colour):
    col = hexc(colour)
    bg = Image.new("RGBA", (S, S), BG)
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse((200, 200, S - 200, S - 200), fill=mix(col, BG, .72))
    bg = Image.alpha_composite(bg, g.filter(ImageFilter.GaussianBlur(60)))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((70, 70, S - 70, S - 70), outline=col, width=40)
    bg = Image.alpha_composite(bg, glow.filter(ImageFilter.GaussianBlur(26)))
    ImageDraw.Draw(bg).ellipse((70, 70, S - 70, S - 70), outline=col, width=34)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon(ImageDraw.Draw(layer), mono, col, (255, 255, 255, 255))
    big = layer.resize((int(S * 1.3), int(S * 1.3)), Image.LANCZOS)
    o = (big.width - S) // 2
    bg = Image.alpha_composite(bg, big.crop((o, o, o + S, o + S)))
    out = bg.resize((800, 800), Image.LANCZOS).convert("RGB")
    out.save(BR / f"{handle}_avatar.png")
    return out


if __name__ == "__main__":
    from tools.channel_brand import C
    ims = [make(m, handle, colour).resize((260, 260)) for name, handle, m, colour, *_ in C]
    sheet = Image.new("RGB", (260 * 7, 520), "white")
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % 7) * 260, (i // 7) * 260))
    sheet.save(ROOT / "work" / "wm" / "avatars_new.png")
    print("ok", len(ims))
