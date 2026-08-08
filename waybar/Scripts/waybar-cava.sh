#!/usr/bin/env bash

# Asegurar que CAVA encuentre el servidor de audio cuando lo ejecuta Waybar
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

bars=("▁" "▂" "▃" "▄" "▅" "▆" "▇" "█")

CONFIG="$(mktemp)"

cat > "$CONFIG" <<EOF
[general]
bars = 12
framerate = 30
autosens = 1
sensitivity = 120
lower_cutoff_freq = 40
higher_cutoff_freq = 16000

[input]
# pipewire | pulse | alsa
method = pipewire
source = auto

[output]
channels = mono
mono_option = average
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 7

[smoothing]
integral = 70
EOF

cleanup() {
    rm -f "$CONFIG"
}

trap cleanup EXIT
# Ignorar SIGPIPE a nivel del shell para que el pipeline no muera
# cuando Waybar deja de leer (reload, reinicio, etc.)
trap '' PIPE

run_cava() {
    # Guardamos los errores en /tmp/cava_error.log por si sigue fallando
    stdbuf -oL cava -p "$CONFIG" 2> /tmp/cava_error.log |
    while IFS= read -r line; do
        output=""

        IFS=';' read -ra vals <<< "$line"

        for v in "${vals[@]}"; do
            [[ "$v" =~ ^[0-7]$ ]] || continue
            output+="${bars[$v]}"
        done

        # Si printf falla (pipe roto hacia Waybar), salir del subshell
        # limpiamente para que el loop externo reinicie cava
        printf '{"text":"%s"}\n' "$output" 2>/dev/null || exit 0
    done
}

# Auto-restart: si cava muere el módulo se recupera solo
while true; do
    # Pequeña pausa inicial para asegurar que el servidor de audio ya arrancó
    sleep 2 
    run_cava
    sleep 1
done