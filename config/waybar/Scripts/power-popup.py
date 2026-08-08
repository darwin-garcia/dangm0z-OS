#!/usr/bin/env python3
"""
power-popup.py
──────────────────────────────────────────────────────────────────────────
Popup interactivo de "Actividad de energía" para Waybar (Hyprland +
gtk-layer-shell). Mismo patrón que calendar-popup.py: ventana capa
(layer-shell), toggle por PID-file, estilo Tokyo Night.

AUTO-DETECCIÓN DE MÁQUINA
──────────────────────────
  · /sys/class/power_supply/BAT* existe → modo PORTÁTIL (X1 Carbon Gen 8)
        % batería · estado · vatios con signo · tiempo restante ·
        nombre/modelo de la batería · salud · ciclos de carga ·
        cargador conectado (con potencia estimada) · perfil de energía
        (power-profiles-daemon).

  · No existe                            → modo ESCRITORIO (Ryzen 5700G)
        Solo vatios del paquete CPU (RAPL o amd_energy) + gráfica.
        Sin nada de batería ni perfil de energía (no aplica en desktop).

GRÁFICA DE 60 MINUTOS
──────────────────────
La gráfica lee ~/.cache/power-history.json, que escribe un proceso
APARTE (power-history-daemon.py) una vez por minuto. Esto es necesario
porque el popup solo vive mientras está abierto — para mostrar una
ventana real de la última hora (con franjas horarias) hace falta un
proceso de fondo que siga muestreando aunque el popup esté cerrado.

Si el daemon no está corriendo (archivo ausente o vacío), el popup cae a
un muestreo propio en vivo de los últimos 60s, y lo deja claro en el
subtítulo de la gráfica en vez de mostrar datos engañosos.

Uso:
  chmod +x power-popup.py
  ./power-popup.py        # abre el popup; si ya está abierto, lo cierra (toggle)

Dependencias (Arch / CachyOS):
  sudo pacman -S python-gobject gtk3
  yay -S gtk-layer-shell          # AUR

Companion obligatorio para la gráfica de 60 min:
  power_common.py            (mismo directorio — módulo compartido)
  power-history-daemon.py    (ver ese archivo para instalarlo como
                               servicio systemd --user)

Wiring en Waybar (config JSONC):
  "custom/power": {
      "exec": "echo '⚡'",
      "on-click": "~/.config/waybar/Scripts/power-popup.py",
      "interval": "once"
  }

Blur (frosted glass) en hyprland.conf:
  layerrule {
      name = Power Popup Layer
      match:namespace = ^(power-popup)$
      blur = true
      ignore_alpha = false
  }
──────────────────────────────────────────────────────────────────────────
"""
import os
import sys
import signal
import json
import time
import datetime
import traceback
from collections import deque

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

GLib.set_prgname("power-popup")

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ImportError, ValueError):
    HAS_LAYER_SHELL = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from power_common import (
    find_battery_path,
    read_battery_snapshot,
    read_charger_info,
    read_ac_online,
    read_power_profile,
    battery_icon,
    PROFILE_DISPLAY,
    RECOMMENDED_CHARGER_W,
    WattSource,
    GpuWattSource,
)

PIDFILE = "/tmp/waybar-power-popup.pid"
LOGFILE = os.path.expanduser("~/.cache/waybar-power-popup.log")
HISTORY_FILE = os.path.expanduser("~/.cache/power-history.json")

STATS_TICK_S = 1          # refresco de los valores en vivo (%, W, perfil...)
GRAPH_POLL_S = 5          # cada cuánto se relee el archivo de historial
LIVE_FALLBACK_LEN = 60    # tamaño del buffer si no hay daemon (60 x 1s)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        with open(LOGFILE, "a") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except OSError:
        pass


def toggle_or_lock():
    if os.path.exists(PIDFILE):
        try:
            with open(PIDFILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
        finally:
            try:
                os.remove(PIDFILE)
            except OSError:
                pass
        sys.exit(0)

    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))


def release_lock():
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


def load_persisted_history():
    """Lee el historial escrito por power-history-daemon.py. Devuelve
    lista de dicts {"t":..., "w":...} o [] si no existe / está vacío /
    corrupto (no debe tumbar el popup)."""
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    except (OSError, ValueError):
        pass
    return []


# ── CSS embebido (Tokyo Night, coherente con calendar-popup.py) ──────────
CSS = """
window#power-popup {
    background-color: transparent;
    border: none;
    box-shadow: none;
}

#main-container {
    background-color: rgba(8, 9, 18, 0.80);
    border-radius: 24px;
    border: 1px solid rgba(255,255,255,0.12);
    margin: 8px;
    padding: 20px 26px;
}
* {
    font-family: "JetBrainsMono Nerd Font", "JetBrainsMono Nerd Font Mono", monospace;
}
label { color: #f1f1f1; }

#title-label { font-size: 13px; font-weight: bold; color: #a9b1d6; }

#watts-label { font-size: 40px; font-weight: bold; color: #f1f1f1; }
#watts-sub   { font-size: 11px; color: #a9b1d6; }

#battery-percent { font-size: 34px; font-weight: bold; color: #f1f1f1; }
#status-label { font-size: 12px; font-weight: bold; }
#status-label.charging { color: #9ece6a; }
#status-label.discharging { color: #7aa2f7; }
#status-label.full { color: #e0af68; }

#stat-key { font-size: 11px; color: #a9b1d6; }
#stat-val { font-size: 12px; font-weight: bold; color: #f1f1f1; }
#stat-val.small { font-size: 11px; }
#stat-val.warn { color: #e0af68; }

#section-label { font-size: 11px; font-weight: bold; color: #565f89; }

#profile-value { font-size: 13px; font-weight: bold; }
#profile-value.performance { color: #f7768e; }
#profile-value.balanced { color: #7aa2f7; }
#profile-value.power-saver { color: #9ece6a; }
#profile-value.unknown { color: #565f89; }

#axis-label { font-size: 9px; color: #565f89; }

#warn-label { font-size: 10px; color: #e0af68; }
#nodata-label { font-size: 12px; color: #565f89; }
"""

BOLT = "\u26a1"


class PowerPopup(Gtk.Window):
    def __init__(self):
        super().__init__(title="power-popup")
        self.set_name("power-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_role("power-popup")

        self.bat_path = find_battery_path()
        self.has_battery = self.bat_path is not None
        self.watt_source = WattSource() if not self.has_battery else None
        self.gpu_source = GpuWattSource() if not self.has_battery else None
        self.last_status = "Unknown"

        # Buffer de respaldo si el daemon de historial no está corriendo
        self.live_fallback = deque(maxlen=LIVE_FALLBACK_LEN)
        self.using_persisted_history = False
        self._graph_tick_counter = 0

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)

        if HAS_LAYER_SHELL:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, "power-popup")
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 10)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 8)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        else:
            self.set_position(Gtk.WindowPosition.CENTER)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_name("main-container")
        outer.set_border_width(16)
        self.add(outer)

        if not HAS_LAYER_SHELL:
            warn = Gtk.Label()
            warn.set_name("warn-label")
            warn.set_line_wrap(True)
            warn.set_justify(Gtk.Justification.CENTER)
            warn.set_text(
                "\u26a0 gtk-layer-shell not found.\n"
                "Get Install via (AUR): yay -S gtk-layer-shell"
            )
            outer.pack_start(warn, False, False, 0)

        title = Gtk.Label(label=f"{BOLT} Energy Status")
        title.set_name("title-label")
        title.set_halign(Gtk.Align.CENTER)
        outer.pack_start(title, False, False, 0)

        if self.has_battery:
            self._build_battery_ui(outer)
        else:
            self._build_desktop_ui(outer)

        outer.pack_start(Gtk.Separator(), False, False, 4)

        # ── Gráfica de consumo (Cairo, últimos 60 min vía daemon) ──────
        self.graph = Gtk.DrawingArea()
        self.graph.set_size_request(260, 74)
        self.graph.connect("draw", self.on_draw_graph)
        outer.pack_start(self.graph, False, False, 0)

        axis_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.axis_start = Gtk.Label(label="—")
        self.axis_start.set_name("axis-label")
        self.axis_start.set_halign(Gtk.Align.START)
        self.axis_mid = Gtk.Label(label="—")
        self.axis_mid.set_name("axis-label")
        self.axis_mid.set_hexpand(True)
        self.axis_mid.set_halign(Gtk.Align.CENTER)
        self.axis_end = Gtk.Label(label="—")
        self.axis_end.set_name("axis-label")
        self.axis_end.set_halign(Gtk.Align.END)
        axis_row.pack_start(self.axis_start, False, False, 0)
        axis_row.pack_start(self.axis_mid, True, True, 0)
        axis_row.pack_start(self.axis_end, False, False, 0)
        outer.pack_start(axis_row, False, False, 0)

        self.graph_sub = Gtk.Label(label="loading historial…")
        self.graph_sub.set_name("watts-sub")
        self.graph_sub.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self.graph_sub, False, False, 2)

        # ── Perfil de energía — solo tiene sentido en portátil ─────────
        if self.has_battery:
            outer.pack_start(Gtk.Separator(), False, False, 2)
            profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   halign=Gtk.Align.CENTER, spacing=8)
            section = Gtk.Label(label="Power Profile: ")
            section.set_name("section-label")
            profile_row.pack_start(section, False, False, 0)
            self.profile_lbl = Gtk.Label(label="—")
            self.profile_lbl.set_name("profile-value")
            profile_row.pack_start(self.profile_lbl, False, False, 0)
            outer.pack_start(profile_row, False, False, 0)

        self.update_graph_data()
        self.update_stats()
        GLib.timeout_add_seconds(STATS_TICK_S, self.update_stats)

        self.connect("key-press-event", self.on_key_press)
        self.connect("button-press-event", self.on_background_click)

    # ── UI: modo portátil ──────────────────────────────────────────────
    def _build_battery_ui(self, outer):
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER, spacing=10)

        # Icono de batería (mismo tamaño/color que el %, clase "battery-percent"
        # compartida) — se recalcula cada tick según capacidad + si está
        # cargando (ver battery_icon() en power_common.py).
        self.battery_icon_lbl = Gtk.Label()
        self.battery_icon_lbl.set_name("battery-percent")
        top_row.pack_start(self.battery_icon_lbl, False, False, 0)

        self.percent_lbl = Gtk.Label()
        self.percent_lbl.set_name("battery-percent")
        top_row.pack_start(self.percent_lbl, False, False, 0)

        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.status_lbl = Gtk.Label()
        self.status_lbl.set_name("status-label")
        status_box.pack_start(self.status_lbl, False, False, 0)
        top_row.pack_start(status_box, False, False, 0)

        outer.pack_start(top_row, False, False, 4)

        grid = Gtk.Grid(row_spacing=6, column_spacing=18)
        grid.set_halign(Gtk.Align.CENTER)

        # Orden pedido: Vatios, Tiempo restante, Nombre de la batería
        # (entre tiempo restante y salud), Salud, Ciclos de carga, Cargador.
        self.watts_val = self._stat_row(grid, 0, " Current")
        self.time_val = self._stat_row(grid, 1, " Remaining")
        self.name_val = self._stat_row(grid, 2, " Battery", small=True)
        self.health_val = self._stat_row(grid, 3, " Health")
        self.cycles_val = self._stat_row(grid, 4, "󱠵 Current Cycle Charge")
        self.charger_val = self._stat_row(grid, 5, " Plugged")

        outer.pack_start(grid, False, False, 4)

    def _stat_row(self, grid, row, key_text, small=False):
        key = Gtk.Label(label=key_text)
        key.set_name("stat-key")
        key.set_halign(Gtk.Align.START)
        val = Gtk.Label(label="—")
        val.set_name("stat-val")
        if small:
            val.get_style_context().add_class("small")
            val.set_line_wrap(True)
            val.set_max_width_chars(22)
            val.set_justify(Gtk.Justification.RIGHT)
        val.set_halign(Gtk.Align.END)
        grid.attach(key, 0, row, 1, 1)
        grid.attach(val, 1, row, 1, 1)
        return val

    # ── UI: modo escritorio ─────────────────────────────────────────────
    def _build_desktop_ui(self, outer):
        # Número grande = TOTAL del equipo (CPU + GPU dedicada si hay).
        watts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        self.watts_big_lbl = Gtk.Label(label="—")
        self.watts_big_lbl.set_name("watts-label")
        watts_box.pack_start(self.watts_big_lbl, False, False, 0)

        self.watts_sub_lbl = Gtk.Label(label="Total del equipo")
        self.watts_sub_lbl.set_name("watts-sub")
        watts_box.pack_start(self.watts_sub_lbl, False, False, 0)

        outer.pack_start(watts_box, False, False, 4)

        # Desglose CPU / GPU. La fila de GPU solo se crea si se detectó
        # una tarjeta dedicada — en un APU puro (sin dGPU) esa fila ni
        # existe, porque la iGPU ya va incluida en la lectura de CPU.
        grid = Gtk.Grid(row_spacing=6, column_spacing=18)
        grid.set_halign(Gtk.Align.CENTER)
        self.cpu_watt_val = self._stat_row(grid, 0, " CPU")
        self.cpu_watt_val.get_parent().get_child_at(0, 0).set_tooltip_text(self.watt_source.label)
        if self.gpu_source.backend is not None:
            self.gpu_watt_val = self._stat_row(grid, 1, " GPU")
            self.gpu_watt_val.get_parent().get_child_at(0, 1).set_tooltip_text(self.gpu_source.label)
        else:
            self.gpu_watt_val = None
        outer.pack_start(grid, False, False, 4)

        if self.watt_source.path is None and self.gpu_source.backend is None:
            hint = Gtk.Label()
            hint.set_name("nodata-label")
            hint.set_line_wrap(True)
            hint.set_justify(Gtk.Justification.CENTER)
            hint.set_text(
                "No se encontró RAPL, amd_energy, amdgpu ni nvidia-smi.\n"
                "Prueba: sudo modprobe amd_energy"
            )
            outer.pack_start(hint, False, False, 0)

    # ── Historial para la gráfica ────────────────────────────────────────
    def update_graph_data(self):
        """Intenta usar el historial persistido por el daemon (60 min,
        1 muestra/min). Si no hay (daemon apagado), cae al buffer propio
        en vivo (últimos 60s) y lo deja claro en el subtítulo."""
        persisted = load_persisted_history()
        if persisted:
            self.using_persisted_history = True
            self.current_history = persisted
            self.graph_sub.set_text("latest 60 min")
        else:
            self.using_persisted_history = False
            self.current_history = list(self.live_fallback)
            self.graph_sub.set_text(
                "latest 60s (active power-history-daemon.py for watch latest 60 min)"
            )
        self._update_axis_labels()

    def _update_axis_labels(self):
        hist = self.current_history
        if not hist:
            self.axis_start.set_text("—")
            self.axis_mid.set_text("—")
            self.axis_end.set_text("—")
            return

        def fmt(ts):
            return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")

        self.axis_start.set_text(fmt(hist[0]["t"]))
        self.axis_mid.set_text(fmt(hist[len(hist) // 2]["t"]))
        self.axis_end.set_text(fmt(hist[-1]["t"]))

    # ── Actualización periódica de los valores en vivo ──────────────────
    def update_stats(self):
        if self.has_battery:
            snap = read_battery_snapshot(self.bat_path)
            self.last_status = snap["status"]

            self.percent_lbl.set_text(
                f"{snap['capacity']}%" if snap["capacity"] is not None else "—"
            )

            status_map = {
                "Charging": ("Cargando", "charging"),
                "Discharging": ("Descargando", "discharging"),
                "Full": ("Completa", "full"),
                "Not charging": ("En espera", "full"),
            }
            text, css_class = status_map.get(snap["status"], (snap["status"], "discharging"))
            self.status_lbl.set_text(text)
            ctx = self.status_lbl.get_style_context()
            for c in ("charging", "discharging", "full"):
                ctx.remove_class(c)
            ctx.add_class(css_class)

            if snap["watts"] is not None:
                sign = "+" if snap["status"] == "Charging" else "-"
                self.watts_val.set_text(f"{sign}{snap['watts']:.1f} W")
            else:
                self.watts_val.set_text("N/D")

            if snap["time_remaining_h"] is not None:
                h = int(snap["time_remaining_h"])
                m = int((snap["time_remaining_h"] - h) * 60)
                self.time_val.set_text(f"{h} h {m:02d} m")
            else:
                self.time_val.set_text("—" if snap["status"] == "Full" else "N/D")

            self.battery_icon_lbl.set_text(
                battery_icon(snap["capacity"], charging=(snap["status"] == "Charging"))
            )

            self.name_val.set_text(snap["model_name"] or "N/D")

            self.health_val.set_text(
                f"{snap['health']}%" if snap["health"] is not None else "N/D"
            )
            self.cycles_val.set_text(
                str(snap["cycle_count"]) if snap["cycle_count"] is not None else "N/D"
            )

            # Cargador: se muestra si hay CUALQUIER cargador conectado, no
            # solo cuando status=="Charging" (una batería puede estar "Not
            # charging" con el cargador puesto — llena, o con umbral de
            # conservación activo). Si detecta potencia negociada por
            # debajo de RECOMMENDED_CHARGER_W, lo marca como insuficiente.
            cctx = self.charger_val.get_style_context()
            cctx.remove_class("warn")
            if read_ac_online():
                charger = read_charger_info()
                if charger is None:
                    self.charger_val.set_text("Conectado")
                elif charger["watts"] is not None:
                    text = f"{charger['watts']}W ({charger['label']})"
                    if charger["watts"] < RECOMMENDED_CHARGER_W:
                        text += f" \u26a0 <{RECOMMENDED_CHARGER_W}W"
                        cctx.add_class("warn")
                    self.charger_val.set_text(text)
                else:
                    self.charger_val.set_text(f"Conectado ({charger['label']})")
            else:
                self.charger_val.set_text("Desconectado")

            # Perfil de energía (solo existe la fila en modo portátil)
            profile_key = read_power_profile()
            display, css_key = PROFILE_DISPLAY.get(profile_key, (profile_key or "N/D", "unknown"))
            self.profile_lbl.set_text(display)
            pctx = self.profile_lbl.get_style_context()
            for c in ("performance", "balanced", "power-saver", "unknown"):
                pctx.remove_class(c)
            pctx.add_class(css_key)

            live_sample = snap["watts"] if snap["watts"] is not None else 0
        else:
            cpu_w = self.watt_source.sample_watts()
            gpu_w = self.gpu_source.sample_watts() if self.gpu_source.backend else None

            self.cpu_watt_val.set_text(f"{cpu_w:.1f} W" if cpu_w is not None else "N/D")
            if self.gpu_watt_val is not None:
                self.gpu_watt_val.set_text(f"{gpu_w:.1f} W" if gpu_w is not None else "N/D")

            if cpu_w is None and gpu_w is None:
                total = None
            else:
                total = (cpu_w or 0) + (gpu_w or 0)

            if total is not None:
                self.watts_big_lbl.set_text(f"{total:.1f} W")
            live_sample = total if total is not None else 0

        # Solo se usa si el daemon no está corriendo (ver update_graph_data)
        self.live_fallback.append({"t": time.time(), "w": live_sample})

        self._graph_tick_counter += 1
        if self._graph_tick_counter >= GRAPH_POLL_S:
            self._graph_tick_counter = 0
            self.update_graph_data()
        elif not self.using_persisted_history:
            # Sin daemon: refleja el buffer en vivo en cada tick, no solo
            # cada GRAPH_POLL_S, para que se sienta igual de responsivo
            # que antes.
            self.current_history = list(self.live_fallback)
            self._update_axis_labels()

        self.graph.queue_draw()
        return True  # sigue el GLib.timeout

    # ── Gráfica (Cairo) ──────────────────────────────────────────────────
    def on_draw_graph(self, widget, cr):
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height

        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        hist = self.current_history
        if not hist:
            return False

        # Franjas verticales cada cuarto de la ventana (equivalente al
        # separador horario del pantallazo de referencia "12 AM | 12 PM").
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.set_line_width(1)
        for frac in (0.25, 0.5, 0.75):
            x = w * frac
            cr.move_to(x, 0)
            cr.line_to(x, h)
            cr.stroke()

        values = [entry["w"] for entry in hist]
        max_val = max(values) or 1
        n = len(values)
        bar_w = w / max(n, 1)

        charging = self.has_battery and self.last_status == "Charging"
        if charging:
            cr.set_source_rgb(0.620, 0.804, 0.416)   # verde Tokyo Night (#9ece6a)
        else:
            cr.set_source_rgb(0.478, 0.635, 0.969)   # azul Tokyo Night (#7aa2f7)

        for i, val in enumerate(values):
            bar_h = (val / max_val) * (h - 4) if max_val else 0
            x = i * bar_w
            y = h - bar_h
            cr.rectangle(x + 1, y, max(bar_w - 2, 1), bar_h)
        cr.fill()
        return False

    def on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_popup()
        return False

    def on_background_click(self, _widget, event):
        # Popup pequeño sin sub-controles clicables: cualquier clic dentro
        # de la ventana la cierra.
        self.close_popup()
        return True

    def close_popup(self):
        Gtk.main_quit()


def main():
    toggle_or_lock()
    log(f"Starting — HAS_LAYER_SHELL={HAS_LAYER_SHELL}")

    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(CSS.encode("utf-8"))
    except GLib.Error as e:
        log(f"error loading CSS: {e}")

    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_USER,
    )

    win = PowerPopup()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()

    try:
        Gtk.main()
    except Exception:
        log(traceback.format_exc())
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()
