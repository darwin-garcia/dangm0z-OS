#!/usr/bin/env bash
# dangm0z-OS :: eww Control Center — data feed + action dispatcher
#
# Usage:
#   control-center.sh listen               -> stream JSON state forever (used by deflisten)
#   control-center.sh data                 -> print JSON state once
#   control-center.sh wifi-toggle
#   control-center.sh bt-toggle
#   control-center.sh power-profile-cycle
#   control-center.sh nightlight-toggle    -> wlsunset
#   control-center.sh dnd-toggle           -> mako
#   control-center.sh airplane-toggle
#   control-center.sh hotspot-toggle
#   control-center.sh set-brightness <0-100>
#   control-center.sh set-volume <0-100>
#   control-center.sh power-menu           -> wlogout
#   control-center.sh cal-listen           -> stream JSON del grid de calendario (calendar-data.py)
#   control-center.sh cal-prev-month / cal-next-month
#   control-center.sh cal-prev-year  / cal-next-year
#   control-center.sh cal-reset            -> vuelve al mes/año actual
#
# Deps: jq, networkmanager, bluez-utils, power-profiles-daemon,
#       brightnessctl, wireplumber (wpctl), mako, wlsunset, wlogout, python3
# (la batería del footer no pasa por este script: usa EWW_BATTERY,
#  la magic variable nativa de eww, así que upower no hace falta)
#
# Si algo no "lee el valor real" (bluetooth, power profile, brillo,
# volumen), revisá ~/.cache/control-center.log — cada collector deja
# rastro ahí cuando el comando subyacente falla o no está instalado,
# mismo patrón de logging que ya usás en calendar-popup.py.

set -uo pipefail
trap 'exit 0' SIGPIPE SIGTERM SIGINT

HOTSPOT_CON="hotspot"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
LOGFILE="$HOME/.cache/control-center.log"
CAL_STATE="${XDG_RUNTIME_DIR:-/tmp}/cc-cal-state"

log() {
  mkdir -p "$(dirname "$LOGFILE")" 2>/dev/null
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"
}

need() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# data collectors
# ---------------------------------------------------------------------------

get_wifi() {
  local dev ssid="" signal=0 band="" freq connected=false
  dev=$(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | awk -F: '$2=="wifi" && $3=="connected"{print $1; exit}')
  if [[ -n "$dev" ]]; then
    connected=true
    ssid=$(nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')
    signal=$(nmcli -t -f active,signal dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')
    freq=$(nmcli -t -f active,freq dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')
    [[ "$freq" =~ ^5 ]] && band="5 GHz" || band="2.4 GHz"
  fi
  # title/sub ya vienen formateados y con el fallback resuelto acá —
  # el yuck del lado de eww no necesita hacer ningún ternario ni
  # replace(), solo mostrar el campo tal cual. Menos lógica en yuck =
  # menos superficie para bugs de su lenguaje de expresiones.
  local title sub
  if [[ "$connected" == true ]]; then
    title="$ssid"
    sub="$band (${signal:-0}%)"
  else
    title="Wi-Fi"
    sub=""
  fi
  jq -n --arg ssid "$ssid" --arg band "$band" --arg title "$title" --arg sub "$sub" \
        --argjson signal "${signal:-0}" --argjson connected "$connected" \
        '{connected:$connected, ssid:$ssid, band:$band, signal:$signal, title:$title, sub:$sub}'
}

# bluetoothctl "devices Connected" solo existe desde BlueZ 5.65 — en
# versiones más viejas ese filtro no existe y devuelve vacío siempre,
# por eso el bluetooth nunca se detectaba. Este loop es compatible con
# cualquier versión: recorre TODOS los dispositivos conocidos y
# pregunta uno por uno si están conectados.
get_bluetooth() {
  local mac="" name="" battery=0 connected=false raw dev_mac dev_name

  if ! need bluetoothctl; then
    log "get_bluetooth: bluetoothctl no está instalado"
    jq -n '{connected:false, device:"", battery:0}'
    return
  fi

  while IFS= read -r line; do
    [[ "$line" =~ ^Device\ ([0-9A-Fa-f:]+)\ (.+)$ ]] || continue
    dev_mac="${BASH_REMATCH[1]}"
    dev_name="${BASH_REMATCH[2]}"
    if timeout 2 bluetoothctl info "$dev_mac" 2>/dev/null | grep -q "Connected: yes"; then
      mac="$dev_mac"
      name="$dev_name"
      connected=true
      break
    fi
  done < <(timeout 2 bluetoothctl devices 2>/dev/null)

  if [[ "$connected" == true ]]; then
    raw=$(timeout 2 bluetoothctl info "$mac" 2>/dev/null)
    battery=$(awk -F'0x' '/Battery Percentage/{gsub(")","",$2); printf "%d", strtonum("0x"$2); exit}' <<<"$raw")
    [[ "$battery" =~ ^[0-9]+$ ]] || battery=0
  else
    log "get_bluetooth: sin dispositivos conectados (bluetoothctl devices no listó ninguno con 'Connected: yes')"
  fi

  local title sub
  if [[ "$connected" == true ]]; then
    title="$name"
    sub="${battery}%"
  else
    title="Bluetooth"
    sub=""
  fi
  jq -n --arg device "$name" --arg title "$title" --arg sub "$sub" \
        --argjson battery "${battery:-0}" --argjson connected "$connected" \
        '{connected:$connected, device:$device, battery:$battery, title:$title, sub:$sub}'
}

get_power_profile() {
  if ! need powerprofilesctl; then
    log "get_power_profile: powerprofilesctl no está instalado"
    echo "N/A"
    return
  fi
  local out rc
  out=$(timeout 2 powerprofilesctl get 2>&1)
  rc=$?
  if [[ $rc -ne 0 || -z "$out" ]]; then
    log "get_power_profile: falló powerprofilesctl get (rc=$rc) -> $out (¿está corriendo power-profiles-daemon.service?)"
    echo "N/A"
    return
  fi
  # Capitalizado acá (bash), no en yuck: "performance" -> "Performance"
  echo "${out^}"
}

get_brightness() {
  if ! need brightnessctl; then
    log "get_brightness: brightnessctl no está instalado"
    echo 0
    return
  fi
  local cur max
  cur=$(brightnessctl g 2>>"$LOGFILE")
  max=$(brightnessctl m 2>>"$LOGFILE")
  if [[ -z "$cur" || -z "$max" || "$max" -eq 0 ]]; then
    log "get_brightness: brightnessctl no devolvió valores válidos (cur='$cur' max='$max')"
    echo 0
    return
  fi
  echo $(( cur * 100 / max ))
}

get_volume() {
  if ! need wpctl; then
    log "get_volume: wpctl no está instalado (¿está corriendo wireplumber?)"
    echo 0
    return
  fi
  local raw vol
  raw=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>>"$LOGFILE")
  if [[ -z "$raw" ]]; then
    log "get_volume: wpctl get-volume no devolvió nada (¿hay un sink por defecto configurado?)"
    echo 0
    return
  fi
  vol=$(awk '{printf "%d", ($2*100)+0.5}' <<<"$raw")
  [[ -z "$vol" ]] && vol=0
  echo "$vol"
}

get_muted() {
  need wpctl || { echo false; return; }
  wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null | grep -q MUTED && echo true || echo false
}

get_nightlight() { pgrep -x wlsunset >/dev/null 2>&1 && echo true || echo false; }
get_dnd()        { makoctl mode 2>/dev/null | grep -q "do-not-disturb" && echo true || echo false; }

get_airplane() {
  local wifi_state bt_blocked=false
  wifi_state=$(nmcli -t -f WIFI g 2>/dev/null)
  rfkill list bluetooth 2>/dev/null | grep -q "Soft blocked: yes" && bt_blocked=true
  [[ "$wifi_state" == "disabled" && "$bt_blocked" == "true" ]] && echo true || echo false
}

get_hotspot() {
  nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}:" && echo true || echo false
}

collect() {
  local out
  out=$(jq -n \
    --arg user "$(whoami)" \
    --argjson wifi "$(get_wifi)" \
    --argjson bluetooth "$(get_bluetooth)" \
    --arg power_profile "$(get_power_profile)" \
    --argjson nightlight "$(get_nightlight)" \
    --argjson dnd "$(get_dnd)" \
    --argjson airplane "$(get_airplane)" \
    --argjson hotspot "$(get_hotspot)" \
    --argjson brightness "$(get_brightness)" \
    --argjson volume "$(get_volume)" \
    --argjson muted "$(get_muted)" \
    '{user:$user, wifi:$wifi, bluetooth:$bluetooth, power_profile:$power_profile,
      nightlight:$nightlight, dnd:$dnd, airplane:$airplane, hotspot:$hotspot,
      brightness:$brightness, volume:$volume, muted:$muted}' 2>>"$LOGFILE")

  # Si CUALQUIER collector devolvió algo que rompió el jq -n (ej. un
  # campo vacío pasado a --argjson), antes esto hacía que TODO el
  # objeto fallara en silencio y CC_DATA se quedaba pegado en
  # :initial para siempre — aunque wifi/power-profile/etc funcionaran
  # bien individualmente. Ahora, si eso pasa, se loguea y se emite un
  # JSON de emergencia válido en vez de nada, así el próximo ciclo
  # (2s después) puede recuperarse solo.
  if [[ -z "$out" ]]; then
    log "collect: jq -n falló al construir el JSON completo (ver arriba el stderr de jq)"
    echo '{"user":"'"$(whoami)"'","wifi":{"connected":false,"ssid":"","band":"","signal":0},"bluetooth":{"connected":false,"device":"","battery":0},"power_profile":"n/a","nightlight":false,"dnd":false,"airplane":false,"hotspot":false,"brightness":0,"volume":0,"muted":false}'
  else
    echo "$out"
  fi
}

# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

wifi_toggle() {
  [[ "$(nmcli -t -f WIFI g)" == "enabled" ]] && nmcli radio wifi off || nmcli radio wifi on
}

bt_toggle() {
  local mac=""
  while IFS= read -r line; do
    [[ "$line" =~ ^Device\ ([0-9A-Fa-f:]+)\  ]] || continue
    dev_mac="${BASH_REMATCH[1]}"
    if timeout 2 bluetoothctl info "$dev_mac" 2>/dev/null | grep -q "Connected: yes"; then
      mac="$dev_mac"
      break
    fi
  done < <(timeout 2 bluetoothctl devices 2>/dev/null)

  if [[ -n "$mac" ]]; then
    bluetoothctl disconnect "$mac"
  else
    bluetoothctl power on
  fi
}

power_profile_cycle() {
  if ! need powerprofilesctl; then
    log "power_profile_cycle: powerprofilesctl no está instalado"
    return
  fi
  case "$(powerprofilesctl get 2>>"$LOGFILE")" in
    performance) powerprofilesctl set balanced ;;
    balanced)    powerprofilesctl set power-saver ;;
    *)           powerprofilesctl set performance ;;
  esac
}

nightlight_toggle() {
  if pgrep -x wlsunset >/dev/null 2>&1; then
    pkill -x wlsunset
  else
    setsid -f wlsunset -t 4500 >/dev/null 2>&1
  fi
}

dnd_toggle() {
  if makoctl mode 2>/dev/null | grep -q "do-not-disturb"; then
    makoctl set-mode default
  else
    makoctl set-mode do-not-disturb
  fi
}

airplane_toggle() {
  if [[ "$(get_airplane)" == "true" ]]; then
    nmcli radio wifi on
    rfkill unblock bluetooth
  else
    nmcli radio wifi off
    rfkill block bluetooth
  fi
}

hotspot_toggle() {
  if [[ "$(get_hotspot)" == "true" ]]; then
    nmcli con down "$HOTSPOT_CON"
  else
    nmcli con up "$HOTSPOT_CON" 2>/dev/null || \
      nmcli dev wifi hotspot ifname wlan0 con-name "$HOTSPOT_CON" ssid "dangmoz-hotspot" password "changeme123"
  fi
}

set_brightness() {
  need brightnessctl || { log "set_brightness: brightnessctl no está instalado"; return; }
  brightnessctl set "${1:-50}%" >>"$LOGFILE" 2>&1
}
set_volume() {
  need wpctl || { log "set_volume: wpctl no está instalado"; return; }
  wpctl set-volume @DEFAULT_AUDIO_SINK@ "${1:-50}%" 2>>"$LOGFILE"
}
power_menu() { setsid -f wlogout >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# calendario — reusa la lógica de festivos/semanas de calendar-popup.py
# vía scripts/calendar-data.py, guardando el mes/año que se está
# navegando en un archivo de estado temporal.
# ---------------------------------------------------------------------------

cal_read() {
  if [[ -f "$CAL_STATE" ]]; then
    cat "$CAL_STATE"
  else
    date '+%-Y %-m'
  fi
}

cal_write() { echo "$1 $2" > "$CAL_STATE"; }

cal_prev_month() {
  read -r y m < <(cal_read)
  m=$((10#$m - 1))
  if (( m < 1 )); then m=12; y=$((y - 1)); fi
  cal_write "$y" "$m"
}

cal_next_month() {
  read -r y m < <(cal_read)
  m=$((10#$m + 1))
  if (( m > 12 )); then m=1; y=$((y + 1)); fi
  cal_write "$y" "$m"
}

cal_prev_year() {
  read -r y m < <(cal_read)
  cal_write "$((y - 1))" "$m"
}

cal_next_year() {
  read -r y m < <(cal_read)
  cal_write "$((y + 1))" "$m"
}

cal_reset() { rm -f "$CAL_STATE"; }

cal_listen() {
  if ! need python3; then
    log "cal_listen: python3 no está instalado, no se puede generar el calendario"
    while true; do echo '{"year":0,"month":0,"month_name":"","weeks":[]}'; sleep 5; done
  fi
  while true; do
    read -r y m < <(cal_read)
    python3 "$SCRIPT_DIR/calendar-data.py" "$y" "$m" 2>>"$LOGFILE"
    sleep 0.5
  done
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

cmd="${1:-listen}"
case "$cmd" in
  listen)
    while true; do
      out="$(collect)"
      echo "$out"
      log "listen: $out"
      sleep 2
    done
    ;;
  data)                 collect ;;
  wifi-toggle)           wifi_toggle ;;
  bt-toggle)             bt_toggle ;;
  power-profile-cycle)   power_profile_cycle ;;
  nightlight-toggle)     nightlight_toggle ;;
  dnd-toggle)            dnd_toggle ;;
  airplane-toggle)       airplane_toggle ;;
  hotspot-toggle)        hotspot_toggle ;;
  set-brightness)        set_brightness "${2:-50}" ;;
  set-volume)            set_volume "${2:-50}" ;;
  power-menu)            power_menu ;;
  cal-listen)            cal_listen ;;
  cal-prev-month)        cal_prev_month ;;
  cal-next-month)        cal_next_month ;;
  cal-prev-year)         cal_prev_year ;;
  cal-next-year)         cal_next_year ;;
  cal-reset)             cal_reset ;;
  *) echo "Unknown command: $cmd" >&2; exit 1 ;;
esac
