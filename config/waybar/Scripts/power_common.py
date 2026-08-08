#!/usr/bin/env python3
"""
power_common.py
──────────────────────────────────────────────────────────────────────────
Lógica compartida de lectura de energía, usada por power-popup.py (UI) y
power-history-daemon.py (sampler de fondo). Vive separada para que ambos
procesos lean exactamente de la misma fuente y no se dupliquen bugs.

No importa Gtk — este módulo es puro sysfs/subprocess, así el daemon (que
corre sin display) no necesita nada de GTK instalado.
──────────────────────────────────────────────────────────────────────────
"""
import os
import glob
import time
import subprocess

# Potencia (W) del cargador de fábrica de TU equipo. Se usa solo para
# avisar si detecta uno de menor potencia conectado (carga más lenta,
# o directamente no carga bajo uso intenso de CPU). Ajusta este valor
# si cambias de equipo — el X1 Carbon Gen 8 trae un cargador de 65W.
RECOMMENDED_CHARGER_W = 65


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_str(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


# ── Batería (modo portátil) ───────────────────────────────────────────────

def find_battery_path():
    candidates = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    return candidates[0] if candidates else None


def read_battery_snapshot(bat_path):
    """Snapshot completo: estado, %, salud, vatios, tiempo restante,
    ciclos de carga y nombre/modelo de la batería."""
    status = _read_str(f"{bat_path}/status") or "Unknown"
    capacity = _read_int(f"{bat_path}/capacity")
    voltage_now = _read_int(f"{bat_path}/voltage_now")

    energy_now = _read_int(f"{bat_path}/energy_now")
    energy_full = _read_int(f"{bat_path}/energy_full")
    energy_full_design = _read_int(f"{bat_path}/energy_full_design")
    power_now = _read_int(f"{bat_path}/power_now")

    if energy_now is None or energy_full is None or energy_full_design is None:
        # Fallback para baterías que solo exponen charge_* (µAh) en vez de
        # energy_* (µWh) — se estima Wh multiplicando por el voltaje actual.
        charge_now = _read_int(f"{bat_path}/charge_now")
        charge_full = _read_int(f"{bat_path}/charge_full")
        charge_full_design = _read_int(f"{bat_path}/charge_full_design")
        v = voltage_now or 0
        if charge_now is not None:
            energy_now = charge_now * v // 1_000_000
        if charge_full is not None:
            energy_full = charge_full * v // 1_000_000
        if charge_full_design is not None:
            energy_full_design = charge_full_design * v // 1_000_000

    if power_now is None:
        current_now = _read_int(f"{bat_path}/current_now")
        if current_now is not None and voltage_now is not None:
            power_now = (abs(current_now) * voltage_now) // 1_000_000

    health = None
    if energy_full and energy_full_design:
        health = round((energy_full / energy_full_design) * 100, 1)

    time_remaining_h = None
    if power_now and power_now > 0:
        if status == "Discharging" and energy_now is not None:
            time_remaining_h = energy_now / power_now
        elif status == "Charging" and energy_now is not None and energy_full is not None:
            time_remaining_h = max(energy_full - energy_now, 0) / power_now

    watts = (power_now / 1_000_000) if power_now is not None else None

    cycle_count = _read_int(f"{bat_path}/cycle_count")
    model_name = _read_str(f"{bat_path}/model_name")
    manufacturer = _read_str(f"{bat_path}/manufacturer")
    if model_name and manufacturer:
        display_name = f"{manufacturer} {model_name}"
    else:
        display_name = model_name or manufacturer or os.path.basename(bat_path)

    return {
        "status": status,
        "capacity": capacity,
        "health": health,
        "watts": watts,
        "time_remaining_h": time_remaining_h,
        "cycle_count": cycle_count,
        "model_name": display_name,
    }


def read_charger_info():
    """Busca un suministro Mains/USB conectado (sin importar si la
    batería está activamente cargando — ver read_ac_online) y estima su
    potencia nominal. Prioriza rutas 'ucsi-*' (negociación USB-C PD
    real) sobre el genérico 'AC'/'ADP1'.

    Devuelve dict {"label": str, "watts": int|None} o None si no hay
    ningún cargador conectado."""
    supplies = sorted(glob.glob("/sys/class/power_supply/*"))
    ucsi = [s for s in supplies if "ucsi" in os.path.basename(s).lower()]
    generic = [s for s in supplies if s not in ucsi]

    for group in (ucsi, generic):
        for s in group:
            stype = _read_str(f"{s}/type")
            if stype not in ("Mains", "USB", "USB_PD", "USB_PD_DRP"):
                continue
            online = _read_int(f"{s}/online")
            if online != 1:
                continue
            v = _read_int(f"{s}/voltage_now") or _read_int(f"{s}/voltage_max_design")
            c = _read_int(f"{s}/current_max") or _read_int(f"{s}/current_now")
            watts = round((v / 1_000_000) * (c / 1_000_000)) if (v and c) else None
            return {"label": os.path.basename(s), "watts": watts}
    return None


def read_ac_online():
    """True si HAY un cargador físicamente conectado, sin importar si
    la batería está activamente cargando o no. Necesario porque
    'status' puede ser 'Not charging' con el cargador puesto (umbral de
    conservación de carga del BIOS/EC alcanzado, batería llena, etc.) —
    ese caso antes no mostraba nada de info del cargador."""
    for s in glob.glob("/sys/class/power_supply/*"):
        stype = _read_str(f"{s}/type")
        if stype in ("Mains", "USB", "USB_PD", "USB_PD_DRP"):
            if _read_int(f"{s}/online") == 1:
                return True
    return False


# ── Icono de batería (Nerd Font / Material Design Icons) ──────────────────
# Codepoints verificados decodificando los glifos de ejemplo que diste
# (󰁺 = U+F007A = battery-10, 󰂂 = U+F0082 = battery-90,
#  󰂋 = U+F008B = battery-charging-70, 󰃃 = U+F0083 = battery-alert).
# A partir de esos 4 puntos confirmados se deriva el resto de la serie,
# que en Material Design Icons es secuencial de a 1 codepoint por cada
# 10% de nivel.
_BATTERY_DISCHARGE_BASE = 0xF0079   # "battery" (100%, sin cargar)
_BATTERY_ALERT = 0xF0083            # "battery-alert" (crítica)
_BATTERY_CHARGE_BASE = 0xF0086      # "battery-charging-20" (base de la serie 20..90)
_BATTERY_CHARGE_100 = 0xF0085       # "battery-charging-100" (fuera de secuencia en MDI)
_BATTERY_CHARGE_10 = 0xF089C        # "battery-charging-10" (codepoint aparte en MDI)


def battery_icon(capacity, charging):
    """Elige el glifo de batería según % y si está cargando. Si tu Nerd
    Font es distinta a Material Design Icons (JetBrainsMono Nerd Font sí
    la incluye), verifica con `nerd-fonts-cheat-sheet` que estos
    codepoints coincidan; el único no confirmado a mano es
    battery-charging-10 (0xF089C)."""
    cap = 50 if capacity is None else max(0, min(100, capacity))
    level = int(round(cap / 10.0)) * 10
    level = max(0, min(100, level))

    if charging:
        if level <= 10:
            return chr(_BATTERY_CHARGE_10)
        if level >= 100:
            return chr(_BATTERY_CHARGE_100)
        return chr(_BATTERY_CHARGE_BASE + (level - 20) // 10)

    if cap <= 15:
        return chr(_BATTERY_ALERT)
    if level >= 100:
        return chr(_BATTERY_DISCHARGE_BASE)
    return chr(_BATTERY_DISCHARGE_BASE + level // 10)


# ── Perfil de energía (power-profiles-daemon) — solo aplica a portátiles ──

PROFILE_DISPLAY = {
    "power-saver": ("Ahorro de energía", "power-saver"),
    "balanced": ("Equilibrado", "balanced"),
    "performance": ("Rendimiento", "performance"),
}


def read_power_profile():
    """Perfil activo vía powerprofilesctl (power-profiles-daemon), con
    fallback a platform_profile de ACPI si el daemon no está corriendo.
    Devuelve la clave cruda ('performance', 'balanced', 'power-saver')
    o None si no hay ninguna fuente disponible."""
    try:
        out = subprocess.run(
            ["powerprofilesctl", "get"],
            capture_output=True, text=True, timeout=1,
        )
        if out.returncode == 0:
            val = out.stdout.strip()
            if val:
                return val
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return _read_str("/sys/firmware/acpi/platform_profile")


# ── Vatios para modo escritorio (sin batería): Intel RAPL o AMD amd_energy ─

class WattSource:
    """Calcula vatios instantáneos por diferencia entre dos lecturas de
    energía acumulada (µJ). Sirve tanto para RAPL como amd_energy, que
    reportan en el mismo formato."""

    def __init__(self):
        self.path = None
        self.label = "N/D"
        self._find_source()
        self._last_uj = None
        self._last_t = None

    def _find_source(self):
        rapl = "/sys/class/powercap/intel-rapl:0/energy_uj"
        if os.path.exists(rapl) and _read_int(rapl) is not None:
            self.path = rapl
            self.label = "CPU Package (RAPL)"
            return

        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read_str(f"{hwmon}/name")
            if name == "amd_energy":
                inputs = sorted(glob.glob(f"{hwmon}/energy*_input"))
                if inputs and _read_int(inputs[0]) is not None:
                    self.path = inputs[0]
                    self.label = "CPU Package (amd_energy)"
                    return

        self.path = None
        self.label = "N/D (sin RAPL/amd_energy)"

    def sample_watts(self):
        if self.path is None:
            return None
        uj = _read_int(self.path)
        if uj is None:
            return None
        now = time.monotonic()
        watts = None
        if self._last_uj is not None and self._last_t is not None:
            dt = now - self._last_t
            duj = uj - self._last_uj
            if duj < 0:
                duj = None  # wraparound del contador de energía
            if dt > 0 and duj is not None:
                watts = duj / 1_000_000 / dt
        self._last_uj = uj
        self._last_t = now
        return watts


class GpuWattSource:
    """Detecta una GPU DEDICADA (AMD vía hwmon 'amdgpu', o NVIDIA vía
    nvidia-smi) y reporta vatios instantáneos.

    Nota importante para el 5700G: es un APU con iGPU Vega integrada.
    Esa iGPU normalmente YA está incluida en la lectura de amd_energy
    del paquete completo (WattSource), así que esta clase solo aporta
    un número adicional cuando hay una tarjeta dedicada de verdad
    instalada aparte del APU — si no hay ninguna, self.backend queda en
    None y sample_watts() siempre devuelve None (no falla, no suma
    nada de más)."""

    def __init__(self):
        self.backend = None   # "nvidia" | "amdgpu" | None
        self.path = None
        self.label = "Sin GPU dedicada"
        self._find_source()

    def _find_source(self):
        # 1. NVIDIA — requiere el driver propietario + nvidia-smi en PATH.
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=1,
            )
            if out.returncode == 0 and out.stdout.strip():
                self.backend = "nvidia"
                self.label = out.stdout.strip().splitlines()[0]
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        # 2. AMD dGPU vía driver amdgpu (hwmon con name=="amdgpu"). Es
        # distinto del hwmon "amd_energy" que usa WattSource para el
        # paquete CPU — aquí buscamos específicamente la tarjeta.
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read_str(f"{hwmon}/name")
            if name == "amdgpu":
                for fname in ("power1_average", "power1_input"):
                    p = f"{hwmon}/{fname}"
                    if os.path.exists(p) and _read_int(p) is not None:
                        self.backend = "amdgpu"
                        self.path = p
                        self.label = "AMD Radeon (amdgpu)"
                        return

        self.backend = None

    def sample_watts(self):
        if self.backend == "nvidia":
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=power.draw",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=1,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return float(out.stdout.strip().splitlines()[0])
            except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
                return None
            return None

        if self.backend == "amdgpu" and self.path:
            # power1_average/power1_input ya vienen promediados en µW —
            # a diferencia de RAPL/amd_energy no hace falta delta entre
            # dos muestras, es lectura directa.
            uw = _read_int(self.path)
            return (uw / 1_000_000) if uw is not None else None

        return None
