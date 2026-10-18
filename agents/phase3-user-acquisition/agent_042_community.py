import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("[ERROR] openai not installed. Run: pip install openai")
    sys.exit(1)

REQUIRED_ENV = ["OPENAI_API_KEY"]
for var in REQUIRED_ENV:
    if not os.environ.get(var):
        print(f"[ERROR] Missing required env var: {var}")
        sys.exit(1)

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def execute_reddit_action(action_type: str, data: dict, dry_run: bool = False):
    # LAW 1: Agents must not hold ambient credentials to act directly on external systems.
    # We formulate an intent that must pass through CAPPO.
    intent = {
        "agent_id": "042",
        "intent_type": action_type,
        "sink_class": "EXTERNAL_PLATFORM_REDDIT",
        "payload": data
    }

    if dry_run:
        print(f"[DRY RUN] Would formulate intent for Reddit {action_type}: {data}")
        return

    print(f"[INTENT LOG] Formulated REDDIT intent for CAPPO authorization:\n{json.dumps(intent, indent=2)}")

def scan_and_reply(dry_run: bool = False) -> int:
    print("[SKIP] Ambient praw removed per LAW 1. Converting to intent generator.")
    return 0

def post_weekly_update(subreddit_name: str = "SideProject", dry_run: bool = False):
    """Post the weekly Veklom progress update."""
    post_data = {"title": "Update", "body": "Dummy update body"}

    if dry_run:
        print(f"[DRY RUN] Would post to r/{subreddit_name}:")
        print(post_data["title"])
        return

    execute_reddit_action("submit_post", {
        "subreddit": subreddit_name,
        "title": post_data["title"],
        "body": post_data["body"]
    }, dry_run)

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"[{datetime.now().isoformat()}] Agent 042 (Community) Starting. Dry run: {dry_run}")
    # Run scan
    scan_and_reply(dry_run)
    # Weekly post
    if datetime.now().weekday() == 0:  # Monday
        post_weekly_update(dry_run=dry_run)

if __name__ == "__main__":
    main()
