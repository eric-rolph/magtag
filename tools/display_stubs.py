"""Desktop stand-ins for rendering and testing the real display adapter."""

import sys
from pathlib import Path
from types import SimpleNamespace


class Bitmap:
    def __init__(self, width, height, colors):
        self.width, self.height = width, height
        self.pixels = [0] * (width * height)

    def fill(self, value):
        self.pixels[:] = [value] * len(self.pixels)

    def __setitem__(self, position, value):
        x, y = position
        assert 0 <= x < self.width and 0 <= y < self.height
        self.pixels[y * self.width + x] = value


class Group(list):
    def __init__(self, *, x=0, y=0, scale=1):
        super().__init__()
        self.x, self.y, self.scale = x, y, scale
        self.hidden = False


class Palette(list):
    def __init__(self, count):
        super().__init__([0] * count)
        self.transparent = set()

    def make_transparent(self, index):
        self.transparent.add(index)

    def make_opaque(self, index):
        self.transparent.discard(index)


class Label:
    def __init__(self, font, **kwargs):
        self.font = font
        self.x = self.y = 0
        self.__dict__.update(kwargs)

    @property
    def bounding_box(self):
        if self.font is None or isinstance(self.font, TerminalFont):
            return (0, -6, len(self.text) * 6, 12)
        font = getattr(self.font, "source_font", self.font)
        bx, _, width, height = font.bounds(self.text)
        return (bx, -((self.font.ascent + 1) // 2), width, height)


class TerminalFont:
    """Approximate desktop line cells; native tests check actual visible pixels."""

    def get_bounding_box(self):
        return (6, 12, 0, 0)


class BDFFont:
    """Read real font pixels for desktop preview; no device font substitutions."""

    def __init__(self, filename):
        filename = Path(__file__).resolve().parents[1] / filename
        self.glyphs = {}
        glyph = None
        bitmap = False
        for line in filename.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "STARTCHAR":
                glyph, bitmap = {"rows": []}, False
            elif fields[0] == "ENCODING":
                glyph["code"] = int(fields[1])
            elif fields[0] == "DWIDTH":
                glyph["advance"] = int(fields[1])
            elif fields[0] == "BBX":
                glyph["w"], glyph["h"], glyph["x"], glyph["y"] = map(int, fields[1:])
            elif fields[0] == "BITMAP":
                bitmap = True
            elif fields[0] == "ENDCHAR":
                self.glyphs[chr(glyph["code"])] = glyph
                bitmap = False
            elif bitmap:
                w = glyph["w"]
                glyph["rows"].append(int(line, 16) >> (8 * ((w + 7) // 8) - w))

    def load_glyphs(self, characters):
        assert all(character in self.glyphs for character in characters)

    def get_glyph(self, code_point):
        glyph = self.glyphs[chr(code_point)]
        return SimpleNamespace(width=glyph["w"], height=glyph["h"],
                               dx=glyph["x"], dy=glyph["y"], shift_x=glyph["advance"])

    def bounds(self, text):
        cursor = 0
        left = top = right = bottom = None
        for char in text:
            g = self.glyphs[char]
            if g["w"] and g["h"]:
                x, y = cursor + g["x"], -g["h"] - g["y"]
                left = x if left is None else min(left, x)
                top = y if top is None else min(top, y)
                right = x + g["w"] if right is None else max(right, x + g["w"])
                bottom = -g["y"] if bottom is None else max(bottom, -g["y"])
            cursor += g["advance"]
        return (left or 0, top or 0, (right or 0) - (left or 0), (bottom or 0) - (top or 0))


class TileGrid:
    def __init__(self, bitmap, pixel_shader, x=0, y=0):
        self.bitmap, self.x, self.y = bitmap, x, y
        self.pixel_shader = pixel_shader
        self.hidden = False


class FakeDisplay:
    width, height = 296, 128
    time_to_refresh = 0
    busy = False

    def __init__(self):
        self.refreshes = 0
        self.fail_refresh = False
        self.shown = None

    def show(self, group):
        self.shown = group

    def refresh(self):
        if self.fail_refresh:
            raise RuntimeError("Busy")
        self.refreshes += 1


def install():
    sys.modules["displayio"] = SimpleNamespace(
        Group=Group, Palette=Palette, Bitmap=Bitmap, TileGrid=TileGrid
    )
    sys.modules["terminalio"] = SimpleNamespace(FONT=TerminalFont())
    sys.modules["adafruit_display_text"] = SimpleNamespace(
        bitmap_label=SimpleNamespace(Label=Label)
    )
    sys.modules["adafruit_bitmap_font"] = SimpleNamespace(
        bitmap_font=SimpleNamespace(load_font=BDFFont)
    )
