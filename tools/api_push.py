"""Push a local tree to GitHub through the REST Git Data API.

Why this exists
---------------
`git push` over https:443 fails on this machine ("Failed to connect to
github.com:443") while `gh api` works, so the git transport and the API go out
through different paths. Rather than fight the network, this script performs the
same operation the git protocol would: create blobs, build a tree, make a
commit, move the ref.

It creates ONE commit containing the whole tree, matching the local commit's
message, so the remote history looks like a normal first push.

Usage:
    python tools/api_push.py --repo linmoxi33/yaml12-bridge --branch main
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys

# Directories and files that must never be uploaded.
SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".whl", ".egg-info"}


def gh_api(method: str, path: str, payload: dict | None = None) -> dict:
    """Call the GitHub API through the `gh` CLI (which has working auth+network)."""
    cmd = ["gh", "api", "-X", method, path, "--input", "-"]
    proc = subprocess.run(
        cmd,
        input=json.dumps(payload or {}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{method} {path} failed: {proc.stderr.strip()[:500]}")
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def collect(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    files: list[tuple[str, pathlib.Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.name.endswith(".egg-info"):
            continue
        files.append((rel.as_posix(), path))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--root", default=".")
    ap.add_argument("--message-file", default=None)
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    files = collect(root)
    print(f"uploading {len(files)} files from {root}")

    # 0. A completely empty repo has no commit, and the Git Data API refuses to
    # create blobs/trees against it (HTTP 409 "Git Repository is empty"). Bootstrap
    # with one file through the Contents API, which creates the initial commit.
    try:
        gh_api("GET", f"repos/{args.repo}/contents/.gitattributes")
        print("repo already has content; reusing history")
        parents_needed = True
    except RuntimeError:
        first = root / ".gitattributes"
        if first.exists():
            payload = {
                "message": "chore: bootstrap repository",
                "content": base64.b64encode(first.read_bytes()).decode(),
                "branch": args.branch,
            }
            gh_api("PUT", f"repos/{args.repo}/contents/.gitattributes", payload)
            print("bootstrapped empty repo via Contents API")
        parents_needed = False

    # 1. blobs
    entries = []
    for index, (rel, path) in enumerate(files, 1):
        data = path.read_bytes()
        blob = gh_api(
            "POST",
            f"repos/{args.repo}/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if index % 10 == 0 or index == len(files):
            print(f"  blobs {index}/{len(files)}")

    # 2. tree
    tree = gh_api("POST", f"repos/{args.repo}/git/trees", {"tree": entries})
    print(f"tree {tree['sha'][:8]}")

    # 3. commit message: reuse the local one so history reads normally
    if args.message_file:
        message = pathlib.Path(args.message_file).read_text(encoding="utf-8")
    else:
        proc = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"], cwd=root, capture_output=True, text=True
        )
        message = proc.stdout.strip() or "Initial commit"

    # 4. commit
    payload: dict = {"message": message, "tree": tree["sha"]}
    if parents_needed:
        ref = gh_api("GET", f"repos/{args.repo}/git/ref/heads/{args.branch}")
        payload["parents"] = [ref["object"]["sha"]]
    commit = gh_api("POST", f"repos/{args.repo}/git/commits", payload)
    print(f"commit {commit['sha'][:8]}")

    # 5. move the branch
    gh_api(
        "PATCH",
        f"repos/{args.repo}/git/refs/heads/{args.branch}",
        {"sha": commit["sha"], "force": True},
    )
    print(f"refs/heads/{args.branch} -> {commit['sha'][:8]}")
    print(f"done: https://github.com/{args.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
