#!/bin/sh
set -eu
umask 077
target=/var/backups/garagehub
backup_tmp=$(mktemp "$target/.backup.XXXXXX")
trap 'rm -f "$backup_tmp"' EXIT
runuser -u postgres -- pg_dump --format=custom garagehub > "$backup_tmp"
mv "$backup_tmp" "$target/garagehub-$(date -u +%Y%m%dT%H%M%SZ).dump"
find "$target" -maxdepth 1 -name 'garagehub-*.dump' -type f -mtime +6 -delete
