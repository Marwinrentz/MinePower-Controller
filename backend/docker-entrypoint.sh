#!/bin/sh
# Startet MinePower ohne root-Rechte.
#
# Der Container beginnt als root, damit er die Datenordner (Volumes) dem
# Anwendungsnutzer geben kann – bestehende Installationen haben dort Dateien
# von root, etwa die Schlüsseldatei mit Rechten 600. Danach gibt er alle
# Rechte ab und startet die Anwendung als unprivilegierter Nutzer.
#
# PUID/PGID wählen den Nutzer (Standard 1000:1000, auf Unraid üblich: 99:100).
# Läuft der Container bereits ohne root (docker run --user …), wird nichts
# umgestellt.
set -e

APP_UID="${PUID:-1000}"
APP_GID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    for dir in /app/data /app/backups; do
        mkdir -p "$dir"
        # Nur umstellen, wenn etwas nicht passt: ein großer Sicherungsordner
        # wird nicht bei jedem Start durchlaufen.
        if [ -n "$(find "$dir" ! -user "$APP_UID" -print -quit 2>/dev/null)" ]; then
            chown -R "$APP_UID:$APP_GID" "$dir" 2>/dev/null \
                || echo "Warnung: $dir gehört nicht Nutzer $APP_UID und lässt sich nicht umstellen" >&2
        fi
    done
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups --no-new-privs "$@"
fi

exec "$@"
