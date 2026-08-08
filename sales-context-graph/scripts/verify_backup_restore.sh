#!/usr/bin/env bash
# Proves scripts/backup_neo4j.sh + restore_neo4j.sh actually round-trip
# data, rather than just existing as unverified scripts. Writes a uniquely
# named marker node, backs up, deletes the marker (simulating data loss),
# restores from the backup, and confirms the marker is back.
#
# This is the test docs/evaluation.md's Showpad engineering-rigor
# assessment named as missing: "no automated Neo4j/Redis backup
# verification... an untested restore path." Run this after any change to
# the backup/restore scripts themselves, not routinely -- it's a real
# stop/restore cycle against the local dev database, not a fast unit test.
set -euo pipefail
cd "$(dirname "$0")/.."

MARKER_ID="backup-verify-$(date -u +%s)"

cypher() {
  docker exec scg_neo4j cypher-shell -u neo4j -p scg_dev_local "$1"
}

echo "== Step 1: write a marker node (${MARKER_ID}) =="
cypher "CREATE (:BackupVerifyMarker {id: '${MARKER_ID}', created_at: datetime()})"
cypher "MATCH (m:BackupVerifyMarker {id: '${MARKER_ID}'}) RETURN m.id" | grep -q "$MARKER_ID"
echo "marker present before backup: OK"

echo ""
echo "== Step 2: back up =="
bash scripts/backup_neo4j.sh
BACKUP_FILE="$(ls -t backups/neo4j/neo4j_data_*.tar.gz | head -1)"
echo "using backup: ${BACKUP_FILE}"

echo ""
echo "== Step 3: delete the marker (simulating data loss) =="
cypher "MATCH (m:BackupVerifyMarker {id: '${MARKER_ID}'}) DELETE m"
if cypher "MATCH (m:BackupVerifyMarker {id: '${MARKER_ID}'}) RETURN m.id" | grep -q "$MARKER_ID"; then
  echo "marker still present after deletion -- test setup is broken" >&2
  exit 1
fi
echo "marker confirmed gone: OK"

echo ""
echo "== Step 4: restore from the backup =="
FORCE=1 bash scripts/restore_neo4j.sh "$BACKUP_FILE"

echo ""
echo "== Step 5: verify the marker is back =="
if cypher "MATCH (m:BackupVerifyMarker {id: '${MARKER_ID}'}) RETURN m.id" | grep -q "$MARKER_ID"; then
  echo "marker restored: OK"
else
  echo "FAIL: marker was not restored -- the backup/restore cycle is broken" >&2
  exit 1
fi

echo ""
echo "== Step 6: clean up the marker =="
cypher "MATCH (m:BackupVerifyMarker) DETACH DELETE m"

echo ""
echo "== backup/restore round trip verified =="
