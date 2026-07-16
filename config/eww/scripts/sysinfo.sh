#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  sysinfo.sh — deflisten source para el sidebar de eww
# ══════════════════════════════════════════════════════════

set -o pipefail

# --- Datos estáticos del Sistema ---
SYSNAME=$(uname -s)
KERNEL=$(uname -r)
MACHINE=$(uname -m)
CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo | sed -E 's/model name\s*:\s*//')
DISTRO=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")
[ -z "$DISTRO" ] && DISTRO="$SYSNAME"

CPU_MODEL_SHORT=$(echo "$CPU_MODEL" | sed -E \
    -e 's/\(R\)//g' -e 's/\(TM\)//g' -e 's/ CPU\b//' \
    -e 's/@ *[0-9.]+GHz//' -e 's/ +/ /g' -e 's/^ +| +$//g')
[ -z "$CPU_MODEL_SHORT" ] && CPU_MODEL_SHORT="N/A"

# --- Detección del Tipo de Kernel Linux ---
KERNEL_TYPE="Vanilla"
if [[ "$KERNEL" =~ -lts ]]; then
    KERNEL_TYPE="LTS"
elif [[ "$KERNEL" =~ -zen ]]; then
    KERNEL_TYPE="Zen"
elif [[ "$KERNEL" =~ -hardened ]]; then
    KERNEL_TYPE="Hardened"
elif [[ "$KERNEL" =~ -rt ]]; then
    KERNEL_TYPE="Real-Time"
elif [[ "$KERNEL" =~ -cachyos ]]; then
    KERNEL_TYPE="CachyOS"
elif [[ "$KERNEL" =~ -xanmod ]]; then
    KERNEL_TYPE="XanMod"
elif [[ "$KERNEL" =~ -arch ]]; then
    KERNEL_TYPE="Arch"
fi

# --- Modelo CORTO y limpio de GPU ---
GPU_MODEL=$(lspci -vnn 2>/dev/null | grep -i -E 'vga|3d|display' | head -n 1 | sed -E \
    -e 's/.*: //; s/ \[.*//; s/ \(rev .*\)//' \
    -e 's/Corporation //i; s/Integrated Graphics Controller//i' \
    -e 's/NVIDIA GeForce /NVIDIA /i; s/Advanced Micro Devices, Inc. \[AMD\/ATI\] //i' \
    -e 's/ +/ /g; s/^ +| +$//g')
[ -z "$GPU_MODEL" ] && GPU_MODEL="Intel UHD Graphics"

# --- RAM: Extracción exacta para ThinkPad X1 Carbon (ej. LPDDR3 2133MHz) ---
# NOTA: en portátiles con RAM soldada (como el X1C8) dmidecode reporta
# "Form Factor: Row Of Chips" en vez de "SODIMM", pero el campo "Type:"
# (LPDDR3, LPDDR4, etc.) y "Configured Memory Speed:" se leen igual.
MEM_TYPE=""
MEM_SPEED=""
MEM_PERM_ERROR=0

if command -v dmidecode >/dev/null 2>&1; then

    # -t 17 = "Memory Device" (por-DIMM: Type, Speed, Configured Memory Speed).
    if DMI=$(sudo -n dmidecode -t 17 2>/dev/null) && [ -n "$DMI" ]; then
        MEM_TYPE=$(
            echo "$DMI" |
            awk -F': ' '/^[[:space:]]*Type:/ && $2 !~ /Unknown|Other|None/ {gsub(/^ +| +$/,"",$2); print $2; exit}'
        )

        # Velocidad REAL a la que corre la RAM ahora mismo.
        MEM_SPEED_RAW=$(
            echo "$DMI" |
            awk -F': ' '/Configured Memory Speed:/ && $2 !~ /Unknown/ {gsub(/^ +| +$/,"",$2); print $2; exit}'
        )

        # Fallback: velocidad máxima soportada por el módulo (no la actual).
        if [ -z "$MEM_SPEED_RAW" ]; then
            MEM_SPEED_RAW=$(
                echo "$DMI" |
                awk -F': ' '/^[[:space:]]*Speed:/ && $2 !~ /Unknown/ {gsub(/^ +| +$/,"",$2); print $2; exit}'
            )
        fi

        # dmidecode reporta "2133 MT/s" o "2133 MHz" -> nos quedamos solo
        # con el número y normalizamos a "MHz" para el formato pedido.
        MEM_SPEED=$(echo "$MEM_SPEED_RAW" | grep -oE '^[0-9]+')
    else
        # sudo -n falló: o no hay regla NOPASSWD para dmidecode, o el usuario
        # nunca autenticó y no puede hacerlo sin prompt. Ver documentación
        # al final de este archivo para la línea de sudoers necesaria.
        MEM_PERM_ERROR=1
    fi
fi

if [ -n "$MEM_TYPE" ] && [ -n "$MEM_SPEED" ]; then
    MEM_INFO="${MEM_TYPE} ${MEM_SPEED}MHz"
elif [ -n "$MEM_TYPE" ]; then
    MEM_INFO="$MEM_TYPE"
elif [ "$MEM_PERM_ERROR" -eq 1 ]; then
    MEM_INFO="RAM (sin permisos)"
else
    MEM_INFO="RAM"
fi

# --- Disco físico que contiene la raíz (/) ---
DISK_MODEL="N/A"
DISK_FS="N/A"
DISK_PARTITION="N/A"
ROOT_SRC_RAW=$(findmnt -no SOURCE / 2>/dev/null)
DISK_FS=$(findmnt -no FSTYPE / 2>/dev/null)
[ -z "$DISK_FS" ] && DISK_FS="N/A"

ROOT_SRC=$(echo "$ROOT_SRC_RAW" | sed -E 's/\[.*\]//')
ROOT_SUBVOL=$(echo "$ROOT_SRC_RAW" | grep -oP '(?<=\[/)[^]]*(?=\])')
if [ -n "$ROOT_SUBVOL" ]; then
    DISK_PARTITION="${ROOT_SRC} (@${ROOT_SUBVOL#@})"
else
    DISK_PARTITION="$ROOT_SRC_RAW"
fi
[ -z "$DISK_PARTITION" ] && DISK_PARTITION="N/A"

if [ -n "$ROOT_SRC_RAW" ]; then
    DISK_MODEL=$(lsblk -sno MODEL "$ROOT_SRC_RAW" 2>/dev/null | grep -v -E '^$|N/A' | tail -n 1 | sed -E 's/^ +| +$//g')
fi
[ -z "$DISK_MODEL" ] && DISK_MODEL="NVMe SSD"

get_iface() {
    ip route get 1.1.1.1 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}'
}

read_bytes() {
    local iface="$1" dir="$2"
    cat "/sys/class/net/${iface}/statistics/${dir}_bytes" 2>/dev/null || echo 0
}

human_speed() {
    local bytes="$1"
    awk -v b="$bytes" 'BEGIN {
        if (b >= 1048576) printf "%.1f MB/s", b/1048576;
        else printf "%.1f KB/s", b/1024;
    }'
}

resolve_nic_name() {
    local iface="$1" devpath name vendor device pci_id
    devpath=$(readlink -f "/sys/class/net/${iface}/device" 2>/dev/null)
    [ -z "$devpath" ] && { echo "N/A"; return; }

    if command -v lspci >/dev/null 2>&1; then
        if [ -f "$devpath/vendor" ] && [ -f "$devpath/device" ]; then
            vendor=$(cat "$devpath/vendor" 2>/dev/null | sed 's/^0x//')
            device=$(cat "$devpath/device" 2>/dev/null | sed 's/^0x//')
            name=$(lspci -d "${vendor}:${device}" 2>/dev/null | head -n 1 | sed -E 's/^[0-9a-f:.]+ [^:]+: //; s/ \(rev [0-9a-f]+\)$//; s/\[[^]]*\]//g; s/ +/ /g' | xargs)
        fi
        if [ -z "$name" ]; then
            pci_id=$(basename "$devpath")
            name=$(lspci -s "${pci_id#*:}" 2>/dev/null | head -n 1 | sed -E 's/^[0-9a-f:.]+ [^:]+: //; s/ \(rev [0-9a-f]+\)$//; s/\[[^]]*\]//g; s/ +/ /g' | xargs)
        fi
    fi

    if [ -z "$name" ] && command -v lsusb >/dev/null 2>&1; then
        local d="$devpath"
        while [ "$d" != "/" ] && [ "$d" != "/sys" ] && [ -n "$d" ]; do
            if [ -f "$d/idVendor" ] && [ -f "$d/idProduct" ]; then
                vendor=$(cat "$d/idVendor" 2>/dev/null)
                device=$(cat "$d/idProduct" 2>/dev/null)
                name=$(lsusb -d "${vendor}:${device}" 2>/dev/null | head -n 1 | sed -E 's/^.*ID [0-9a-f]{4}:[0-9a-f]{4} //; s/ *\(.*//' | xargs)
                break
            fi
            d=$(dirname "$d")
        done
    fi
    [ -z "$name" ] && name="Intel Wi-Fi 6 AX201"
    echo "$name"
}

IFACE=""
NET_TYPE="disconnected"
NIC_NAME="N/A"
PREV_RX=0
PREV_TX=0

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

PREV_RC6=0
PREV_RC6_TS=0
if [ -n "$GPU_RC6_PATH" ] && [ -f "$GPU_RC6_PATH" ]; then
    PREV_RC6=$(cat "$GPU_RC6_PATH" 2>/dev/null || echo 0)
    PREV_RC6_TS=$(date +%s%3N)
fi

while true; do
    sleep 2

    UPTIME=$(uptime -p 2>/dev/null | sed 's/^up //')
    CPU_TEMP=$(sensors 2>/dev/null | awk '/Package id 0:|Tctl:|Tdie:/ { gsub(/[+°C]/,"",$4); print $4; exit }')
    [ -z "$CPU_TEMP" ] && CPU_TEMP="N/A"

    CUR_IFACE=$(get_iface)
    [ -z "$CUR_IFACE" ] && CUR_IFACE="lo"

    if [[ "$CUR_IFACE" == "lo" ]]; then
        NET_TYPE="disconnected"
    elif [[ "$CUR_IFACE" == wl* ]]; then
        NET_TYPE="wifi"
    elif [[ "$CUR_IFACE" == en* || "$CUR_IFACE" == eth* ]]; then
        NET_TYPE="ethernet"
    else
        NET_TYPE="unknown"
    fi

    if [ "$CUR_IFACE" != "$IFACE" ]; then
        IFACE="$CUR_IFACE"
        PREV_RX=$(read_bytes "$IFACE" rx)
        PREV_TX=$(read_bytes "$IFACE" tx)
        NIC_NAME=$(resolve_nic_name "$IFACE")
    fi

    ESSID=$(iwgetid -r 2>/dev/null)
    if [ -n "$ESSID" ]; then
        NET_LABEL="󰤨 ${ESSID}"
    else
        NET_LABEL="󰈁 Connected"
    fi

    CUR_RX=$(read_bytes "$IFACE" rx)
    CUR_TX=$(read_bytes "$IFACE" tx)
    DOWN_BPS=$(( (CUR_RX - PREV_RX) / 2 ))
    UP_BPS=$(( (CUR_TX - PREV_TX) / 2 ))
    PREV_RX=$CUR_RX
    PREV_TX=$CUR_TX
    [ "$DOWN_BPS" -lt 0 ] && DOWN_BPS=0
    [ "$UP_BPS" -lt 0 ] && UP_BPS=0
    NET_DOWN=$(human_speed "$DOWN_BPS")
    NET_UP=$(human_speed "$UP_BPS")

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

    # Agregamos kernel_type al objeto JSON saliente
    jq -Rn -c --arg sysname "$SYSNAME" \
           --arg kernel "$KERNEL" \
           --arg kernel_type "$KERNEL_TYPE" \
           --arg machine "$MACHINE" \
           --arg distro "$DISTRO" \
           --arg cpu_model "$CPU_MODEL" \
           --arg cpu_model_short "$CPU_MODEL_SHORT" \
           --arg cpu_temp "$CPU_TEMP" \
           --arg mem_info "$MEM_INFO" \
           --arg gpu_model "$GPU_MODEL" \
           --arg disk_model "$DISK_MODEL" \
           --arg disk_fs "$DISK_FS" \
           --arg disk_partition "$DISK_PARTITION" \
           --arg uptime "$UPTIME" \
           --arg iface "$IFACE" \
           --arg net_type "$NET_TYPE" \
           --arg nic_name "$NIC_NAME" \
           --arg net_label "$NET_LABEL" \
           --arg net_up "$NET_UP" \
           --arg net_down "$NET_DOWN" \
           --argjson net_down_raw "$DOWN_BPS" \
           --argjson net_up_raw "$UP_BPS" \
           --arg gpu_freq "$GPU_FREQ" \
           --arg gpu_usage "$GPU_USAGE" \
           '{sysname:$sysname, kernel:$kernel, kernel_type:$kernel_type, machine:$machine, distro:$distro,
             cpu_model:$cpu_model, cpu_model_short:$cpu_model_short, cpu_temp:$cpu_temp,
             mem_info:$mem_info, gpu_model:$gpu_model, disk_model:$disk_model, disk_fs:$disk_fs, 
             disk_partition:$disk_partition, uptime:$uptime, iface:$iface, net_type:$net_type, 
             nic_name:$nic_name, net_label:$net_label, net_up:$net_up, net_down:$net_down, 
             net_down_raw:$net_down_raw, net_up_raw:$net_up_raw, gpu_freq:$gpu_freq, gpu_usage:$gpu_usage}'
done

# ══════════════════════════════════════════════════════════
#  Permisos requeridos — lectura de RAM (Type + Speed)
# ══════════════════════════════════════════════════════════
# dmidecode necesita privilegios de root para leer la tabla SMBIOS
# (es lo que expone el fabricante del módulo, tipo LPDDR3/DDR4, y la
# velocidad configurada). Sin esto, sysinfo.sh mostrará
# "RAM (sin permisos)" en vez de "LPDDR3 2133MHz".
#
# Para permitir que sudo -n (no interactivo) ejecute SOLO dmidecode
# sin pedir contraseña, crea una regla de sudoers dedicada:
#
#   echo "$(whoami) ALL=(root) NOPASSWD: /usr/bin/dmidecode" \
#     | sudo tee /etc/sudoers.d/dmidecode
#   sudo chmod 440 /etc/sudoers.d/dmidecode
#
# Verifica que quedó bien escrita (evita romper sudo por un typo):
#
#   sudo visudo -c -f /etc/sudoers.d/dmidecode
#
# Prueba que funciona sin contraseña (debe imprimir la tabla, no pedir clave):
#
#   sudo -n dmidecode -t 17
#
# Nota de seguridad: dmidecode es de solo lectura (no puede modificar
# el sistema), pero sí expone datos como el número de serie del equipo
# y de los módulos de RAM. Restringir la regla a ese único binario
# (como arriba) evita dar sudo total sin contraseña.
# ══════════════════════════════════════════════════════════