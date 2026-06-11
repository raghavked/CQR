#!/usr/bin/env bash
# inject-env.sh — Called at container start to inject real .env values.
# This script is read-only and cannot be modified by agents.
# Real secret values are passed via environment variables set by the CQR runtime.
# Agents only see key names in /workspace/.env.keys — never values.

set -euo pipefail

ENV_KEYS_FILE="/workspace/.env.keys"

if [[ ! -f "$ENV_KEYS_FILE" ]]; then
    echo "[cqr] No .env.keys file found — skipping env injection"
    exit 0
fi

echo "[cqr] Injecting environment variables from vault..."
while IFS= read -r key; do
    # Key names are passed as CQR_SECRET_<KEY> env vars by the execution layer
    env_var_name="CQR_SECRET_${key}"
    if [[ -n "${!env_var_name:-}" ]]; then
        export "${key}=${!env_var_name}"
        echo "[cqr] Injected: ${key}"
    fi
done < "$ENV_KEYS_FILE"

echo "[cqr] Env injection complete"
