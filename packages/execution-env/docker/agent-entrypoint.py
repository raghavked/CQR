"""
agent-entrypoint.py — CQR container runtime entrypoint.
Bootstraps the agent workspace: injects env vars and initialises git.
"""
import os
import subprocess
import sys


def main() -> None:
    """Bootstrap the CQR agent workspace."""
    # 1. Inject real env values from vault
    subprocess.run(["/cqr/inject-env.sh"], check=False)

    # 2. Ensure /workspace is a git repo
    workspace = "/workspace"
    git_dir = os.path.join(workspace, ".git")
    if not os.path.exists(git_dir):
        subprocess.run(["git", "init", workspace], check=False)
        print("[cqr] Initialised git repository at /workspace")

    # 3. Ensure scratch space exists
    os.makedirs("/tmp/agent-scratch", exist_ok=True)

    print("[cqr] Agent workspace ready")
    sys.exit(0)


if __name__ == "__main__":
    main()
