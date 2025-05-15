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
