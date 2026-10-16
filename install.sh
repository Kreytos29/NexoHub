#!/usr/bin/env bash
# Instalador de NexoHub (scrcpy-autoconfig) para Linux.
#
# Que hace:
#   1. Verifica que haya python3 (>=3.9) y pip.
#   2. Copia el proyecto a una ubicacion fija (~/.local/share/nexohub),
#      asi que anda aunque despues borres esta carpeta descargada. Tus
#      ajustes NO viven ahi: estan en ~/.config/nexohub/config.yaml, asi
#      que actualizar nunca los pisa. Si venis de una version anterior a
#      la 1.1 (que guardaba los ajustes dentro de la instalacion), lo que
#      habias cambiado se migra solo a ese archivo.
#   3. Instala las dependencias de Python (requirements.txt) con pip
#      para el usuario actual, sin tocar el Python del sistema.
#   4. Crea un lanzador "nexohub" en ~/.local/bin y una entrada de menu
#      (.desktop) para que aparezca en rofi/wofi/tu launcher de siempre.
#
# Las dependencias del SISTEMA (adb, scrcpy, cpupower, polkit) las
# instala la propia app la primera vez que se abre, desde la seccion
# "Dependencias" (con contrasena grafica via pkexec) -- este script no
# las toca para no pedir sudo mas de lo necesario.
#
# Uso:
#   ./install.sh              instala o actualiza
#   ./install.sh --uninstall  desinstala todo lo que crea este script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/nexohub"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
LAUNCHER="$BIN_DIR/nexohub"
DESKTOP_FILE="$DESKTOP_DIR/nexohub.desktop"
ICON_FILE="$ICON_DIR/nexohub.svg"
USER_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/nexohub"
USER_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/nexohub"

c_green="\033[0;32m"; c_yellow="\033[0;33m"; c_red="\033[0;31m"; c_reset="\033[0m"
info()  { echo -e "${c_green}==>${c_reset} $1"; }
warn()  { echo -e "${c_yellow}⚠${c_reset}  $1"; }
error() { echo -e "${c_red}✗${c_reset} $1" >&2; }

uninstall() {
    info "Desinstalando NexoHub..."
    rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_FILE"
    if [ -d "$INSTALL_DIR" ]; then
        read -r -p "¿Borrar también $INSTALL_DIR (el programa)? [y/N] " reply
        case "$reply" in
            [yY]*) rm -rf "$INSTALL_DIR"; info "Borrado $INSTALL_DIR." ;;
            *) info "Se dejó $INSTALL_DIR sin tocar." ;;
        esac
    fi
    if [ -d "$USER_CONFIG_DIR" ] || [ -d "$USER_STATE_DIR" ]; then
        read -r -p "¿Borrar también tus ajustes y registros ($USER_CONFIG_DIR, $USER_STATE_DIR)? [y/N] " reply
        case "$reply" in
            [yY]*) rm -rf "$USER_CONFIG_DIR" "$USER_STATE_DIR"; info "Ajustes y registros borrados." ;;
            *) info "Se conservaron tus ajustes." ;;
        esac
    fi
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    info "Listo. NexoHub desinstalado."
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

# --- 1. python3 y pip ---------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    error "No se encontró python3. Instalalo con el gestor de paquetes de tu"
    error "distro (ej. 'sudo pacman -S python', 'sudo apt install python3') y"
    error "volvé a correr este script."
    exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJOR="$(echo "$PY_VERSION" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VERSION" | cut -d. -f2)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    warn "Se detectó Python $PY_VERSION. NexoHub está probado en Python 3.9+;"
    warn "puede fallar en versiones más viejas. Se sigue igual por las dudas."
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
    warn "pip no está disponible para python3; intentando 'ensurepip'..."
    python3 -m ensurepip --upgrade >/dev/null 2>&1 || {
        error "No se pudo preparar pip automáticamente. Instalá el paquete"
        error "'python-pip' (pacman) / 'python3-pip' (apt/dnf/zypper) y reintentá."
        exit 1
    }
fi

# --- 2. copiar el proyecto a una ubicacion fija -------------------------
info "Instalando en $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# El config.yaml de la instalación ya no guarda ajustes de usuario (son los
# valores de fábrica). Si había uno de una versión anterior, se guarda una
# copia para migrar lo que el usuario cambió (ver más abajo).
LEGACY_CONFIG=""
if [ -f "$INSTALL_DIR/config.yaml" ]; then
    LEGACY_CONFIG="$(mktemp)"
    cp "$INSTALL_DIR/config.yaml" "$LEGACY_CONFIG"
fi
# rsync si esta disponible (mas prolijo, borra sobrantes de versiones
# viejas); si no, cp -r alcanza para instalar/actualizar.
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude ".git" --exclude ".github" --exclude "__pycache__" \
        --exclude "*.pyc" --exclude ".pytest_cache" \
        "$SCRIPT_DIR"/ "$INSTALL_DIR"/
else
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name '.venv' -exec rm -rf {} +
    cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR"/
    find "$INSTALL_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
fi

# --- 3. dependencias de Python -------------------------------------------
info "Instalando dependencias de Python (puede tardar un minuto)..."
if ! python3 -m pip install --user --break-system-packages -r "$INSTALL_DIR/requirements.txt"; then
    warn "Falló con --break-system-packages (pip viejo); reintentando sin esa opción..."
    python3 -m pip install --user -r "$INSTALL_DIR/requirements.txt"
fi

# --- 3b. migrar ajustes de una version anterior -------------------------
if [ -n "$LEGACY_CONFIG" ]; then
    if (cd "$INSTALL_DIR" && python3 -c \
        'import sys, config_store; sys.exit(0 if config_store.migrate_legacy_config(sys.argv[1]) else 1)' \
        "$LEGACY_CONFIG") 2>/dev/null; then
        info "Se migraron tus ajustes anteriores a $USER_CONFIG_DIR/config.yaml."
    fi
    rm -f "$LEGACY_CONFIG"
fi

# --- 4. lanzador + entrada de menu ---------------------------------------
mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR" && exec python3 gui.py "\$@"
EOF
chmod +x "$LAUNCHER"

cp "$INSTALL_DIR/assets/nexohub.svg" "$ICON_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=NexoHub
GenericName=Configurador de scrcpy
Comment=Detecta tu celular y lanza scrcpy con el mejor ajuste automatico
Exec=$LAUNCHER
Icon=nexohub
Terminal=false
Categories=Utility;
Keywords=scrcpy;android;adb;celular;streaming;espejo;
StartupWMClass=nexohub
EOF
chmod +x "$DESKTOP_FILE"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true

info "¡Listo! NexoHub instalado."
echo
echo "  - Buscalo en tu launcher de apps (rofi/wofi/menu) como \"NexoHub\"."
echo "  - O corré: nexohub"
echo

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        warn "$BIN_DIR no está en tu PATH, así que 'nexohub' no va a andar"
        warn "directo desde la terminal (el ícono del menú sí funciona igual)."
        warn "Para habilitarlo, agregá esta línea a tu ~/.bashrc o ~/.zshrc:"
        echo "      export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac

info "La primera vez que la abras, usá la sección \"Dependencias\" (abajo de"
info "todo) para instalar adb/scrcpy/polkit si todavía te faltan (te va a pedir"
info "la contraseña en una ventana gráfica, con pkexec)."
