"""Desktop stand-ins for rendering and testing the real display adapter."""

import sys
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
        self.__dict__.update(kwargs)


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
    sys.modules["terminalio"] = SimpleNamespace(FONT=None)
    sys.modules["adafruit_display_text"] = SimpleNamespace(
        bitmap_label=SimpleNamespace(Label=Label)
    )
