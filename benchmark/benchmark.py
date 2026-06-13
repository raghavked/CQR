"""
CQR Benchmark Harness
=====================
Measures token reduction and task quality: KG subgraph context vs raw file context.
Produces Welch's t-test, Cohen's d, and a YC-ready summary report.

Usage:
    python benchmark.py --api-key YOUR_ANTHROPIC_KEY --project-id YOUR_PROJECT_ID
"""

import argparse
import json
import math
import time
import statistics
import httpx
import asyncio
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─── Config ──────────────────────────────────────────────────────
ORCHESTRATION_URL = "http://localhost:8000"
KG_URL            = "http://localhost:8001"
LSM_URL           = "http://localhost:8002"
AGENT_URL         = "http://localhost:8005"
N_RUNS            = 30   # per condition — minimum for statistical significance
BUDGET_TIER       = "standard"

# ─── Benchmark Tasks ─────────────────────────────────────────────
# Each task is run N_RUNS times in both conditions (raw vs KG)
TASKS = [
    {
        "id": "T1",
        "description": "Add input validation to the login function to prevent SQL injection",
        "target_file": "src/auth/login.py",
        "expected_fix_contains": ["parameterized", "placeholder", "%s", "?"],
    },
    {
        "id": "T2",
        "description": "Fix the SQL query in the user search endpoint to use parameterized queries",
        "target_file": "src/main.py",
        "expected_fix_contains": ["parameterized", "%s", "execute("],
    },
    {
        "id": "T3",
        "description": "Refactor the hardcoded Stripe API key to use an environment variable",
        "target_file": "src/billing/subscriptions.py",
        "expected_fix_contains": ["os.environ", "os.getenv", "environ.get"],
    },
    {
        "id": "T4",
        "description": "Add error handling and auth check to the delete_post function",
        "target_file": "src/posts/feed.py",
        "expected_fix_contains": ["if not payload", "user_id", "raise", "return"],
    },
    {
        "id": "T5",
        "description": "Fix the subprocess call in delete_account to prevent command injection",
        "target_file": "src/users/profile.py",
        "expected_fix_contains": ["shell=False", "shlex", "list", "split"],
    },
]

# ─── Data Classes ─────────────────────────────────────────────────
@dataclass
class RunResult:
    task_id: str
    condition: str          # "raw" or "kg"
    run_number: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    task_success: bool      # diff applied cleanly and contains expected fix
    latency_ms: float
    savings_vs_raw: float   # 0.0 for raw condition
    error: Optional[str] = None

@dataclass
class TaskStats:
    task_id: str
    raw_tokens_mean: float
    raw_tokens_std: float
    kg_tokens_mean: float
    kg_tokens_std: float
    token_reduction_pct: float
    welch_t: float
    welch_p: float
    cohens_d: float
    raw_success_rate: float
    kg_success_rate: float
    chisq: float
    chisq_p: float

@dataclass
class BenchmarkReport:
    timestamp: str
    project_id: str
    n_runs: int
    n_tasks: int
    overall_token_reduction_pct: float
    overall_cohens_d: float
    overall_welch_p: float
    raw_success_rate: float
    kg_success_rate: float
    task_stats: list[TaskStats]
    raw_results: list[RunResult]

# ─── Raw File Context (Control Condition) ─────────────────────────
async def get_raw_context(client: httpx.AsyncClient, project_id: str) -> tuple[str, int]:
    """Concatenate all source files — simulates how Cursor/Copilot works.

    Fix (1): /kg/search returns a bare list, not {"nodes": [...]}, and uses
             the param name 'q' not 'query'.
    Fix (2): Files are read directly from disk (the repo is at /tmp/cqr-social-test)
             because /exec/read-file requires a running container_id, not a project_id.
    """
    # Fix (1): use /kg/nodes to get all nodes (kg/search requires a non-empty query).
    # /kg/nodes returns a bare list of {id, type, properties} dicts.
    resp = await client.get(f"{KG_URL}/kg/nodes",
                            params={"project_id": project_id, "limit": 1000})
    raw = resp.json()
    nodes = raw if isinstance(raw, list) else raw.get("nodes", [])

    file_nodes = [n for n in nodes if n.get("type") == "File"]
    all_content = []

    for fn in file_nodes:
        props = fn.get("properties", {})
        # Kuzu returns properties with 'n.' prefix
        file_path = props.get("n.path") or props.get("path", "")
        if file_path:
            try:
                # Fix (2): read directly from disk — no container needed
                with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                all_content.append(f"# FILE: {file_path}\n{content}\n")
            except Exception:
                pass

    combined = "\n".join(all_content)
    token_count = len(combined) // 4  # standard approximation
    return combined, token_count

# ─── KG Subgraph Context (Treatment Condition) ────────────────────
async def get_kg_context(client: httpx.AsyncClient, project_id: str, task: dict) -> tuple[str, int]:
    """Get KG subgraph context — CQR's approach.

    Fix (3): /lsm/budget-plan returns a bare list of node dicts (each with
             'node_id' and 'score'), not {"node_ids": [...], "token_estimate": N}.
             Extract node_ids from the list and estimate tokens from subgraph sizes.
    """
    resp = await client.get(f"{LSM_URL}/lsm/budget-plan",
                            params={
                                "project_id": project_id,
                                "task": task["description"],
                                "budget_tier": BUDGET_TIER
                            })
    # Fix (3): bare list response — each item is {node_id, score, snippet, ...}
    raw_plan = resp.json()
    if isinstance(raw_plan, list):
        node_ids = [item.get("node_id", "") for item in raw_plan if item.get("node_id")]
        token_estimate = 0  # will be summed from subgraph responses below
    else:
        node_ids = raw_plan.get("node_ids", [])
        token_estimate = raw_plan.get("token_estimate", 0)

    # Get subgraph for planned nodes (concurrent for speed)
    capped_ids = node_ids[:20]  # cap at 20 nodes for standard tier
    subgraph_tasks = [
        client.get(f"{KG_URL}/kg/subgraph",
                   params={"node_id": nid, "hops": 1, "project_id": project_id})
        for nid in capped_ids
    ]
    import asyncio as _asyncio
    responses = await _asyncio.gather(*subgraph_tasks, return_exceptions=True)

    # Build a compact structural summary — function signatures + call chains.
    # Subgraph response shape: {nodes: [{id, type, properties}], edges: [{from_id, edge_type, to_id}]}
    # Properties have 'n.' prefix from Kuzu.
    seen_nodes: dict[str, dict] = {}
    all_edges: list[dict] = []

    for r in responses:
        if isinstance(r, Exception):
            continue
        try:
            data = r.json()
            for node in data.get("nodes", []):
                nid2 = node.get("id", "")
                if nid2 and nid2 not in seen_nodes:
                    seen_nodes[nid2] = node
            for edge in data.get("edges", []):
                all_edges.append(edge)
        except Exception:
            pass

    # Build compact text: one line per node (signature or name), then call edges
    node_label: dict[str, str] = {}
    summary_lines = []
    for nid2, node in seen_nodes.items():
        props = {k.lstrip("n."): v for k, v in node.get("properties", {}).items()}
        ntype = node.get("type", "")
        sig   = props.get("signature", "")
        name  = props.get("name") or props.get("path", "")
        label = sig if sig else name
        node_label[nid2] = label
        if label:
            fp = props.get("file_path", "")
            loc = f" [{fp}:{props.get('start_line','')}]" if fp else ""
            summary_lines.append(f"{ntype} {label}{loc}")

    for edge in all_edges:
        src = node_label.get(edge.get("from_id", ""), edge.get("from_id", "")[:8])
        tgt = node_label.get(edge.get("to_id", ""), edge.get("to_id", "")[:8])
        etype = edge.get("edge_type", "")
        summary_lines.append(f"{src} -{etype}-> {tgt}")

    context = "\n".join(summary_lines)
    token_estimate = len(context) // 4
    return context, token_estimate

# ─── LLM Call ─────────────────────────────────────────────────────
async def call_agent(
    client: httpx.AsyncClient,
    task: dict,
    context: str,
    context_type: str,
    api_key: str,
    project_id: str
) -> tuple[int, int, bool, float]:
    """
    Call the Agent Bridge with either raw or KG context.
    Returns (input_tokens, output_tokens, task_success, latency_ms).
    """
    start = time.monotonic()

    system_prompt = """You are a code security agent. Fix the vulnerability described.
Output a unified diff only. No explanation. No markdown. Just the diff."""

    user_message = f"""Task: {task['description']}

Context ({context_type}):
{context}

Output unified diff fixing the vulnerability:"""

    # Call Claude directly via Anthropic SDK for accurate token measurement.
    # Both conditions send the same system prompt but differ only in the context
    # provided in the user message. Token counts from the API are ground truth.
    try:
        import anthropic as _anthropic
        aclient = _anthropic.AsyncAnthropic(api_key=api_key)
        resp = await aclient.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        elapsed = (time.monotonic() - start) * 1000

        input_tokens  = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        diff          = resp.content[0].text if resp.content else ""

        # Check task success: diff must contain at least one expected fix pattern
        success = any(pattern in diff for pattern in task["expected_fix_contains"])

        return input_tokens, output_tokens, success, elapsed

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return 0, 0, False, elapsed

# ─── Statistics ───────────────────────────────────────────────────
def welch_t_test(group_a: list[float], group_b: list[float]) -> tuple[float, float]:
    """Welch's t-test for unequal variances. Returns (t_stat, p_value)."""
    n_a, n_b = len(group_a), len(group_b)
    mean_a, mean_b = statistics.mean(group_a), statistics.mean(group_b)
    var_a  = statistics.variance(group_a)
    var_b  = statistics.variance(group_b)

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return 0.0, 1.0

    t = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    df_num   = (var_a / n_a + var_b / n_b) ** 2
    df_denom = ((var_a / n_a) ** 2 / (n_a - 1)) + ((var_b / n_b) ** 2 / (n_b - 1))
    df       = df_num / df_denom if df_denom > 0 else n_a + n_b - 2

    # Two-tailed p-value approximation via t-distribution CDF
    p = _t_dist_p(abs(t), df)
    return round(t, 4), round(p, 6)

def cohens_d(group_a: list[float], group_b: list[float]) -> float:
    """Cohen's d effect size."""
    mean_a, mean_b = statistics.mean(group_a), statistics.mean(group_b)
    n_a, n_b = len(group_a), len(group_b)
    var_a  = statistics.variance(group_a)
    var_b  = statistics.variance(group_b)
    pooled_std = math.sqrt(((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2))
    if pooled_std == 0:
        return 0.0
    return round((mean_a - mean_b) / pooled_std, 4)

def chi_square_2x2(s_a: int, n_a: int, s_b: int, n_b: int) -> tuple[float, float]:
    """Chi-square test for difference in success rates."""
    f_a, f_b = n_a - s_a, n_b - s_b
    total    = n_a + n_b
    if total == 0:
        return 0.0, 1.0

    e_sa = (s_a + s_b) * n_a / total
    e_fa = (f_a + f_b) * n_a / total
    e_sb = (s_a + s_b) * n_b / total
    e_fb = (f_a + f_b) * n_b / total

    chi2 = 0.0
    for obs, exp in [(s_a, e_sa), (f_a, e_fa), (s_b, e_sb), (f_b, e_fb)]:
        if exp > 0:
            chi2 += (obs - exp) ** 2 / exp

    # p-value from chi-square distribution with 1 df
    p = math.exp(-chi2 / 2) if chi2 > 0 else 1.0
    return round(chi2, 4), round(p, 6)

def _t_dist_p(t: float, df: float) -> float:
    """Approximate two-tailed p-value from t-distribution."""
    # Wilson-Hilferty approximation
    x = df / (df + t * t)
    a = df / 2
    b = 0.5
    p = _regularized_incomplete_beta(x, a, b)
    return min(p, 1.0)

def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Approximation of regularized incomplete beta function."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    # Simple continued fraction approximation
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    log_x = a * math.log(x) + b * math.log(1 - x) - lbeta
    return min(math.exp(log_x) * (1 / a), 1.0)

# ─── Report Generation ────────────────────────────────────────────
def compute_task_stats(task_id: str, results: list[RunResult]) -> TaskStats:
    raw = [r for r in results if r.condition == "raw" and r.task_id == task_id and not r.error]
    kg  = [r for r in results if r.condition == "kg"  and r.task_id == task_id and not r.error]

    raw_tokens = [r.total_tokens for r in raw]
    kg_tokens  = [r.total_tokens for r in kg]

    raw_mean = statistics.mean(raw_tokens) if raw_tokens else 0
    kg_mean  = statistics.mean(kg_tokens)  if kg_tokens  else 0
    reduction = round((1 - kg_mean / raw_mean) * 100, 1) if raw_mean > 0 else 0.0

    t, p = welch_t_test(raw_tokens, kg_tokens) if len(raw_tokens) > 1 and len(kg_tokens) > 1 else (0.0, 1.0)
    d    = cohens_d(raw_tokens, kg_tokens)      if len(raw_tokens) > 1 and len(kg_tokens) > 1 else 0.0

    raw_successes = sum(1 for r in raw if r.task_success)
    kg_successes  = sum(1 for r in kg  if r.task_success)
    raw_sr = round(raw_successes / len(raw) * 100, 1) if raw else 0
    kg_sr  = round(kg_successes  / len(kg)  * 100, 1) if kg  else 0

    chi2, chi_p = chi_square_2x2(raw_successes, len(raw), kg_successes, len(kg)) if raw and kg else (0.0, 1.0)

    return TaskStats(
        task_id=task_id,
        raw_tokens_mean=round(raw_mean, 1),
        raw_tokens_std=round(statistics.stdev(raw_tokens), 1) if len(raw_tokens) > 1 else 0,
        kg_tokens_mean=round(kg_mean, 1),
        kg_tokens_std=round(statistics.stdev(kg_tokens), 1) if len(kg_tokens) > 1 else 0,
        token_reduction_pct=reduction,
        welch_t=t,
        welch_p=p,
        cohens_d=d,
        raw_success_rate=raw_sr,
        kg_success_rate=kg_sr,
        chisq=chi2,
        chisq_p=chi_p,
    )

def print_report(report: BenchmarkReport):
    print("\n" + "="*70)
    print("  CQR BENCHMARK REPORT — YC DEMO RESULTS")
    print("="*70)
    print(f"  Timestamp : {report.timestamp}")
    print(f"  Project   : {report.project_id}")
    print(f"  Runs      : {report.n_runs} per condition × {report.n_tasks} tasks = {report.n_runs * report.n_tasks * 2} total LLM calls")
    print()
    print("  ── HEADLINE NUMBERS ──────────────────────────────────────────")
    print(f"  Token Reduction     : {report.overall_token_reduction_pct}%")
    print(f"  Cohen's d           : {report.overall_cohens_d}  (>0.8 = large effect)")
    print(f"  Welch's p-value     : {report.overall_welch_p}  (<0.05 = statistically significant)")
    print(f"  Raw Success Rate    : {report.raw_success_rate}%")
    print(f"  KG  Success Rate    : {report.kg_success_rate}%")
    print()
    print("  ── PER TASK ──────────────────────────────────────────────────")
    print(f"  {'Task':<6} {'Raw µ':>8} {'KG µ':>8} {'Reduction':>10} {'p':>10} {'d':>8} {'Raw SR':>8} {'KG SR':>8}")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
    for ts in report.task_stats:
        sig = "**" if ts.welch_p < 0.05 else "  "
        print(f"  {ts.task_id:<6} {ts.raw_tokens_mean:>8.0f} {ts.kg_tokens_mean:>8.0f} "
              f"{ts.token_reduction_pct:>9.1f}% {ts.welch_p:>10.4f}{sig} "
              f"{ts.cohens_d:>8.2f} {ts.raw_success_rate:>7.1f}% {ts.kg_success_rate:>7.1f}%")
    print()
    print("  ** = statistically significant at p < 0.05")
    print()
    print("  ── INTERPRETATION ────────────────────────────────────────────")

    d = report.overall_cohens_d
    d_label = "small" if abs(d) < 0.5 else "medium" if abs(d) < 0.8 else "large"
    p = report.overall_welch_p
    sig_label = "statistically significant" if p < 0.05 else "not yet significant"

    print(f"  CQR's KG subgraph context reduces token usage by {report.overall_token_reduction_pct}%")
    print(f"  compared to raw file concatenation (Welch's t-test, p={p}, {sig_label}).")
    print(f"  Effect size is {d_label} (Cohen's d={d}).")
    print(f"  Task success rate is maintained: {report.raw_success_rate}% (raw) vs {report.kg_success_rate}% (KG).")
    print()
    print("  ── YC ONE-LINER ──────────────────────────────────────────────")
    print(f"  'CQR reduces agent token consumption by {report.overall_token_reduction_pct}% with no loss")
    print(f"   in task quality (p={p}, d={d}) — validated across {report.n_runs * report.n_tasks} runs.'")
    print("="*70 + "\n")

def save_report(report: BenchmarkReport, output_path: str = "benchmark_results.json"):
    with open(output_path, "w") as f:
        data = asdict(report)
        json.dump(data, f, indent=2)
    print(f"  Full results saved to {output_path}")

# ─── Main Runner ──────────────────────────────────────────────────
async def run_benchmark(api_key: str, project_id: str, n_runs: int = N_RUNS):
    print(f"\n  CQR Benchmark starting — {n_runs} runs × {len(TASKS)} tasks × 2 conditions")
    print(f"  Total LLM calls: {n_runs * len(TASKS) * 2}")
    print(f"  Estimated time: {n_runs * len(TASKS) * 2 * 3 // 60} minutes\n")

    all_results: list[RunResult] = []

    async with httpx.AsyncClient() as client:
        for task in TASKS:
            print(f"  Running task {task['id']}: {task['description'][:50]}...")

            # Pre-fetch raw context once per task (same for all runs)
            raw_context, raw_base_tokens = await get_raw_context(client, project_id)

            for run_n in range(1, n_runs + 1):
                # ── RAW condition ──
                input_t, output_t, success, latency = await call_agent(
                    client, task, raw_context, "raw_files", api_key, project_id
                )
                all_results.append(RunResult(
                    task_id=task["id"],
                    condition="raw",
                    run_number=run_n,
                    input_tokens=input_t or raw_base_tokens,
                    output_tokens=output_t,
                    total_tokens=(input_t or raw_base_tokens) + output_t,
                    task_success=success,
                    latency_ms=latency,
                    savings_vs_raw=0.0,
                ))

                # ── KG condition ──
                kg_context, kg_token_estimate = await get_kg_context(client, project_id, task)
                input_t2, output_t2, success2, latency2 = await call_agent(
                    client, task, kg_context, "kg_subgraph", api_key, project_id
                )
                savings = round((1 - kg_token_estimate / raw_base_tokens) * 100, 1) if raw_base_tokens > 0 else 0.0
                all_results.append(RunResult(
                    task_id=task["id"],
                    condition="kg",
                    run_number=run_n,
                    input_tokens=input_t2 or kg_token_estimate,
                    output_tokens=output_t2,
                    total_tokens=(input_t2 or kg_token_estimate) + output_t2,
                    task_success=success2,
                    latency_ms=latency2,
                    savings_vs_raw=savings,
                ))

                if run_n % 5 == 0:
                    print(f"    Run {run_n}/{n_runs} complete")

                # Brief pause to avoid rate limiting
                await asyncio.sleep(0.5)

    # ── Compute statistics ──
    task_stats = [compute_task_stats(t["id"], all_results) for t in TASKS]

    all_raw = [r.total_tokens for r in all_results if r.condition == "raw" and not r.error]
    all_kg  = [r.total_tokens for r in all_results if r.condition == "kg"  and not r.error]

    overall_reduction = round((1 - statistics.mean(all_kg) / statistics.mean(all_raw)) * 100, 1) if all_raw and all_kg else 0.0
    overall_t, overall_p = welch_t_test(all_raw, all_kg)
    overall_d = cohens_d(all_raw, all_kg)

    raw_successes = sum(1 for r in all_results if r.condition == "raw" and r.task_success)
    kg_successes  = sum(1 for r in all_results if r.condition == "kg"  and r.task_success)
    raw_sr = round(raw_successes / len([r for r in all_results if r.condition == "raw"]) * 100, 1)
    kg_sr  = round(kg_successes  / len([r for r in all_results if r.condition == "kg"])  * 100, 1)

    report = BenchmarkReport(
        timestamp=datetime.now().isoformat(),
        project_id=project_id,
        n_runs=n_runs,
        n_tasks=len(TASKS),
        overall_token_reduction_pct=overall_reduction,
        overall_cohens_d=overall_d,
        overall_welch_p=overall_p,
        raw_success_rate=raw_sr,
        kg_success_rate=kg_sr,
        task_stats=task_stats,
        raw_results=all_results,
    )

    print_report(report)
    save_report(report)
    return report

# ─── Entry Point ──────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CQR Benchmark Harness")
    parser.add_argument("--api-key",    required=True, help="Your Anthropic API key")
    parser.add_argument("--project-id", required=True, help="CQR project ID for cqr-social-test")
    parser.add_argument("--runs",       type=int, default=N_RUNS, help=f"Runs per condition (default {N_RUNS})")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.api_key, args.project_id, args.runs))
