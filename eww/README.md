# eww sidebar — dangm0z-OS

Reemplazo del `conky.conf` original, migrado a eww (yuck + scss),
integrado como capa `layer-shell` en Hyprland.

## Estructura

```
eww/
├── eww.yuck               # índice, solo incluye los demás archivos
├── eww.scss                # tema Tokyo Night
├── vars/
│   └── polls.yuck          # deflisten/defpoll (lo que las magic vars no cubren)
├── widgets/
│   └── sidebar.yuck        # todos los (defwidget ...)
├── windows/
│   └── windows.yuck        # (defwindow sidebar ...)
├── scripts/
│   ├── sysinfo.sh           # JSON: cpu model/temp, uptime, red, iGPU
│   └── top-processes.sh     # una fila del top de procesos por invocación
└── hyprland-sidebar.conf    # snippet para pegar en tu hyprland.conf
```

## Dependencias

```bash
sudo pacman -S eww jq
```

- `jq` — arma el JSON en `sysinfo.sh`.
- `iwgetid` (paquete `wireless_tools`) — opcional, para el ESSID de wifi.
- `lm_sensors` (comando `sensors`) — opcional, para la temperatura de CPU.
  Si no está instalado o no configurado (`sensors-detect`), el sidebar
  simplemente muestra "N/A" en ese campo, sin romper nada.

## Instalación

1. Copiá toda la carpeta a `~/.config/eww/`:

   ```bash
   cp -r eww-sidebar/* ~/.config/eww/
   chmod +x ~/.config/eww/scripts/*.sh
   ```

2. Agregá el contenido de `hyprland-sidebar.conf` a tu `hyprland.conf`
   (o hacé `source = ~/.config/hypr/hyprland-sidebar.conf` si preferís
   mantenerlo separado, como ya hacés con el resto de tus módulos).

3. Recargá Hyprland (`hyprctl reload`) o simplemente cerrá sesión y
   volvé a entrar.

4. Para probar sin reiniciar la sesión:

   ```bash
   eww daemon
   eww open sidebar
   ```

   Y para depurar:

   ```bash
   eww logs          # ver errores de yuck/scss en vivo
   eww state          # ver el valor actual de cada variable
   eww close sidebar && eww open sidebar   # recargar tras editar scss
   ```

   Nota: eww detecta cambios en `.yuck`/`.scss` y recarga solo, pero si
   algo queda "pegado" (por ejemplo tras tocar `windows.yuck`), conviene
   cerrar y volver a abrir la ventana.

## Notas de personalización

- **Monitor**: `:monitor 0` en `windows/windows.yuck`. Con tu dock y el
  ultrawide conectado, puede que quieras fijarlo al monitor interno o
  al externo según el caso — cambiá el índice o usá el nombre del
  output (`:monitor "eDP-1"`, por ejemplo).
- **Ancho/posición**: geometría en el mismo archivo (`:width "300px"`,
  `:anchor "top right"`, etc.).
- **GPU**: como tu X1 Carbon Gen 8 usa la iGPU Intel UHD 620, el
  script lee `/sys/class/drm/card0/gt_cur_freq_mhz` y
  `gpu_busy_percent` (expuestos por el driver `i915`, sin dependencias
  extra). El `conky.conf` que subiste tenía la sección de GPU armada
  para NVIDIA (`nvidia-smi`) — si en algún momento tenés una GPU NVIDIA
  discreta en otra máquina, decime y te dejo esa variante también.
- **Interfaz de red**: se detecta sola vía `ip route get`, no hace
  falta hardcodear `wlan0` ni `enp0s31f6` como en el conky original.
- **Transparencia**: el nivel de opacidad del panel está en `eww.scss`,
  variable `$bg-panel` (`rgba(26, 27, 38, 0.40)` — el último valor es
  el alpha, `0` = invisible, `1` = sólido). Para que además se vea con
  desenfoque real (no solo transparente) necesitás tener el blur
  global activado en tu `hyprland.conf`:
  ```
  decoration {
      blur {
          enabled = true
          size = 6
          passes = 3
      }
  }
  ```
  El `layerrule = blur, eww-sidebar` que ya está en
  `hyprland-sidebar.conf` solo aplica ese blur global a la ventana del
  sidebar; si el blur global está apagado, no hay nada que aplicar.
- **Toggle de procesos**: hacé click en el título "Procesos" para
  colapsar/expandir esa sección (usa `revealer` + `defvar`).

## Cómo verifiqué esto

Repasé la documentación oficial de eww (`elkowar.github.io/eww`) para
confirmar sintaxis vigente de `defwidget`/`defwindow`/`deflisten`, las
magic variables (`EWW_CPU`, `EWW_RAM`, `EWW_DISK`, `EWW_NET`,
`EWW_TEMPS`) y las propiedades de los widgets `image`, `progress` y
`graph`, ya que esas partes cambiaron entre versiones de eww en el
pasado.
