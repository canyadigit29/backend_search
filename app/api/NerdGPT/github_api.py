import os
import logging
import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

@router.get("/github/repos")
def list_github_repos():
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GitHub token not configured.")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    url = "https://api.github.com/user/repos"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        logger.error(f"GitHub API error: {response.status_code} - {response.text}")
        raise HTTPException(status_code=500, detail="Failed to fetch repositories from GitHub.")

    repos = response.json()
    return [{
        "name": repo["name"],
        "full_name": repo["full_name"],
        "private": repo["private"]
    } for repo in repos]

@router.get("/github/tree")
def get_github_tree(repo: str = Query(..., description="GitHub repo in format owner/repo")):
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GitHub token not configured.")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    # Step 1: Get default branch
    repo_url = f"https://api.github.com/repos/{repo}"
    repo_res = requests.get(repo_url, headers=headers)
    if repo_res.status_code != 200:
        raise HTTPException(status_code=404, detail=f"Repo not found: {repo}")
    default_branch = repo_res.json().get("default_branch", "main")

    # Step 2: Get full tree
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/{default_branch}?recursive=1"
    tree_res = requests.get(tree_url, headers=headers)
    if tree_res.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to load tree from GitHub")
    tree_data = tree_res.json()

    # Extract only file paths (not folders)
    return [item["path"] for item in tree_data.get("tree", []) if item["type"] == "blob"]
