"""Fail if the Hermes API key (read from ~/.hermes/.env, never printed) appears in tracked files or history."""
import subprocess
import sys
from pathlib import Path

key = ""
for line in (Path.home() / ".hermes" / ".env").read_text().splitlines():
    if line.strip().startswith("API_SERVER_KEY="):
        key = line.split("=", 1)[1].strip().strip('"').strip("'")
if len(key) < 8:
    sys.exit("no key to check")
tracked = subprocess.run(["git", "grep", "-l", "-F", key], check=False, capture_output=True, text=True)
history = subprocess.run(["git", "log", "-p", "--all", "-S", key, "--format=%h"], check=False, capture_output=True, text=True)
work = subprocess.run(["grep", "-rlF", key, "docs/evidence/hermes-analyst"], check=False, capture_output=True, text=True)
hits = [x for x in (tracked.stdout + history.stdout + work.stdout).split() if x]
print("key length", len(key), "| tracked hits:", tracked.stdout.count("\n"),
      "| history hits:", len(history.stdout.split()), "| evidence dir hits:", work.stdout.count("\n"))
sys.exit(1 if hits else 0)
