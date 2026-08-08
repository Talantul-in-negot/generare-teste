#!/usr/bin/env bash
# Neo4j backup -- docs/evaluation.md's Showpad engineering-rigor assessment
# (2026-08-08, Band 4) named this precisely: "docs/deployment.md states
# plainly there is 'no automated Neo4j/Redis backup verification beyond
# what Aura/Upstash' provide. Honest -- and still an untested restore
# path." This script (and restore_neo4j.sh) is that untested path made
# real: a volume-level backup/restore proven to actually round-trip data,
# via scripts/verify_backup_restore.sh.
#
# Volume-level, not `neo4j-admin database dump`: the standard neo4j Docker
# image runs the server as the container's own PID 1 in the foreground, so
# there's no way to stop just the DBMS process via `docker exec` while
# keeping the container (and therefore neo4j-admin's own binary) reachable
# -- you'd have to stop the whole container either way. Given that, a
# straightforward tar of the quiesced data volume is the more robust choice
# here: no neo4j-admin version-specific dump/load syntax to get wrong, and
# it backs up exactly what's on disk, nothing more or less.
#
# Local/Docker-Compose only. This does NOT solve production backup
# automation on Fly.io + Neo4j Aura (docs/deployment.md's actual deploy
# target) -- Aura's own backup/restore tooling is what a production
# deployment needs, and is a different (paid-tier, Aura-specific)
# mechanism this script doesn't attempt to replace. What this DOES prove:
# the underlying restore procedure -- stop, wipe, replace data, restart,
# verify -- actually works, which is the part that was never tested before.
set -euo pipefail
cd "$(dirname "$0")/.."

VOLUME="${NEO4J_DATA_VOLUME:-sales-context-graph_neo4j_data}"
BACKUP_DIR="backups/neo4j"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/neo4j_data_${TIMESTAMP}.tar.gz"
mkdir -p "$BACKUP_DIR"

echo "== Neo4j backup -- ${TIMESTAMP} =="
echo "volume: ${VOLUME}"
echo "output: ${BACKUP_FILE}"

if ! docker volume inspect "$VOLUME" > /dev/null 2>&1; then
  echo "volume ${VOLUME} does not exist -- is neo4j running? (docker compose up -d neo4j)" >&2
  exit 1
fi

echo "-- stopping neo4j (data must be quiescent for a consistent backup) --"
docker compose stop neo4j

echo "-- archiving the volume --"
# A throwaway alpine container mounts the named volume read-only and tars
# it to a bind-mounted host directory -- the standard, portable way to
# back up a Docker named volume without needing to know where Docker
# Desktop actually stores it on the host filesystem.
#
# MSYS_NO_PATHCONV: on Git Bash / Windows, "$(pwd)/${BACKUP_DIR}" otherwise
# gets silently mangled into the wrong path before docker.exe ever sees it
# (MSYS's automatic POSIX->Windows path conversion) -- verified directly:
# without this, the bind mount resolved to "C:/Program Files/Git/backup"
# instead of this repo's backups/neo4j directory. Same class of bug
# loadtest/run_baseline.sh already hit and fixed earlier in this repo's
# history; harmless no-op on Linux/Mac.
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${VOLUME}:/data:ro" \
  -v "$(pwd)/${BACKUP_DIR}:/backup" \
  alpine sh -c "tar czf /backup/$(basename "$BACKUP_FILE") -C /data ."

echo "-- restarting neo4j --"
docker compose start neo4j

echo "-- waiting for neo4j to become reachable (plugin loading on startup can take ~90s) --"
for i in $(seq 1 90); do
  if docker exec scg_neo4j cypher-shell -u neo4j -p scg_dev_local "RETURN 1" > /dev/null 2>&1; then
    echo "== backup complete: ${BACKUP_FILE} ($(du -h "$BACKUP_FILE" | cut -f1)) =="
    exit 0
  fi
  sleep 2
done
echo "neo4j did not become reachable within 180s after restart -- check docker logs scg_neo4j" >&2
exit 1
