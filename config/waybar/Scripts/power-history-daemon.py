#!/usr/bin/env python3
"""
power-history-daemon.py
──────────────────────────────────────────────────────────────────────────
Sampler de fondo para el historial de la gráfica de power-popup.py.

POR QUÉ EXISTE: power-popup.py es un popup que abre y cierra bajo demanda
(on-click de Waybar) — mientras está cerrado no hay proceso vivo que siga
midiendo vatios. Para que la gráfica muestre una ventana real de los
últimos 60 MINUTOS (con franjas horarias, no solo lo que dure el popup
abierto en pantalla), hace falta un proceso aparte y persistente que
muestree una vez por minuto y vaya guardando un buffer circular en disco.
El popup solo LEE ese archivo al abrirse/redibujar.

Guarda en ~/.cache/power-history.json una lista de hasta 60 entradas
{"t": <epoch_seconds>, "w": <watts>} — 1 hora de historial a 1 muestra/min.

Uso manual (prueba rápida):
  chmod +x power-history-daemon.py
  ./power-history-daemon.py &

Como servicio de usuario systemd (recomendado — arranca solo en login y
se reinicia si falla):

  mkdir -p ~/.config/systemd/user
  # guarda el bloque de abajo como
  # ~/.config/systemd/user/power-history.service
  systemctl --user daemon-reload
  systemctl --user enable --now power-history.service

--- ~/.config/systemd/user/power-history.service -------------------------
[Unit]
Description=Sampler de historial de energia para power-popup

[Service]
Type=simple
ExecStart=%h/.config/waybar/Scripts/power-history-daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
----------------------------------------------------------------------------

Nota: si mueves los scripts a otra ruta, ajusta ExecStart. El daemon
importa power_common.py, así que ese archivo debe vivir en la misma
carpeta que este script.
──────────────────────────────────────────────────────────────────────────
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from power_common import find_battery_path, read_battery_snapshot, WattSource, GpuWattSource

HISTORY_FILE = os.path.expanduser("~/.cache/power-history.json")
MAX_ENTRIES = 60        # 60 muestras
SAMPLE_INTERVAL_S = 60  # 1 muestra por minuto -> 60 min de historial


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (OSError, ValueError):
        pass
    return []


def save_history(history):
    """Escritura atómica (tmp + rename) para que el popup nunca lea un
    JSON a medio escribir si abre justo cuando el daemon está guardando."""
    tmp = HISTORY_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(history, f)
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        pass


def main():
    bat_path = find_battery_path()
    watt_source = WattSource() if bat_path is None else None
    gpu_source = GpuWattSource() if bat_path is None else None
    history = load_history()

    while True:
        if bat_path is not None:
            snap = read_battery_snapshot(bat_path)
            watts = snap["watts"]
            # Se normaliza a positivo para la gráfica; la dirección
            # (carga/descarga) la pinta el popup con el color de la barra.
            watts = abs(watts) if watts is not None else None
        else:
            cpu_w = watt_source.sample_watts()
            if cpu_w is None:
                # La primera lectura de RAPL/amd_energy no trae delta útil
                # todavía (necesita dos muestras); se reintenta pronto.
                time.sleep(2)
                cpu_w = watt_source.sample_watts()
            gpu_w = gpu_source.sample_watts() if gpu_source else None
            # Total del equipo = CPU + GPU dedicada (si hay). Si ninguna
            # de las dos fuentes respondió, se guarda 0 en vez de None
            # para no romper la gráfica.
            if cpu_w is None and gpu_w is None:
                watts = None
            else:
                watts = (cpu_w or 0) + (gpu_w or 0)

        history.append({"t": time.time(), "w": watts if watts is not None else 0})
        history = history[-MAX_ENTRIES:]
        save_history(history)
        time.sleep(SAMPLE_INTERVAL_S)


if __name__ == "__main__":
    main()
