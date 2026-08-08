#!/usr/bin/env python3
"""
calendar-popup.py
──────────────────────────────────────────────────────────────────────────
Popup interactivo de calendario para Waybar (Hyprland + gtk-layer-shell).

Reemplaza el tooltip estático del módulo "clock" (que no acepta clics,
por limitación de GTK) por una ventana capa (layer-shell) con:
  - Reloj digital grande (HH | MM), en la zona horaria de Colombia
  - Fecha en texto, en negrita (weekday, día mes)
  - Navegación de MES independiente (‹ Julio ›)
  - Navegación de AÑO independiente (‹ 2026 ›)
  - Grid de días propio (Gtk.Grid + Gtk.Label), con encabezado de días
    en negrita y espaciado real entre celdas.

NOTA DE DISEÑO: el grid de días NO usa Gtk.Calendar. En GTK3.24+,
Gtk.Calendar dibuja el mes con su propio renderer interno y no expone
nodos CSS por celda — por eso antes las letras de "SunMonTue..." salían
pegadas sin espacio y con un fondo gris que no se podía quitar con CSS.
Construir el grid a mano con Gtk.Grid/Gtk.Label da control total sobre
espaciado, negritas y colores.

Uso:
  chmod +x calendar-popup.py
  ./calendar-popup.py        # abre el popup; si ya está abierto, lo cierra (toggle)

Dependencias (Arch / CachyOS):
  sudo pacman -S python-gobject gtk3
  yay -S gtk-layer-shell          # AUR, no está en repos oficiales

Wiring en Modules.jsonc (módulo "clock"):
  "on-click": "~/.config/waybar/Scripts/calendar-popup.py",

Blur real (frosted glass) — GTK3 no soporta backdrop-filter, así que el
blur lo da el compositor. En hyprland.conf (sintaxis de bloques 0.53+):

  layerrule {
      name = Calendar Popup Layer
      match:namespace = ^(calendar-popup)$
      blur = true
      ignore_alpha = false
  }

Y confirma que el blur esté encendido globalmente:
  decoration { blur { enabled = true } }
──────────────────────────────────────────────────────────────────────────
"""
import os
import sys
import signal
import datetime
import traceback

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

# app_id que ve el compositor (Hyprland) en Wayland. Sin esto, Hyprland no
# puede distinguir esta ventana con un windowrule/layerrule y le aplica
# sus reglas por defecto (borde, sombra, sin blur).
GLib.set_prgname("calendar-popup")

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ImportError, ValueError):
    HAS_LAYER_SHELL = False  # cae a ventana flotante normal si no está instalado

# ── Zona horaria (Colombia = America/Bogota, UTC-5 todo el año, sin DST) ──
# Se fija explícitamente en vez de detectarla, porque es más confiable que
# adivinar ubicación geográfica real desde este contexto. Si alguna vez
# cambias de país, este es el único valor que hay que tocar.
TIMEZONE_NAME = "America/Bogota"
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    TZ = None  # fallback: hora local del sistema si zoneinfo no está disponible


def now_local():
    if TZ is not None:
        return datetime.datetime.now(TZ)
    return datetime.datetime.now()


# ── Festivos de Colombia ────────────────────────────────────────────────
# Cálculo autocontenido (sin dependencias externas ni llamadas a internet):
# la mayoría de los festivos colombianos se rigen por la "Ley Emiliani",
# que traslada varios festivos fijos al lunes siguiente si no caen ya en
# lunes. Los de Semana Santa dependen de la fecha de Pascua, calculada
# con el algoritmo de Gauss/Meeus (calendario gregoriano).

def _easter_sunday(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _next_monday(d):
    """Si 'd' ya es lunes, lo deja igual; si no, avanza al lunes siguiente
    (comportamiento de la Ley Emiliani)."""
    days_ahead = (7 - d.weekday()) % 7  # weekday(): lunes=0
    return d + datetime.timedelta(days=days_ahead)


# Festivos fijos que NO se trasladan de fecha:
_CO_FIXED = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
# Festivos fijos que SÍ se trasladan al lunes siguiente (Ley Emiliani):
_CO_MOVED_TO_MONDAY = [(1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11)]


def colombia_holidays(year):
    """Devuelve un set de datetime.date con los festivos de Colombia
    para el año dado."""
    holidays = set()
    for m, d in _CO_FIXED:
        holidays.add(datetime.date(year, m, d))
    for m, d in _CO_MOVED_TO_MONDAY:
        holidays.add(_next_monday(datetime.date(year, m, d)))

    easter = _easter_sunday(year)
    holidays.add(easter - datetime.timedelta(days=3))   # Jueves Santo
    holidays.add(easter - datetime.timedelta(days=2))   # Viernes Santo
    holidays.add(_next_monday(easter + datetime.timedelta(days=39)))  # Ascensión
    holidays.add(_next_monday(easter + datetime.timedelta(days=60)))  # Corpus Christi
    holidays.add(_next_monday(easter + datetime.timedelta(days=68)))  # Sagrado Corazón
    return holidays


PIDFILE = "/tmp/waybar-calendar-popup.pid"
LOGFILE = os.path.expanduser("~/.cache/waybar-calendar-popup.log")

WEEKDAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def log(msg):
    """Log simple a archivo, para diagnosticar cuando el script corre
    disparado por Waybar (sin terminal visible)."""
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        with open(LOGFILE, "a") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except OSError:
        pass


# ── CSS embebido (Tokyo Night, coherente con style.css de Waybar) ─────────
CSS = """
window#calendar-popup {
    background-color: transparent;
    border: none;
    box-shadow: none;
}

#main-container {
    background-color: rgba(8, 9, 18, 0.80);
    border-radius: 24px; 
    border: 1px solid rgba(255,255,255,0.12);
    margin: 8px;     
    padding: 22px 28px;
}
* {
    font-family: "JetBrainsMono Nerd Font", "JetBrainsMono Nerd Font Mono", monospace;
}
label { color: #f1f1f1; }

#time-label { font-size: 52px; font-weight: bold; color: #f1f1f1; }
#time-sep   { font-size: 44px; font-weight: bold; color: #7aa2f7; }
#date-label { font-size: 14px; font-weight: bold; color: #a9b1d6; }
#ampm-label { font-size: 10px; color: #a9b1d6; }
#warn-label { font-size: 10px; color: #e0af68; }

#nav-label-month { font-size: 15px; font-weight: bold; color: #9ece6a; }
#nav-label-year  { font-size: 14px; font-weight: bold; color: #7aa2f7; }

button.nav-btn {
    background: transparent;
    border: none;
    box-shadow: none;
    color: #7aa2f7;
    min-width: 18px;
    min-height: 18px;
    padding: 0px 4px;
    transition: color 0.15s ease;
}
button.nav-btn:hover { color: #ffffff; }

/* Grid de dias propio (reemplaza a Gtk.Calendar) */
#day-header {
    font-size: 13px;
    font-weight: bold;
    color: #9ece6a;
}
#day-cell {
    font-size: 14px;
    color: #f1f1f1;
}
#day-cell.other-month { color: #3d4465; }
#day-cell.today {
    color: #0b1020;
    font-weight: bold;
    background-color: #7aa2f7;
    border-radius: 8px;
}
#day-cell.holiday {
    color: #9b2242;
    font-weight: bold;
}
#day-cell.today.holiday {
    /* Si un festivo cae hoy: se conserva el fondo azul de "hoy" y el
       texto en vinotinto para que ambas señales sigan siendo visibles. */
    color: #9b2242;
    background-color: #7aa2f7;
}
""".encode("utf-8")


def toggle_or_lock():
    """Si ya hay un popup abierto, lo cierra (toggle) y sale del proceso
    actual. Si no, escribe el pidfile propio."""
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


class CalendarPopup(Gtk.Window):
    def __init__(self):
        super().__init__(title="calendar-popup")
        self.set_name("calendar-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_role("calendar-popup")

        # Estado propio del mes/año mostrado (ya no depende de Gtk.Calendar).
        today = now_local()
        self.cur_year = today.year
        self.cur_month = today.month  # 1-indexed

        # Visual RGBA: sin esto, el background-color con alpha y el
        # border-radius del CSS no se pintan y GTK cae al blanco opaco
        # del tema del sistema.
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)

        if HAS_LAYER_SHELL:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, "calendar-popup")
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 10)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 8)
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.ON_DEMAND
            )
        else:
            # Fallback: ventana flotante normal (útil para probar en X11/otro WM)
            self.set_position(Gtk.WindowPosition.CENTER)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_name("main-container") # Añade esta línea
        outer.set_border_width(16)
        self.add(outer)

        if not HAS_LAYER_SHELL:
            warn = Gtk.Label()
            warn.set_name("warn-label")
            warn.set_line_wrap(True)
            warn.set_justify(Gtk.Justification.CENTER)
            warn.set_text(
                "⚠ gtk-layer-shell no encontrado.\n"
                "Instálalo (AUR): yay -S gtk-layer-shell\n"
                "Mientras tanto se ve sin estilo/posición correctos."
            )
            outer.pack_start(warn, False, False, 0)

        # ── Reloj digital ───────────────────────────────────────────
        time_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            spacing=8,
        )
        self.hour_lbl = Gtk.Label()
        self.hour_lbl.set_name("time-label")
        sep_lbl = Gtk.Label(label="|")
        sep_lbl.set_name("time-sep")
        self.min_lbl = Gtk.Label()
        self.min_lbl.set_name("time-label")
        time_box.pack_start(self.hour_lbl, False, False, 0)
        time_box.pack_start(sep_lbl, False, False, 0)
        time_box.pack_start(self.min_lbl, False, False, 0)
        outer.pack_start(time_box, False, False, 0)

        self.ampm_lbl = Gtk.Label()
        self.ampm_lbl.set_name("ampm-label")
        self.ampm_lbl.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self.ampm_lbl, False, False, 0)

        self.date_lbl = Gtk.Label()
        self.date_lbl.set_name("date-label")
        self.date_lbl.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self.date_lbl, False, False, 0)

        outer.pack_start(Gtk.Separator(), False, False, 6)

        # ── Navegación de MES (independiente) ───────────────────────
        self.month_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            spacing=10,
        )
        self.month_row.pack_start(self._nav_button("\u2039", self.on_prev_month), False, False, 0)
        self.month_lbl = Gtk.Label()
        self.month_lbl.set_name("nav-label-month")
        self.month_row.pack_start(self.month_lbl, False, False, 0)
        self.month_row.pack_start(self._nav_button("\u203a", self.on_next_month), False, False, 0)
        outer.pack_start(self.month_row, False, False, 0)

        # ── Navegación de AÑO (independiente) ───────────────────────
        self.year_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            spacing=10,
        )
        self.year_row.pack_start(self._nav_button("\u2039", self.on_prev_year), False, False, 0)
        self.year_lbl = Gtk.Label()
        self.year_lbl.set_name("nav-label-year")
        self.year_row.pack_start(self.year_lbl, False, False, 0)
        self.year_row.pack_start(self._nav_button("\u203a", self.on_next_year), False, False, 0)
        outer.pack_start(self.year_row, False, False, 0)

        # ── Grid de días (propio, no Gtk.Calendar) ───────────────────
        self.grid_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(self.grid_container, False, False, 4)


        self.sync_nav_labels()
        self.rebuild_grid()
        self.update_clock()
        GLib.timeout_add_seconds(1, self.update_clock)

        self.connect("key-press-event", self.on_key_press)
        self.connect("button-press-event", self.on_background_click)
        # NOTA: se quitó el cierre por "focus-out-event". Con Hyprland en
        # focus-follows-mouse, mover el cursor a otra ventana le quita el
        # foco de teclado a este popup y lo cerraba solo. El cierre ahora
        # es explícito: click de nuevo en el reloj (toggle), tecla Esc, o
        # click en el borde/área vacía del popup (ver on_background_click).

    def _nav_button(self, glyph, callback):
        btn = Gtk.Button(label=glyph)
        btn.get_style_context().add_class("nav-btn")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.connect("clicked", callback)
        return btn

    def sync_nav_labels(self):
        month_name = datetime.date(self.cur_year, self.cur_month, 1).strftime("%B")
        self.month_lbl.set_text(month_name.capitalize())
        self.year_lbl.set_text(str(self.cur_year))

    # -- Navegación de mes: cambia mes, hace rollover de año automático --
    def on_prev_month(self, *_):
        self.cur_month -= 1
        if self.cur_month < 1:
            self.cur_month, self.cur_year = 12, self.cur_year - 1
        self._refresh()

    def on_next_month(self, *_):
        self.cur_month += 1
        if self.cur_month > 12:
            self.cur_month, self.cur_year = 1, self.cur_year + 1
        self._refresh()

    # -- Navegación de año: mueve solo el año, mismo mes -----------------
    def on_prev_year(self, *_):
        self.cur_year -= 1
        self._refresh()

    def on_next_year(self, *_):
        self.cur_year += 1
        self._refresh()

    def _refresh(self):
        self.sync_nav_labels()
        self.rebuild_grid()

    def _build_weeks(self):
        """Calcula la matriz de semanas (listas de 7 días) para
        self.cur_year/self.cur_month, incluyendo días grises del mes
        anterior/siguiente para rellenar la primera y última fila,
        igual que en la captura de referencia."""
        import calendar as _cal  # alias local, no colisiona con el nombre del archivo

        first_weekday, days_in_month = _cal.monthrange(self.cur_year, self.cur_month)
        # monthrange: 0=lunes..6=domingo. Queremos empezar en domingo (0=domingo).
        start_offset = (first_weekday + 1) % 7

        prev_month = 12 if self.cur_month == 1 else self.cur_month - 1
        prev_year = self.cur_year - 1 if self.cur_month == 1 else self.cur_year
        days_in_prev = _cal.monthrange(prev_year, prev_month)[1]

        cells = []
        for i in range(start_offset):
            day_num = days_in_prev - start_offset + i + 1
            cells.append((day_num, False))  # (día, es_mes_actual)
        for d in range(1, days_in_month + 1):
            cells.append((d, True))
        while len(cells) % 7 != 0:
            next_day_index = len(cells) - (start_offset + days_in_month)
            cells.append((next_day_index + 1, False))

        return [cells[i:i + 7] for i in range(0, len(cells), 7)]

    def rebuild_grid(self):
        # Limpia el grid anterior
        for child in self.grid_container.get_children():
            self.grid_container.remove(child)

        grid = Gtk.Grid()
        grid.set_row_spacing(6)     # ← espaciado real entre filas
        grid.set_column_spacing(10)  # ← espaciado real entre columnas
        grid.set_halign(Gtk.Align.CENTER)

        for col, name in enumerate(WEEKDAY_ABBR):
            lbl = Gtk.Label(label=name)
            lbl.set_name("day-header")
            grid.attach(lbl, col, 0, 1, 1)

        today = now_local()
        weeks = self._build_weeks()
        holidays = colombia_holidays(self.cur_year)
        for row, week in enumerate(weeks, start=1):
            for col, (day_num, in_month) in enumerate(week):
                lbl = Gtk.Label(label=str(day_num))
                lbl.set_name("day-cell")
                lbl.set_size_request(24, 24)
                ctx = lbl.get_style_context()
                if not in_month:
                    ctx.add_class("other-month")
                else:
                    if (
                        day_num == today.day
                        and self.cur_month == today.month
                        and self.cur_year == today.year
                    ):
                        ctx.add_class("today")
                    this_date = datetime.date(self.cur_year, self.cur_month, day_num)
                    is_sunday = this_date.weekday() == 6  # lunes=0 ... domingo=6
                    if this_date in holidays or is_sunday:
                        ctx.add_class("holiday")
                grid.attach(lbl, col, row, 1, 1)

        self.grid_container.pack_start(grid, False, False, 0)
        self.grid_container.show_all()

    def update_clock(self):
        now = now_local()
        self.hour_lbl.set_text(now.strftime("%I"))
        self.min_lbl.set_text(now.strftime("%M"))
        # AM/PM calculado a mano (no via %p) para evitar variantes de
        # locale como "a. m." / "p. m." en sistemas configurados en español.
        self.ampm_lbl.set_text("AM" if now.hour < 12 else "PM")
        weekday = now.strftime("%A").capitalize()
        day = now.strftime("%d").lstrip("0") or "0"
        month = now.strftime("%B").capitalize()
        self.date_lbl.set_text(f"\U000F0E17 {weekday}, {day} {month}")
        return True  # sigue el GLib.timeout

    def on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_popup()
        return False

    def on_background_click(self, _widget, event):
        """Cierra el popup si el clic cae fuera de las zonas protegidas
        (grid de días completo, fila de navegación de mes, fila de
        navegación de año). Los botones ‹ › ya no disparan este handler
        de por sí (tienen su propia ventana de entrada GTK), pero se
        incluyen igual en el hit-test por si el clic cae sobre su label
        o el espacio entre botones."""
        protected_widgets = [self.grid_container, self.month_row, self.year_row]
        for w in protected_widgets:
            coords = w.translate_coordinates(self, 0, 0)
            if coords is None:
                continue
            wx, wy = coords
            alloc = w.get_allocation()
            if wx <= event.x <= wx + alloc.width and wy <= event.y <= wy + alloc.height:
                return False  # dentro de zona protegida: no cerrar
        self.close_popup()
        return True

    def close_popup(self):
        Gtk.main_quit()


def main():
    toggle_or_lock()
    log(f"iniciando — HAS_LAYER_SHELL={HAS_LAYER_SHELL} TZ={TIMEZONE_NAME}")

    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(CSS)
    except GLib.Error as e:
        log(f"error cargando CSS: {e}")

    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_USER,
    )

    win = CalendarPopup()
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
    