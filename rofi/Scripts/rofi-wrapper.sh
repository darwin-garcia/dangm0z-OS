#!/usr/bin/env bash
#
# rofi-wrapper.sh
# Bindea esto (no `rofi` directo) en Hyprland, p.ej.:
#   bind = $mainMod, SPACE, exec, ~/.config/rofi/Scripts/rofi-wrapper.sh
#
# default.rasi solo controla los modos "drun" y "recursivebrowser" (Apps
# y Files), que sí pueden convivir en una misma ventana con Ctrl+Tab.
# "Terminal" (run) queda fuera de "modi" a proposito: rofi no permite
# columnas/iconos distintos por modo dentro de la misma ventana, asi
# que el boton "button-terminal" del tema dispara kb-custom-2 (exit
# code 11 = 9+2) y aqui lo capturamos para relanzar "run" con su propio
# override de 1 columna sin iconos.

ROFI_DIR="$HOME/.config/rofi"

rofi -show drun \
     -modi "drun,recursivebrowser" \
     -theme "$HOME/.config/rofi/themes/default.rasi"
ec=$?

if [[ $ec -eq 11 ]]; then
    rofi -show run \
         -theme "$ROFI_DIR/default.rasi" \
         -theme-str 'configuration { show-icons: false; }' \
         -theme-str 'listview { columns: 1; lines: 8; }' \
         -theme-str 'element { orientation: horizontal; }'
fi
