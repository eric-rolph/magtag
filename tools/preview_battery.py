"""Render actual display trees for synthetic battery states."""
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.display_stubs import FakeDisplay, install
from tools.preview_display import desktop_font, render_screen
from weather_core import DataHistory
import weather_config as config


def main():
    install()
    from weather_display import Display

    history = DataHistory(10)
    history.add_reading(12, 1013, 72.4, 43, 0)
    font = desktop_font()
    states = (
        ("Battery / learning", ("~75%", "Bat ~75% 3.97V | Left Learning")),
        ("Battery / trend available", ("~50%", "Bat ~50% 3.80V | Left ~6h")),
        ("Power source unconfirmed", ("PWR ?", "PWR ? 4.10V | Hold B on battery")),
        ("USB connected", ("USB", "USB 4.10V | Hold B on battery")),
    )
    sheet = Image.new("RGB", (620, 660), "#e9edf1")
    draw = ImageDraw.Draw(sheet)
    for row, (title, text) in enumerate(states):
        view = Display(FakeDisplay(), config)
        sensors = SimpleNamespace(pm=12, pressure=1013, temperature_f=72.4, humidity=43)
        view.update_sensors(sensors, "-", "Outlook: collecting 3h", 3.97)
        view.update_details(10, 12, None, {"aqi": None}, "Alerts: clear", None)
        view.update_battery(text)
        for col, screen in enumerate(("home", "details")):
            view.screen = screen
            view._show_screen()
            x, y = 8+308*col, 25+160*row
            draw.text((x,y-18),title + " / " + screen,fill="black",font=font)
            sheet.paste(render_screen(view,font),(x,y))
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True,exist_ok=True)
    sheet.resize((1240,1320),Image.Resampling.NEAREST).save(output)
    print(output)


if __name__ == "__main__":
    main()
