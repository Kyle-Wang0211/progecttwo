#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP_SQL_SRC="${1:-/root/control_plane/migrations/bootstrap_role_db.sql}"
MIGRATION_SQL_SRC="${2:-/root/control_plane/migrations/0001_initial.sql}"

cp "$BOOTSTRAP_SQL_SRC" /tmp/bootstrap_role_db.sql
cp "$MIGRATION_SQL_SRC" /tmp/0001_initial.sql
chmod 644 /tmp/bootstrap_role_db.sql /tmp/0001_initial.sql

runuser -u postgres -- psql -f /tmp/bootstrap_role_db.sql

if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname = 'aether_control'" | grep -q 1; then
  runuser -u postgres -- createdb -O aether aether_control
fi

PGPASSWORD=aetherpass psql -h 127.0.0.1 -U aether -d aether_control -f /tmp/0001_initial.sql
PGPASSWORD=aetherpass psql -h 127.0.0.1 -U aether -d aether_control -c '\dt'
