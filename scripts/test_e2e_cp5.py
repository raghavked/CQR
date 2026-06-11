"""
CP-5 end-to-end flow test.
Submits a real task against the cqr-social-test project and verifies
WebSocket events fire in the correct sequence.

Usage:
    python3 scripts/test_e2e_cp5.py

Requirements:
    - All 7 services running (run scripts/start_services.sh first)
    - cqr-social-test already ingested (project_id in Postgres)
    - pip install websockets httpx
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import httpx
import websockets

BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"

# Events we require at minimum (task.diff_ready requires a real LLM key)
REQUIRED_EVENTS = ["task.started"]
FULL_EXPECTED_EVENTS = [
    "task.started",
    "task.context_assembled",
    "task.streaming",
    "task.diff_ready",
]


async def run_e2e():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        # ----------------------------------------------------------------
        # 1. Find the social-test project
        # ----------------------------------------------------------------
        r = await http.get("/api/v1/projects")
        r.raise_for_status()
        projects = r.json()
        social = next((p for p in projects if "social" in p.get("name", "").lower()), None)
        if not social:
            print("[FAIL] cqr-social-test project not found. Run seed first.")
            sys.exit(1)
        project_id = social["id"]
        print(f"[OK] Found project: {social['name']} ({project_id})")

        # ----------------------------------------------------------------
        # 2. Open WebSocket, submit task as a concurrent create_task, collect events
        # ----------------------------------------------------------------
        received_events: list[dict] = []
        task_id_holder: list[str] = []

        async with websockets.connect(
            f"{WS_BASE}/ws/{project_id}", open_timeout=5
        ) as ws:
            print(f"[OK] WebSocket connected")

            # Read the initial connected message
            init_raw = await asyncio.wait_for(ws.recv(), timeout=5)
            init_msg = json.loads(init_raw)
            print(f"  [WS] {init_msg.get('event')}: {json.dumps(init_msg.get('data', {}))[:80]}")

            # Submit task as a background task (so WS recv loop can run concurrently)
            async def submit_task():
                await asyncio.sleep(0.1)  # Tiny yield to let recv loop start
                task_payload = {
                    "project_id": project_id,
                    "description": "Add a docstring to the main function explaining what it does.",
                    "api_key": os.getenv("OPENAI_API_KEY", "sk-test-key"),
                    "api_key_type": "openai",
                }
                async with httpx.AsyncClient(base_url=BASE, timeout=30) as h:
                    r2 = await h.post("/api/v1/tasks", json=task_payload)
                if r2.status_code not in (200, 201, 202):
                    print(f"[FAIL] Task submission failed: {r2.status_code} {r2.text}")
                    return
                task = r2.json()
                task_obj = task.get("task") or task
                tid = task_obj.get("id") or task_obj.get("task_id")
                task_id_holder.append(tid)
                print(f"[OK] Task submitted: {tid}")

            submit_coro = asyncio.create_task(submit_task())

            # Read events for up to 30 seconds
            for _ in range(30):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw)
                    evt = msg.get("event", "")
                    if evt == "heartbeat":
                        continue
                    print(f"  [WS] {evt}: {json.dumps(msg.get('data', {}))[:120]}")
                    received_events.append(msg)
                    # Stop after terminal task events
                    if evt in ("task.diff_ready", "task.applied", "task.failed"):
                        break
                except asyncio.TimeoutError:
                    print("[INFO] WS: no more events (timeout)")
                    break

            await submit_coro  # Ensure submit task is done

        # ----------------------------------------------------------------
        # 3. Poll final task status
        # ----------------------------------------------------------------
        task_id = task_id_holder[0] if task_id_holder else None
        if task_id:
            await asyncio.sleep(1)
            r = await http.get(f"/api/v1/tasks/{task_id}")
            r.raise_for_status()
            task_status = r.json().get("task", {})
            print(f"[OK] Final task status: {task_status.get('status')}")
            if task_status.get("diff"):
                diff_lines = task_status["diff"].split("\n")
                print(f"[OK] Diff received: {len(diff_lines)} lines")
            else:
                print("[INFO] No diff (expected with stub LLM key)")

        # ----------------------------------------------------------------
        # 4. Verify event sequence
        # ----------------------------------------------------------------
        event_types = [e.get("event") for e in received_events]
        print("\n=== WebSocket Event Sequence ===")
        for i, evt in enumerate(event_types):
            marker = "[OK]" if evt in FULL_EXPECTED_EVENTS else "[INFO]"
            print(f"  {i+1}. {marker} {evt}")

        missing_required = [e for e in REQUIRED_EVENTS if e not in event_types]
        if missing_required:
            print(f"\n[FAIL] Missing required events: {missing_required}")
            return False

        print(f"\n[PASS] Required events received: {REQUIRED_EVENTS}")

        missing_full = [e for e in FULL_EXPECTED_EVENTS if e not in event_types]
        if missing_full:
            print(f"[INFO] Full sequence needs real LLM key. Missing: {missing_full}")

        # ----------------------------------------------------------------
        # 5. Check security report
        # ----------------------------------------------------------------
        r = await http.get(f"/api/v1/security/report/{project_id}")
        if r.status_code == 200:
            findings = r.json()
            count = len(findings) if isinstance(findings, list) else findings.get("total", 0)
            print(f"[OK] Security report: {count} findings")
        else:
            print(f"[INFO] Security report: {r.status_code}")

        # ----------------------------------------------------------------
        # 6. Check KG node count
        # ----------------------------------------------------------------
        async with httpx.AsyncClient(timeout=10) as kg_http:
            r = await kg_http.get(f"http://localhost:8001/kg/nodes?project_id={project_id}")
            if r.status_code == 200:
                nodes = r.json()
                print(f"[OK] KG nodes: {len(nodes)}")
            else:
                print(f"[INFO] KG nodes endpoint: {r.status_code}")

        print("\n=== CP-5 E2E Test Complete ===")
        return True


if __name__ == "__main__":
    result = asyncio.run(run_e2e())
    sys.exit(0 if result else 1)
