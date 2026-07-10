#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  top-processes.sh <fila 1-7>
# ══════════════════════════════════════════════════════════
# Devuelve UNA línea ya formateada con el proceso que ocupa
# la posición $1 en el ranking de uso de CPU. Se invoca 7
# veces (una por fila) vía defpoll en vars/polls.yuck, así
# evitamos parsear texto multilínea dentro de yuck.

ROW="${1:-1}"

ps axo comm,pid,pcpu,pmem --sort=-pcpu \
    | tail -n +2 \
    | sed -n "${ROW}p" \
    | awk '{printf "%-14.14s %6s  %5.1f%%  %5.1f%%", $1, $2, $3, $4}'
