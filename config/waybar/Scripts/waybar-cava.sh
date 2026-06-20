#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  waybar-cava.sh  —  spectrum analyzer para waybar
#  Instalar en: ~/.config/waybar/Scripts/waybar-cava.sh
#  Permisos:    chmod +x ~/.config/waybar/Scripts/waybar-cava.sh
#
#  En Modules usar:
#    "custom/cava": {
#        "exec": "$HOME/.config/waybar/Scripts/waybar-cava.sh",
#        "tail": true,
#        "format": "{}",
#        "restart-interval": 3,
#        "tooltip": false
#    }
#
#  NOTA: usar $HOME (no ~) en el campo "exec" de waybar.
# ══════════════════════════════════════════════════════════════

# ── Config temporal embebida ──────────────────────────────────
# Se crea en /tmp y se elimina al salir (trap EXIT).
# Toma todos los parámetros de tu config standalone excepto:
#   · xaxis = frequency  → eliminado (añade texto, rompe waybar)
#   · bar_delimiter = 0  → forzado   (barras sin separador)
#   · framerate = 60     → reducido  (100 fps en waybar es excesivo)
#   · sleep_timer = 5    → activado  (suspende en silencio)

TMPCONF=$(mktemp /tmp/cava-waybar-XXXXXX.ini)
trap 'rm -f "$TMPCONF"' EXIT

cat > "$TMPCONF" << 'EOF'
[general]
mode              = waves
framerate         = 60
autosens          = 1
# Reducimos la sensibilidad de 85 a 30
sensitivity       = 30 
bars              = 12
bar_height_log    = 1
lower_cutoff_freq = 30
higher_cutoff_freq = 20000
sleep_timer       = 5

[input]
method = pipewire
source = auto

[output]
method        = noncurses
orientation   = bottom
channels      = mono
mono_option   = average
bar_delimiter = 0

[smoothing]
integral   = 82
gravity    = 65
monstercat = 0
waves      = 1
noise_reduction = 0.77

[eq]
 1  = 1.65
 2  = 1.44
 3  = 1.05
 4  = 0.84
 5  = 0.65
EOF

# ── Esperar a PipeWire ────────────────────────────────────────
# El X1 Carbon Gen 8 arranca waybar antes que el servidor de
# audio. Sin este wait, cava falla silenciosamente en cold boot.

for i in $(seq 1 20); do
    pactl info &>/dev/null && break
    sleep 0.5
done

# ── Lanzar cava ──────────────────────────────────────────────
exec cava -p "$TMPCONF"