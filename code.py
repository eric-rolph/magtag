# SPDX-License-Identifier: MIT
"""
MagTag PM-BME-Zambretti Weather Station

Application for the Adafruit MagTag that:
- Monitors PM2.5 using a PM25 sensor
- Measures temperature, humidity, and pressure via BME280
- Displays current readings and historical graphs with improved UI
- Predicts weather via the Zambretti algorithm
- Shows EPA 2024 PM2.5 air quality categories

Hardware:
- Adafruit MagTag (ESP32-S2 with E-Ink)
- PM2.5 Air Quality Sensor (I2C)
- BME280 Sensor (I2C)

Button Functions:
- A: Cycle data display (PM2.5, Pressure, Temperature, Humidity)
- B: Change sparkline graph time resolution
- C: Adjust LED brightness
"""

# Required Libraries
import time, board, busio, displayio, gc, terminalio, math # math is still needed for pow
from adafruit_pm25.i2c import PM25_I2C
from adafruit_bme280 import basic as adafruit_bme280
from adafruit_display_text import bitmap_label as label
from adafruit_magtag.magtag import MagTag

class SensorData:
    def __init__(self, elevation_m=1609): # Default elevation for Denver, CO approx.
        self.elevation_m = elevation_m
        self.i2c = busio.I2C(board.SCL, board.SDA, frequency=100_000)
        self.pm25 = PM25_I2C(self.i2c, None)
        self.bme = adafruit_bme280.Adafruit_BME280_I2C(self.i2c)
        self.pm = None
        self.temperature_c = None
        self.temperature_f = None
        self.humidity = None
        self.pressure_raw = None
        self.pressure = None

    def read_sensors(self):
        partial_success = False
        try:
            pm_data = self.pm25.read()
            self.pm = pm_data["pm25 env"]
            partial_success = True
        except (RuntimeError, TypeError) as e:
            print(f"PM2.5 read error: {e}")
            self.pm = None

        try:
            self.temperature_c = self.bme.temperature
            self.humidity = self.bme.humidity
            self.pressure_raw = self.bme.pressure
            partial_success = True # Even if only BME works, it's a partial success

            if self.temperature_c is not None and self.pressure_raw is not None:
                self.temperature_f = self.temperature_c * 9 / 5 + 32
                slp_hpa = self.sea_level_pressure(self.pressure_raw, self.temperature_c, self.elevation_m)
                if slp_hpa is not None:
                    self.pressure = slp_hpa
                else:
                    self.pressure = None # Could not calculate sea level pressure
            else:
                self.temperature_f = None
                self.pressure = None # Missing temp or raw pressure for SLP calc

        except RuntimeError as e:
            print(f"BME280 read error: {e}")
            self.temperature_c = None
            self.temperature_f = None
            self.humidity = None
            self.pressure_raw = None
            self.pressure = None # BME error means no pressure data

        return partial_success

    def sea_level_pressure(self, p_abs_hpa, t_c, elev_m):
        if p_abs_hpa is None or t_c is None or elev_m is None:
            return None
        try:
            temp_factor_for_base = t_c + (0.0065 * elev_m) + 273.15
            if temp_factor_for_base == 0: return None # Avoid division by zero

            base = (1 - (0.0065 * elev_m) / temp_factor_for_base)
            if base <= 0: return None # Avoid math domain error for pow if base is non-positive

            slp_hpa = p_abs_hpa / pow(base, 5.257)
            return slp_hpa
        except Exception as e: # Catch any math errors
            print(f"Error calculating sea level pressure: {e}")
            return None

    def get_air_quality_category(self):
        if self.pm is None:
            return "Unknown", (100, 100, 100) # Gray for unknown
        if self.pm <= 9.0: return "Good", (0, 128, 0)
        if self.pm <= 35.4: return "Moderate", (225, 225, 0)
        if self.pm <= 55.4: return "Sensitive", (255, 126, 0)
        if self.pm <= 125.4: return "Unhealthy", (200, 0, 0)
        if self.pm <= 225.4: return "VeryUnhealthy", (128, 0, 128)
        return "Hazardous", (100, 0, 0)

class PressureTrend:
    DELTA_THRESHOLD = 0.5
    def __init__(self, buffer_size=180):
        self.buffer_size = buffer_size
        self.p_buffer = [None] * buffer_size
        self.index = 0
        self.count = 0

    def add_reading(self, pressure):
        if pressure is None:
            return
        self.p_buffer[self.index] = pressure
        self.index = (self.index + 1) % self.buffer_size
        if self.count < self.buffer_size:
            self.count += 1

    def classify_trend(self):
        lookback_period = 10
        if self.count < lookback_period:
            return "-"
        current_idx = (self.index - 1 + self.buffer_size) % self.buffer_size
        prev_idx_offset = min(self.count -1, lookback_period -1)
        if prev_idx_offset < 1: return "-"
        prev_idx = (current_idx - prev_idx_offset + self.buffer_size) % self.buffer_size
        current_p = self.p_buffer[current_idx]
        prev_p = self.p_buffer[prev_idx]
        if current_p is None or prev_p is None:
            return "-"
        delta = current_p - prev_p
        if delta > self.DELTA_THRESHOLD: return "rising"
        elif delta < -self.DELTA_THRESHOLD: return "falling"
        return "steady"

class ZambrettiForecaster:
    Z = [
        None, "Settled Fine", "Fine Weather", "Becoming Fine", "Fairly Fine, Imprv",
        "Fairly Fine, Showers?", "Showery, Bec. Unsettled", "Changeable, Some Rain",
        "Unsettled, Rain Later", "Rain at Times, Worse", "Rain Times, V.Unsettled",
        "Very Unsettled, Rain", "Settled Fine", "Fine Weather", "Becoming Fine",
        "Fairly Fine, Imprv", "Fairly Fine, Shwrs Poss", "Showery Early, Imprv",
        "Changeable, Some Rain", "Unsettled, Rain Times", "Rain Dev, Bec. Unsettled",
        "Rain Times, V.Unsettled", "Fine, Less Settled", "Fairly Fine, Shwrs Poss",
        "Showery, Bright Int.", "Changeable, Some Rain", "Unsettled, Rain Later",
        "Unsettled, Rain Dev.", "Rain Times, V.Unsettled", "Very Unsettled, Rain",
        "Stormy, Much Rain", "Stormy, Imprv Later", "Stormy, Clearing Later"
    ]
    def get_forecast(self, pressure_hpa, trend_str):
        if pressure_hpa is None or trend_str == "-":
            return "Forecast: Pending"
        z_idx = self._calculate_z_index(pressure_hpa, trend_str)
        if 1 <= z_idx < len(self.Z) and self.Z[z_idx] is not None:
            return self.Z[z_idx]
        return "Forecast: N/A"

    def _calculate_z_index(self, p_hpa, trend):
        val = 0
        if trend == "rising":
            val = 127 - (0.12 * p_hpa)
            if p_hpa > 1030: val -= 2
            elif p_hpa > 1015: val -=1
        elif trend == "falling":
            val = 138 - (0.13 * p_hpa)
            if p_hpa < 985: val += 2
            elif p_hpa < 1000: val += 1
        else: # Steady
            val = 130 - (0.125 * p_hpa)
        if trend == "rising": val -= 3
        elif trend == "falling": val += 3
        return max(1, min(int(round(val)), len(self.Z) - 1))

class DataHistory:
    def __init__(self, max_samples=10080): # Default to 1 week (10080 samples at 1 sample/minute)
                                          # Original was 1440 for 24 hours.
                                          # NOTE: This increases RAM usage significantly.
                                          # 10080 samples * 4 sensors * ~4 bytes/sample = ~157 KB
        self.max_samples = max_samples
        self.h_pm = [None] * max_samples
        self.h_p = [None] * max_samples
        self.h_t = [None] * max_samples
        self.h_h = [None] * max_samples
        self.write_index = 0
        self.data_count = 0

    def add_reading(self, pm, pressure, temp, humidity):
        self.h_pm[self.write_index] = pm
        self.h_p[self.write_index] = pressure
        self.h_t[self.write_index] = temp
        self.h_h[self.write_index] = humidity
        self.write_index = (self.write_index + 1) % self.max_samples
        if self.data_count < self.max_samples:
            self.data_count += 1

    def get_series(self, series_index):
        series_list = (self.h_pm, self.h_p, self.h_t, self.h_h)[series_index]
        if self.data_count == 0:
            return []
        if self.data_count < self.max_samples:
            return series_list[:self.data_count]
        else:
            ordered_series = series_list[self.write_index:] + series_list[:self.write_index]
            return ordered_series

    def get_current_value(self, series_index):
        if self.data_count == 0: return None
        series_list = (self.h_pm, self.h_p, self.h_t, self.h_h)[series_index]
        read_idx = (self.write_index - 1 + self.max_samples) % self.max_samples
        return series_list[read_idx]

    def get_stats(self, series_index, num_samples_to_consider=None):
        full_series_data = self.get_series(series_index)
        if num_samples_to_consider is None or num_samples_to_consider > len(full_series_data):
            data_for_stats = full_series_data
        else:
            actual_samples = min(num_samples_to_consider, len(full_series_data))
            if actual_samples <= 0:
                 return None, None, None
            data_for_stats = full_series_data[-actual_samples:]
        valid_values = [v for v in data_for_stats if v is not None]
        if not valid_values:
            return None, None, None
        min_val = min(valid_values)
        avg_val = sum(valid_values) / len(valid_values)
        max_val = max(valid_values)
        return min_val, avg_val, max_val

class LEDManager:
    BRIGHTNESS_LEVELS = [0.00, 0.12, 0.25, 0.38, 0.50, 0.62, 0.75, 1.00]
    def __init__(self, neopixels):
        self.pixels = neopixels
        self.pixels.auto_write = False
        self.brightness_idx = 2
        self.pixels.brightness = self.BRIGHTNESS_LEVELS[self.brightness_idx]

    def set_color(self, color):
        self.pixels.fill(color if color else (0,0,0))
        self.pixels.show()

    def cycle_brightness(self):
        self.brightness_idx = (self.brightness_idx + 1) % len(self.BRIGHTNESS_LEVELS)
        self.pixels.brightness = self.BRIGHTNESS_LEVELS[self.brightness_idx]
        self.pixels.show()

class Display:
    DATA_TYPES = ("PM2.5", "Pressure", "Temp", "Humidity")
    UNITS = (" ug/m3", " hPa", " F", " %RH")
    RESOLUTIONS = [
        60,      # 1 hour
        360,     # 6 hours
        720,     # 12 hours
        1440,    # 1 day (24 hours)
        4320,    # 3 days (72 hours)
        10080    # 1 week (168 hours)
    ]
    PM25_IDX, PRES_IDX, TEMP_IDX, HUM_IDX = 0, 1, 2, 3

    def __init__(self, magtag):
        self.magtag = magtag
        self.display = magtag.graphics.display
        self.res_idx = 2 # Default to index 2: 720 minutes (12h)
        self.graph_resolution_minutes = self.RESOLUTIONS[self.res_idx]
        self.root = displayio.Group()
        self.graph_idx = self.PM25_IDX
        self.refresh_needed = True
        self.setup_background()
        self.setup_sparkline_layout()
        self.setup_labels()
        self.display.show(self.root)

    def cycle_resolution(self):
        self.res_idx = (self.res_idx + 1) % len(self.RESOLUTIONS)
        self.graph_resolution_minutes = self.RESOLUTIONS[self.res_idx]
        self.refresh_needed = True

    def setup_background(self):
        bg_pal = displayio.Palette(1)
        bg_pal[0] = 0xFFFFFF
        bg_img = displayio.Bitmap(self.display.width, self.display.height, 1)
        bg_tile = displayio.TileGrid(bg_img, pixel_shader=bg_pal)
        self.root.append(bg_tile)

    def setup_sparkline_layout(self):
        self.graph_h = 48
        self.graph_label_width = 28
        self.graph_x_start = self.graph_label_width
        self.graph_w = self.display.width - self.graph_x_start - 2
        self.spark_y = self.display.height - self.graph_h - 1
        self.g_bmp = displayio.Bitmap(self.graph_w, self.graph_h, 2)
        g_pal = displayio.Palette(2)
        g_pal[0] = 0xFFFFFF
        g_pal[1] = 0x000000
        self.sparkline_tilegrid = displayio.TileGrid(self.g_bmp, pixel_shader=g_pal, x=self.graph_x_start, y=self.spark_y)
        self.root.append(self.sparkline_tilegrid)

    def setup_labels(self):
        self.lbl_head = self.make_label(self.DATA_TYPES[self.graph_idx], x=4, y=5, scale=1)
        initial_res_text = f"({self.graph_resolution_minutes // 60}h)" if self.graph_resolution_minutes >= 60 else f"({self.graph_resolution_minutes}m)"
        self.lbl_graph_res_indicator = self.make_label(initial_res_text, x=70, y=5, scale=1)
        self.lbl_aqi_status = self.make_label("AQ: ---", x=self.display.width - 135, y=5, scale=1)
        self.lbl_big = self.make_label("--.-", x=4, y=28, scale=3)
        self.lbl_stats = self.make_label("min/avg/max ---/---/---", x=4, y=55, scale=1)
        self.lbl_fc = self.make_label("Forecast: Pending", x=4, y=67, scale=1)
        rhs_x = 220
        self.lbl_pres_rhs = self.make_label("P:----.- -", x=rhs_x, y=22, scale=1)
        self.lbl_temp_rhs = self.make_label("T:--.- F", x=rhs_x, y=34, scale=1)
        self.lbl_hum_rhs = self.make_label("H:--- %", x=rhs_x, y=46, scale=1)
        self.lbl_pm_sm_rhs = self.make_label("PM:--.-", x=rhs_x, y=58, scale=1)
        self.lbl_graph_max = self.make_label("---", x=2, y=self.spark_y + 1, scale=1)
        self.lbl_graph_min = self.make_label("---", x=2, y=self.spark_y + self.graph_h - 9, scale=1)

    def make_label(self, txt, x, y, scale=1):
        l = label.Label(terminalio.FONT, text=str(txt) if txt is not None else "", color=0x000000, x=x, y=y, scale=scale)
        self.root.append(l)
        return l

    def update_sensor_display(self, sensors, trend_char, forecast_text):
        aq_text, _ = sensors.get_air_quality_category()
        self.lbl_aqi_status.text = f"AQ: {aq_text}"
        if self.graph_idx == self.PM25_IDX: self.lbl_pm_sm_rhs.text = ""
        elif sensors.pm is not None: self.lbl_pm_sm_rhs.text = f"PM:{sensors.pm:4.1f}"
        else: self.lbl_pm_sm_rhs.text = "PM: Err"
        if self.graph_idx == self.PRES_IDX: self.lbl_pres_rhs.text = ""
        elif sensors.pressure is not None: self.lbl_pres_rhs.text = f"P:{sensors.pressure:6.1f}{trend_char}"
        else: self.lbl_pres_rhs.text = "P:----.- -"
        if self.graph_idx == self.TEMP_IDX: self.lbl_temp_rhs.text = ""
        elif sensors.temperature_f is not None: self.lbl_temp_rhs.text = f"T:{sensors.temperature_f:4.1f}F"
        else: self.lbl_temp_rhs.text = "T:--.-F"
        if self.graph_idx == self.HUM_IDX: self.lbl_hum_rhs.text = ""
        elif sensors.humidity is not None: self.lbl_hum_rhs.text = f"H:{sensors.humidity:3.0f}%"
        else: self.lbl_hum_rhs.text = "H:---%"
        self.lbl_fc.text = forecast_text
        self.refresh_needed = True

    def update_graph_display(self, history):
        self.lbl_head.text = self.DATA_TYPES[self.graph_idx]
        try:
            bb = self.lbl_head.bounding_box
            base_x = self.lbl_head.x
            if bb and len(bb) >=3 : self.lbl_graph_res_indicator.x = base_x + bb[2] + 5
            else: self.lbl_graph_res_indicator.x = base_x + len(self.lbl_head.text)*6*self.lbl_head.scale + 5
        except AttributeError: self.lbl_graph_res_indicator.x = self.lbl_head.x + len(self.lbl_head.text)*6*self.lbl_head.scale + 5

        res_val = self.graph_resolution_minutes
        res_text = ""
        if res_val == 10080:    res_text = "(1w)"
        elif res_val == 4320:   res_text = "(3d)"
        elif res_val == 1440:   res_text = "(1d)"
        elif res_val == 720:    res_text = "(12h)"
        elif res_val >= 60 :    res_text = f"({res_val // 60}h)"
        else:                   res_text = f"({res_val}m)"
        self.lbl_graph_res_indicator.text = res_text

        current_val = history.get_current_value(self.graph_idx)
        unit_str = self.UNITS[self.graph_idx]
        default_text_float = f"--.-{unit_str}"
        default_text_int = f"---{unit_str}"
        if current_val is not None:
            if self.graph_idx == self.HUM_IDX: self.lbl_big.text = f"{current_val:3.0f}{unit_str}"
            else: self.lbl_big.text = f"{current_val:4.1f}{unit_str}"
        else: self.lbl_big.text = default_text_int if self.graph_idx == self.HUM_IDX else default_text_float
        min_val, avg_val, max_val = history.get_stats(self.graph_idx, self.graph_resolution_minutes)
        if min_val is not None:
            fmt = "{:3.0f}" if self.graph_idx == self.HUM_IDX else "{:4.1f}"
            self.lbl_stats.text = f"min/avg/max {fmt.format(min_val)}/{fmt.format(avg_val)}/{fmt.format(max_val)}"
        else: self.lbl_stats.text = "min/avg/max ---/---/---"
        full_series = history.get_series(self.graph_idx)
        points_for_resolution = self.graph_resolution_minutes
        num_points_to_plot = min(len(full_series), points_for_resolution)
        graph_segment = full_series[-num_points_to_plot:] if num_points_to_plot > 0 else []
        self.draw_graph(graph_segment)
        self.refresh_needed = True

    def draw_graph(self, segment_to_plot):
        for i in range(self.graph_w * self.graph_h): self.g_bmp[i] = 0
        valid_data_points = [s for s in segment_to_plot if s is not None]
        if not valid_data_points:
            self.lbl_graph_min.text = "---"
            self.lbl_graph_max.text = "---"
            return
        data_min = min(valid_data_points)
        data_max = max(valid_data_points)
        plot_min = data_min
        plot_max = data_max
        epsilon = 1e-5
        if abs(data_min - data_max) < epsilon:
            padding = 0.5 if self.graph_idx != self.HUM_IDX else 1
            plot_min = data_min - padding
            plot_max = data_max + padding
        plot_range = max(plot_max - plot_min, 1e-6)
        label_fmt = "{:.0f}" if self.graph_idx == self.HUM_IDX else "{:.1f}"
        self.lbl_graph_min.text = label_fmt.format(plot_min)
        self.lbl_graph_max.text = label_fmt.format(plot_max)
        if self.graph_idx == self.PM25_IDX:
            threshold_levels = [9.0, 35.4, 55.4]
            for th_val in threshold_levels:
                if plot_min <= th_val <= plot_max:
                    y_th_pix = self.graph_h - 1 - int(((th_val - plot_min) / plot_range) * (self.graph_h - 1))
                    y_th_pix = max(0, min(self.graph_h - 1, y_th_pix))
                    for x_px in range(0, self.graph_w, 4):
                        if 0 <= y_th_pix < self.graph_h:
                            self.g_bmp[x_px, y_th_pix] = 1
                            if x_px + 1 < self.graph_w: self.g_bmp[x_px + 1, y_th_pix] = 1
        num_points_in_segment = len(segment_to_plot)
        if num_points_in_segment == 0: return
        last_x_pixel = -1
        for i in range(num_points_in_segment):
            x_pixel = int(i * (self.graph_w -1) / (num_points_in_segment -1 )) if num_points_in_segment > 1 else 0
            y_data_val = segment_to_plot[i]
            if y_data_val is not None:
                y_pixel_on_graph = self.graph_h - 1 - int(((y_data_val - plot_min) / plot_range) * (self.graph_h - 1))
                y_pixel_on_graph = max(0, min(self.graph_h - 1, y_pixel_on_graph))
                if 0 <= x_pixel < self.graph_w:
                    if x_pixel != last_x_pixel or i == 0:
                        self.g_bmp[x_pixel, y_pixel_on_graph] = 1
                        last_x_pixel = x_pixel

    def next_graph_type(self):
        self.graph_idx = (self.graph_idx + 1) % len(self.DATA_TYPES)
        self.refresh_needed = True

    def refresh(self):
        if self.refresh_needed and getattr(self.display, "time_to_refresh", 0) == 0:
            try:
                self.display.refresh()
                self.refresh_needed = False
                return True
            except RuntimeError as e:
                print(f"Display refresh error: {e}")
                return False
        return False

class WeatherStation:
    def __init__(self):
        self.magtag = MagTag()
        self.sensors = SensorData()
        self.pressure_trend = PressureTrend()
        self.forecaster = ZambrettiForecaster()
        self.history = DataHistory()
        self.display = Display(self.magtag)
        self.leds = LEDManager(self.magtag.peripherals.neopixels)
        self.next_reading_time = time.monotonic()
        self.reading_interval_seconds = 60

    def check_buttons(self):
        button_pressed_this_cycle = False
        if self.magtag.peripherals.button_a_pressed:
            self.display.next_graph_type()
            while self.magtag.peripherals.button_a_pressed: time.sleep(0.05)
            button_pressed_this_cycle = True
        if self.magtag.peripherals.button_b_pressed: # Graph resolution
            self.display.cycle_resolution()
            while self.magtag.peripherals.button_b_pressed: time.sleep(0.05)
            button_pressed_this_cycle = True
        if self.magtag.peripherals.button_c_pressed: # LED brightness
            self.leds.cycle_brightness()
            while self.magtag.peripherals.button_c_pressed: time.sleep(0.05)
        if button_pressed_this_cycle:
            self.display.refresh_needed = True

    def update_readings_and_forecast(self):
        if time.monotonic() < self.next_reading_time:
            return False
        self.next_reading_time += self.reading_interval_seconds
        self.sensors.read_sensors()
        if self.sensors.pressure is not None:
            self.pressure_trend.add_reading(self.sensors.pressure)
        current_trend_str = self.pressure_trend.classify_trend()
        current_forecast = self.forecaster.get_forecast(self.sensors.pressure, current_trend_str)
        self.history.add_reading(
            self.sensors.pm, self.sensors.pressure,
            self.sensors.temperature_f, self.sensors.humidity
        )
        trend_display_char = current_trend_str[0].upper() if current_trend_str and current_trend_str != "-" else "-"
        self.display.update_sensor_display(self.sensors, trend_display_char, current_forecast)
        _, aqi_color = self.sensors.get_air_quality_category()
        self.leds.set_color(aqi_color)
        return True

    def run(self):
        self.update_readings_and_forecast()
        self.display.update_graph_display(self.history)
        self.display.refresh()
        while True:
            self.check_buttons()
            new_data_processed = self.update_readings_and_forecast()
            if self.display.refresh_needed or new_data_processed:
                self.display.update_graph_display(self.history)
            self.display.refresh()
            gc.collect()
            time_to_next_reading = self.next_reading_time - time.monotonic()
            sleep_duration = max(0.1, min(time_to_next_reading, 1.0))
            time.sleep(sleep_duration)

if __name__ == "__main__":
    station = WeatherStation()
    station.run()