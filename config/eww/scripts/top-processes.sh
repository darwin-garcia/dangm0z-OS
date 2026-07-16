#!/usr/bin/env bash

ROW="${1:-1}"

ps -eo comm,pid,pcpu,rss --no-headers \
| sort -k3,3nr -k4,4nr \
| sed -n "${ROW}p" \
| awk '{
    ram = $4 / 1024
    if (ram >= 1024)
        ram = sprintf("%.2f GB", ram / 1024)
    else
        ram = sprintf("%.0f MB", ram)

    printf "%-14.14s %6s %5.1f%% %8s", $1, $2, $3, ram
}'