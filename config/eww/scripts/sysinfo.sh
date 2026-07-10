#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  sysinfo.sh — deflisten source para el sidebar de eww
# ══════════════════════════════════════════════════════════
# Imprime UN objeto JSON por línea, cada 2s, con todo lo que
# las magic vars de eww (EWW_CPU, EWW_RAM, EWW_DISK...) no
# traen: modelo de CPU, temperatura, uptime, red e iGPU.
#
# Un solo script para todo esto -> menos archivos sueltos.

set -o pipefail

# --- Datos estáticos: se calculan una sola vez, fuera del loop ---
SYSNAME=$(uname -s)
KERNEL=$(uname -r)
MACHINE=$(uname -m)
CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo | sed -E 's/model name\s*:\s*//')

# Interfaz de red activa (la de la ruta por defecto)
get_iface() {
    ip route get 1.1.1.1 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}'
}

# Lee bytes rx/tx de una interfaz (0 si no existe)
read_bytes() {
    local iface="$1" dir="$2"
    cat "/sys/class/net/${iface}/statistics/${dir}_bytes" 2>/dev/null || echo 0
}

# Formatea bytes/seg a algo legible (KB/s o MB/s)
human_speed() {
    local bytes="$1"
    awk -v b="$bytes" 'BEGIN {
        if (b >= 1048576) printf "%.1f MB/s", b/1048576;
        else printf "%.1f KB/s", b/1024;
    }'
}

IFACE=$(get_iface)
[ -z "$IFACE" ] && IFACE="lo"
PREV_RX=$(read_bytes "$IFACE" rx)
PREV_TX=$(read_bytes "$IFACE" tx)

# --- Localizar la tarjeta i915 y sus rutas de sysfs (una sola vez) ---
# El kernel expone dos ABIs según la versión: la vieja en la raíz de
# card*/ y la nueva bajo card*/gt/gt0/. Probamos ambas y nos quedamos
# con la primera que exista.
GPU_CARD=""
for c in /sys/class/drm/card*/; do
    [ -f "${c}gt_cur_freq_mhz" ] || [ -f "${c}gt/gt0/rps_cur_freq_mhz" ] || continue
    GPU_CARD="${c%/}"
    break
done

if [ -n "$GPU_CARD" ]; then
    if [ -f "$GPU_CARD/gt_cur_freq_mhz" ]; then
        GPU_FREQ_PATH="$GPU_CARD/gt_cur_freq_mhz"
    else
        GPU_FREQ_PATH="$GPU_CARD/gt/gt0/rps_cur_freq_mhz"
    fi
    if [ -f "$GPU_CARD/power/rc6_residency_ms" ]; then
        GPU_RC6_PATH="$GPU_CARD/power/rc6_residency_ms"
    else
        GPU_RC6_PATH="$GPU_CARD/gt/gt0/rc6_residency_ms"
    fi
fi

# i915 NO expone "gpu_busy_percent" (eso es exclusivo de amdgpu). El
# porcentaje de uso real se calcula comparando cuánto tiempo pasó la
# GPU en rc6 (idle) contra el tiempo total transcurrido.
PREV_RC6=0
PREV_RC6_TS=0
if [ -n "$GPU_RC6_PATH" ] && [ -f "$GPU_RC6_PATH" ]; then
    PREV_RC6=$(cat "$GPU_RC6_PATH" 2>/dev/null || echo 0)
    PREV_RC6_TS=$(date +%s%3N)
fi

# --- iGPU Intel: % de uso vía delta de rc6_residency_ms ---
# rc6_residency_ms = tiempo acumulado que la GPU pasó en estado idle (RC6).
# Uso% = 100 - (delta_rc6 / delta_tiempo_real) * 100
RC6_PATH=$(find /sys/class/drm/card0 -maxdepth 3 -iname "rc6_residency_ms" 2>/dev/null | head -1)

if [[ -n "$RC6_PATH" && -r "$RC6_PATH" ]]; then
    now_ms=$(date +%s%3N)
    rc6_now=$(cat "$RC6_PATH")
    # ... resto del bloque delta igual que antes
else
    gpu_usage="null"
fi

IFACE=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev") print $(i+1)}')

if [[ -z "$IFACE" ]]; then
    net_type="disconnected"
elif [[ "$IFACE" == wl* ]]; then
    net_type="wifi"
elif [[ "$IFACE" == en* || "$IFACE" == eth* ]]; then
    net_type="ethernet"
else
    net_type="unknown"
fi

while true; do
    sleep 2

    UPTIME=$(uptime -p 2>/dev/null | sed 's/^up //')

    # Temperatura: intenta coretemp (Intel) y cae a lo primero que encuentre `sensors`
    CPU_TEMP=$(sensors 2>/dev/null | awk '
        /Package id 0:|Tctl:|Tdie:/ { gsub(/[+°C]/,"",$4); print $4; exit }
    ')
    [ -z "$CPU_TEMP" ] && CPU_TEMP="N/A"

    # Wifi / cable
    ESSID=$(iwgetid -r 2>/dev/null)
    if [ -n "$ESSID" ]; then
        NET_LABEL="  ${ESSID}"
    else
        NET_LABEL="󰈀  Cable"
    fi

    # Velocidad de red (delta de bytes / 2s)
    CUR_RX=$(read_bytes "$IFACE" rx)
    CUR_TX=$(read_bytes "$IFACE" tx)
    DOWN_BPS=$(( (CUR_RX - PREV_RX) / 2 ))
    UP_BPS=$(( (CUR_TX - PREV_TX) / 2 ))
    PREV_RX=$CUR_RX
    PREV_TX=$CUR_TX
    NET_DOWN=$(human_speed "$DOWN_BPS")
    NET_UP=$(human_speed "$UP_BPS")

    # iGPU Intel (i915) vía sysfs, sin dependencias extra
    if [ -n "$GPU_FREQ_PATH" ] && [ -f "$GPU_FREQ_PATH" ]; then
        GPU_FREQ="$(cat "$GPU_FREQ_PATH") MHz"
    else
        GPU_FREQ="N/A"
    fi

    if [ -n "$GPU_RC6_PATH" ] && [ -f "$GPU_RC6_PATH" ]; then
        CUR_RC6=$(cat "$GPU_RC6_PATH")
        CUR_RC6_TS=$(date +%s%3N)
        DELTA_RC6=$(( CUR_RC6 - PREV_RC6 ))
        DELTA_TS=$(( CUR_RC6_TS - PREV_RC6_TS ))
        if [ "$DELTA_TS" -gt 0 ]; then
            GPU_USAGE=$(awk -v rc6="$DELTA_RC6" -v total="$DELTA_TS" 'BEGIN {
                pct = 100 - (rc6 * 100 / total);
                if (pct < 0) pct = 0;
                if (pct > 100) pct = 100;
                printf "%.0f", pct;
            }')
        else
            GPU_USAGE="0"
        fi
        PREV_RC6=$CUR_RC6
        PREV_RC6_TS=$CUR_RC6_TS
    else
        GPU_USAGE="0"
    fi

    # jq -Rn arma el JSON escapando todo correctamente (evita romper
    # el parseo si algún campo trae comillas o caracteres raros)
    jq -Rn -c --arg sysname "$SYSNAME" \
           --arg kernel "$KERNEL" \
           --arg machine "$MACHINE" \
           --arg cpu_model "$CPU_MODEL" \
           --arg cpu_temp "$CPU_TEMP" \
           --arg uptime "$UPTIME" \
           --arg iface "$IFACE" \
           --arg net_label "$NET_LABEL" \
           --arg net_up "$NET_UP" \
           --arg net_down "$NET_DOWN" \
           --arg gpu_freq "$GPU_FREQ" \
           --arg gpu_usage "$GPU_USAGE" \
           '{sysname:$sysname, kernel:$kernel, machine:$machine, cpu_model:$cpu_model,
             cpu_temp:$cpu_temp, uptime:$uptime, iface:$iface, net_label:$net_label,
             net_up:$net_up, net_down:$net_down, gpu_freq:$gpu_freq,
             gpu_usage:$gpu_usage}'
done
