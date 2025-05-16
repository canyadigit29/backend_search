import os
import logging
import requests
from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

@router.get("/github/repos")
def list_github_repos():
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GitHub token not configured.")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    # Fetch all repositories (handles pagination)
    repos = []
    page = 1
    per_page = 100
    while True:
        url = f"https://api.github.com/user/repos?per_page={per_page}&page={page}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error(f"GitHub API error: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=500, detail="Failed to fetch repositories from GitHub.")
        batch = resp.json()
        repos.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    repos = response.json()
    return [{
        "name": repo["name"],
        "full_name": repo["full_name"],
        "private": repo["private"]
    } for repo in repos]
