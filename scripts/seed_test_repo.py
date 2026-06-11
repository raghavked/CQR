"""
Seed a synthetic Python project for integration testing.
Generates files that trigger all major CQR components:
- Inter-file imports (KG edges)
- Hardcoded credential (Security scanner)
- Env variable read (Vault / EnvRef)
- SQL injection path (Security scanner)
"""
import os
from pathlib import Path


def main() -> None:
    """Generate the test repository."""
    base_dir = Path("/tmp/cqr-test-repo")
    base_dir.mkdir(parents=True, exist_ok=True)

    # 1. Main entrypoint (imports)
    (base_dir / "main.py").write_text("""
from db import get_user
from auth import login

def process_request(user_id):
    user = get_user(user_id)
    return login(user)
""")

    # 2. DB module (SQL injection)
    (base_dir / "db.py").write_text("""
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # Vulnerability: String concatenation in SQL
    cursor.execute("SELECT * FROM users WHERE id = '" + str(user_id) + "'")
    return cursor.fetchone()
""")

    # 3. Auth module (Hardcoded secret + EnvRef)
    (base_dir / "auth.py").write_text("""
import os

def login(user):
    # Vulnerability: Hardcoded secret
    api_key = "sk-1234567890abcdef1234567890abcdef"
    
    # Env reference
    master_token = os.environ.get("MASTER_TOKEN")
    
    if user and master_token:
        return True
    return False
""")

    print(f"Test repository seeded at {base_dir}")


if __name__ == "__main__":
    main()
