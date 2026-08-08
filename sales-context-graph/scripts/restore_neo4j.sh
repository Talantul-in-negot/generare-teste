#!/usr/bin/env bash
# Neo4j restore -- the other half of scripts/backup_neo4j.sh. See that
# script's header for the full reasoning (volume-level tar/untar, why not
# neo4j-admin dump/load, what this does and doesn't cover for production).
#
# Destructive: wipes the current contents of the neo4j_data volume before
# restoring. Confirms before doing so unless FORCE=1 is set (for
# scripts/verify_backup_restore.sh's own non-interactive use).
set -euo pipefail
cd "$(dirname "$0")/.."

VOLUME="${NEO4J_DATA_VOLUME:-sales-context-graph_neo4j_data}"
BACKUP_FILE="${1:?usage: restore_neo4j.sh <path-to-backup.tar.gz>}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

echo "== Neo4j restore =="
echo "volume: ${VOLUME}"
echo "from:   ${BACKUP_FILE}"

if [ "${FORCE:-0}" != "1" ]; then
  read -r -p "This will PERMANENTLY REPLACE all data in ${VOLUME}. Type 'yes' to continue: " confirm
  if [ "$confirm" != "yes" ]; then
    echo "aborted"
    exit 1
  fi
fi

echo "-- stopping neo4j --"
docker compose stop neo4j

echo "-- wiping the current volume contents --"
# MSYS_NO_PATHCONV -- see scripts/backup_neo4j.sh's comment on the same
# flag; needed here too since these are also Git-Bash/Windows bind mounts.
MSYS_NO_PATHCONV=1 docker run --rm -v "${VOLUME}:/data" alpine sh -c "rm -rf /data/* /data/.[!.]*" 2>/dev/null || true

echo "-- extracting the backup into the volume --"
BACKUP_ABS="$(cd "$(dirname "$BACKUP_FILE")" && pwd)/$(basename "$BACKUP_FILE")"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${VOLUME}:/data" \
  -v "${BACKUP_ABS}:/backup.tar.gz:ro" \
  alpine sh -c "tar xzf /backup.tar.gz -C /data"

echo "-- restarting neo4j --"
docker compose start neo4j

echo "-- waiting for neo4j to become reachable (plugin loading on startup can take ~90s) --"
for i in $(seq 1 90); do
  if docker exec scg_neo4j cypher-shell -u neo4j -p scg_dev_local "RETURN 1" > /dev/null 2>&1; then
    echo "== restore complete, neo4j is reachable =="
    exit 0
  fi
  sleep 2
done
echo "neo4j did not become reachable within 180s after restore -- check docker logs scg_neo4j" >&2
exit 1
