#!/bin/bash

# Sourcing must not execute the launcher or modify the caller shell.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    echo "Info: launcher was sourced; no action taken."
    return 0
fi

set -euo pipefail

if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <sidecar_path> <run_id> <target_done> <max_claims>"
    echo "Example: $0 data/semantic_tagger_v4_probe.local.sqlite3 f1fba19b-686f-4355-aed7-fafc3ab4e30d 1 1"
    exit 1
fi

# 7. Use absolute paths
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
SIDECAR_PATH="$(readlink -f "$1")"

RUN_ID="$2"
TARGET_DONE="$3"
MAX_CLAIMS="$4"

# 8. Deterministic service name
SERVICE_NAME="mnemosyne-worker-${RUN_ID}"

# 6. Validate before launch
if [ ! -f "$SIDECAR_PATH" ]; then
    echo "Error: Sidecar $SIDECAR_PATH does not exist."
    echo "Do not use this script to create sidecars."
    exit 1
fi

if ! [[ "$TARGET_DONE" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: target_done must be a positive integer, got: $TARGET_DONE"
    exit 1
fi

if ! [[ "$MAX_CLAIMS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: max_claims must be a positive integer, got: $MAX_CLAIMS"
    exit 1
fi

if systemctl --user is-active --quiet "$SERVICE_NAME"; then
    echo "Error: Active systemd worker already exists for run ID $RUN_ID ($SERVICE_NAME)"
    exit 1
fi

# 1. & 6. Sidecar verification through Python sqlite3 module
echo "Verifying sidecar with Python..."
VERIFY_OUTPUT=$("$PYTHON_BIN" -c "
import sqlite3
import sys

db_path = sys.argv[1]
run_id = sys.argv[2]

try:
    conn = sqlite3.connect(\"file:\" + db_path + \"?mode=ro\", uri=True)
    conn.row_factory = sqlite3.Row
    
    # Check schema_version in sidecar_meta table
    cur_meta = conn.execute(\"SELECT value FROM sidecar_meta WHERE key = 'schema_version'\")
    meta_row = cur_meta.fetchone()
    if not meta_row:
        print('ERROR: sidecar DB schema_version not found in sidecar_meta.')
        sys.exit(1)
        
    user_version = int(meta_row[0])
    if user_version != 4:
        print(f'ERROR: sidecar DB schema version is {user_version}, expected 4')
        sys.exit(1)
        
    cur = conn.execute(\"SELECT model_name, schema_version FROM tagging_run WHERE run_id = ?\", (run_id,))
    row = cur.fetchone()
    if not row:
        print(f'ERROR: Run ID {run_id} does not exist in sidecar.')
        sys.exit(1)
        
    model_name = row['model_name']
    schema_version = row['schema_version']
    
    if model_name != 'qwen3:14b':
        print(f'ERROR: Expected model_name qwen3:14b, got {model_name}')
        sys.exit(1)
        
    if schema_version != 'semantic-tags-v3':
        print(f'ERROR: Expected schema_version semantic-tags-v3, got {schema_version}')
        sys.exit(1)

    print(model_name)
    sys.exit(0)
except Exception as e:
    print(f'ERROR: {str(e)}')
    sys.exit(1)
" "$SIDECAR_PATH" "$RUN_ID")

if [[ "$VERIFY_OUTPUT" == ERROR:* ]]; then
    echo "$VERIFY_OUTPUT"
    exit 1
fi

# The model name successfully validated above
MODEL_NAME="$VERIFY_OUTPUT"

# 4. Recover expired leases through Python API (JobStore)
if [[ "${MNEMOSYNE_DRY_RUN:-0}" != "1" ]]; then
    echo "Recovering expired leases..."
    "$PYTHON_BIN" -c "
import sys
from pathlib import Path
from scripts.semantic_tagger.job_store import JobStore
store = JobStore(Path(sys.argv[1]))
store.recover_expired_leases(sys.argv[2])
" "$SIDECAR_PATH" "$RUN_ID"
else
    echo "[DRY-RUN] Skipping lease recovery."
fi

# 9. Print exact commands
echo "======================================================"
echo "Starting semantic batch processing"
echo "Sidecar: $SIDECAR_PATH"
echo "Run ID: $RUN_ID"
echo "Model: $MODEL_NAME"
echo "Target Done: $TARGET_DONE"
echo "Max Claims: $MAX_CLAIMS"
echo "Service Name: $SERVICE_NAME"
echo "======================================================"
echo "To view logs:"
echo "  journalctl --user -u $SERVICE_NAME -f"
echo "To view status:"
echo "  $PYTHON_BIN -m scripts.semantic_tagger.cli status --db-path $SIDECAR_PATH"
echo "To stop the worker:"
echo "  systemctl --user stop $SERVICE_NAME"
echo "To resume:"
echo "  bash $0 $SIDECAR_PATH $RUN_ID $TARGET_DONE $MAX_CLAIMS"
echo "======================================================"

SYSTEMD_CMD=(
    systemd-run --user
    --unit="$SERVICE_NAME"
    --collect
    --service-type=exec
    -p WorkingDirectory="$REPO_ROOT"
    -p MemoryMax=12G
    -p MemorySwapMax=2G
    -p CPUWeight=30
    -p IOWeight=30
    "$PYTHON_BIN" -m scripts.semantic_tagger.cli worker
    --db-path "$SIDECAR_PATH"
    --run-id "$RUN_ID"
    --target-done "$TARGET_DONE"
    --max-claims "$MAX_CLAIMS"
    --model "$MODEL_NAME"
)

# 10. Dry-run mode
if [[ "${MNEMOSYNE_DRY_RUN:-0}" == "1" ]]; then
    echo "[DRY-RUN] Validation passed. Would execute:"
    echo "${SYSTEMD_CMD[@]}"
    exit 0
fi

echo "Executing systemd-run..."
exec "${SYSTEMD_CMD[@]}"
