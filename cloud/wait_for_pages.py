#!/usr/bin/env python3
"""
等待 GitHub Pages 部署完成。
用法：python wait_for_pages.py <owner/repo> <commit_sha> [--token <GITHUB_TOKEN>]
"""
import json, os, sys, time, urllib.request, urllib.error


def api_get(url, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "okr-weekly-waiter",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"HTTP {e.code}: {err[:500]}", file=sys.stderr)
        return None


def main():
    if len(sys.argv) < 3:
        print("Usage: python wait_for_pages.py <owner/repo> <commit_sha> [--token <token>]", file=sys.stderr)
        sys.exit(2)

    repo = sys.argv[1]
    target_sha = sys.argv[2]
    token = None
    if "--token" in sys.argv:
        idx = sys.argv.index("--token")
        token = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN not provided", file=sys.stderr)
        sys.exit(2)

    url = f"https://api.github.com/repos/{repo}/pages/deployments?per_page=5"
    print(f"Waiting for Pages deployment of {repo}@{target_sha[:8]}...")

    for attempt in range(1, 31):
        deployments = api_get(url, token)
        if not isinstance(deployments, list):
            print(f"Attempt {attempt}: invalid response, retrying...")
            time.sleep(10)
            continue

        status = "not_found"
        for d in deployments:
            if d.get("sha") == target_sha:
                status = d.get("status", "unknown")
                break

        print(f"Attempt {attempt}: Pages deployment status = {status}")
        if status == "success":
            print("Pages deployment succeeded")
            sys.exit(0)
        elif status == "errored":
            print("Pages deployment failed", file=sys.stderr)
            sys.exit(1)
        time.sleep(10)

    print("Timeout waiting for Pages deployment", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
