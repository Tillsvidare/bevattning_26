#!/bin/sh
# Nattlig backup på VPS:en (körs av cron som root, se runbooken):
#   ren SQLite-kopia (.backup — snapshot av levande WAL-databas kan bli
#   trasig) + tar av mosquittos retained-data. 14 dagars retention.
# Installeras:  cp tools/server_backup.sh /root/backup.sh
#   crontab: 20 3 * * * /bin/sh /root/backup.sh >> /var/log/bevattning-backup.log 2>&1
set -eu

BACKUP_DIR=/root/backups
DB=/var/lib/docker/volumes/bevattning_26_backend-data/_data/irrigation.db
MOSQ=/var/lib/docker/volumes/bevattning_26_mosquitto-data/_data
STAMP=$(date +%F)

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB" ".backup '$BACKUP_DIR/irrigation-$STAMP.db'"
tar czf "$BACKUP_DIR/mosquitto-$STAMP.tar.gz" -C "$MOSQ" .
find "$BACKUP_DIR" -type f -mtime +14 -delete
echo "$(date -Is) backup klar: $STAMP"
