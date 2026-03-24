"""
Repository Adapter - Multi-platform Git hosting abstraction.

Provides a unified interface for interacting with different Git hosting platforms
(GitHub, GitLab, Bitbucket) through a common RepositoryAdapter abstraction.

Master Prompt Reference: Section 4.2 - RepositoryAdapter
"""

from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass
import base64
from urllib.parse import quote
import httpx


@dataclass
class FileChange:
    """Represents a file change to be committed."""
    path: str
    content: str
    operation: str = "update"  # "create", "update", "delete"


@dataclass
class CommitInfo:
    """Git commit information."""
    sha: str
    message: str
    author: str
    date: str
    files: list[str]


@dataclass
class SearchResult:
    """Code search result."""
    repo: str
    path: str
    content: str
    line_number: Optional[int] = None


class RepositoryAdapter(ABC):
    """
    Abstract adapter for different Git hosting platforms.
    
    NEW in v6.0 — enables SAFS to work with repos hosted on GitHub,
    GitLab, Bitbucket, or any Git-compatible platform.
    
    Master Prompt Rule #22:
    All source operations MUST go through RepositoryAdapter.
    """

    @abstractmethod
    async def get_file(self, repo: str, path: str, ref: str = "main") -> str:
        """
        Retrieve file contents from a repository.
        
        Args:
            repo: Repository identifier (e.g., "owner/name")
            path: File path within repository
            ref: Git ref (branch, tag, commit SHA)
        
        Returns:
            File contents as string
        """
        pass

    @abstractmethod
    async def search_code(
        self, query: str, language: Optional[str] = None
    ) -> list[SearchResult]:
        """
        Search for code across repositories.
        
        Args:
            query: Search query string
            language: Optional language filter
        
        Returns:
            List of search results
        """
        pass

    @abstractmethod
    async def list_commits(
        self,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[CommitInfo]:
        """
        List commits for a repository or file.
        
        Args:
            repo: Repository identifier
            path: Optional file path filter
            since: Optional ISO8601 timestamp
        
        Returns:
            List of commit information
        """
        pass

    @abstractmethod
    async def create_branch(
        self, repo: str, branch: str, from_ref: str
    ) -> str:
        """
        Create a new branch.
        
        Args:
            repo: Repository identifier
            branch: New branch name
            from_ref: Source branch/commit
        
        Returns:
            Created branch name
        """
        pass

    @abstractmethod
    async def push_files(
        self, repo: str, branch: str, files: list[FileChange]
    ) -> str:
        """
        Push file changes to a branch.
        
        Args:
            repo: Repository identifier
            branch: Target branch
            files: List of file changes
        
        Returns:
            Commit SHA
        """
        pass

    @abstractmethod
    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> str:
        """
        Create a pull request.
        
        Args:
            repo: Repository identifier
            title: PR title
            body: PR description
            head: Source branch
            base: Target branch
            draft: Create as draft PR (default: True per Master Prompt Rule #7)
        
        Returns:
            Pull request URL
        """
        pass


class GitHubMCPAdapter(RepositoryAdapter):
    """
    Wraps GitHub MCP for repos hosted on GitHub.
    
    Delegates all operations to GitHub MCP tools via MCP protocol.
    """

    def __init__(self, github_mcp_client):
        """
        Initialize GitHub adapter.
        
        Args:
            github_mcp_client: MCP client for GitHub server
        """
        self.github_mcp = github_mcp_client

    async def get_file(self, repo: str, path: str, ref: str = "main") -> str:
        """Get file contents via GitHub MCP."""
        owner, name = repo.split("/")
        result = await self.github_mcp.call(
            "get_file_contents",
            owner=owner,
            repo=name,
            path=path,
            ref=ref,
        )
        return result.get("content", "")

    async def search_code(
        self, query: str, language: Optional[str] = None
    ) -> list[SearchResult]:
        """Search code via GitHub MCP."""
        q = query
        if language:
            q += f" language:{language}"
        
        results = await self.github_mcp.call("search_code", q=q)
        
        search_results = []
        for item in results.get("items", []):
            search_results.append(
                SearchResult(
                    repo=item["repository"]["full_name"],
                    path=item["path"],
                    content=item.get("text_matches", [{}])[0].get(
                        "fragment", ""
                    )
                    if item.get("text_matches")
                    else "",
                    line_number=None,  # GitHub search doesn't return line numbers
                )
            )
        
        return search_results

    async def list_commits(
        self,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[CommitInfo]:
        """List commits via GitHub MCP."""
        owner, name = repo.split("/")
        
        params = {"owner": owner, "repo": name}
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        
        result = await self.github_mcp.call("list_commits", **params)
        
        commits = []
        for commit in result.get("commits", []):
            commits.append(
                CommitInfo(
                    sha=commit["sha"],
                    message=commit["commit"]["message"],
                    author=commit["commit"]["author"]["name"],
                    date=commit["commit"]["author"]["date"],
                    files=[f["filename"] for f in commit.get("files", [])],
                )
            )
        
        return commits

    async def create_branch(
        self, repo: str, branch: str, from_ref: str
    ) -> str:
        """Create branch via GitHub MCP."""
        owner, name = repo.split("/")
        
        await self.github_mcp.call(
            "create_branch",
            owner=owner,
            repo=name,
            branch=branch,
            from_branch=from_ref,
        )
        
        return branch

    async def push_files(
        self, repo: str, branch: str, files: list[FileChange]
    ) -> str:
        """Push files via GitHub MCP."""
        owner, name = repo.split("/")
        
        # GitHub MCP supports batch file updates
        commit_sha = None
        for file in files:
            result = await self.github_mcp.call(
                "create_or_update_file",
                owner=owner,
                repo=name,
                path=file.path,
                content=file.content,
                message=f"SAFS auto-fix: Update {file.path}",
                branch=branch,
            )
            commit_sha = result.get("commit", {}).get("sha")
        
        return commit_sha or ""

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> str:
        """Create PR via GitHub MCP."""
        owner, name = repo.split("/")
        
        result = await self.github_mcp.call(
            "create_pull_request",
            owner=owner,
            repo=name,
            title=title,
            body=body,
            head=head,
            base=base,
            draft=draft,
        )
        
        return result.get("html_url", "")


class GitLabAdapter(RepositoryAdapter):
    """
    Direct REST API adapter for GitLab-hosted repos.
    
    Uses GitLab REST API v4 directly (no MCP server required).
    """

    def __init__(
        self, base_url: str, token: str, timeout: float = 30.0
    ):
        """
        Initialize GitLab adapter.
        
        Args:
            base_url: GitLab instance URL (e.g., "https://gitlab.com")
            token: Personal access token
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            headers={"Private-Token": token},
            timeout=timeout,
        )

    async def get_file(self, repo: str, path: str, ref: str = "main") -> str:
        """Get file contents via GitLab API."""
        url = (
            f"{self.base_url}/api/v4/projects/{quote(repo, safe='')}"
            f"/repository/files/{quote(path, safe='')}"
        )
        
        resp = await self.client.get(url, params={"ref": ref})
        resp.raise_for_status()
        
        content_b64 = resp.json()["content"]
        return base64.b64decode(content_b64).decode("utf-8")

    async def search_code(
        self, query: str, language: Optional[str] = None
    ) -> list[SearchResult]:
        """Search code via GitLab API."""
        url = f"{self.base_url}/api/v4/search"
        
        params = {"scope": "blobs", "search": query}
        
        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        
        search_results = []
        for item in resp.json():
            # Filter by language if specified
            if language and not item["filename"].endswith(
                f".{language}"
            ):
                continue
            
            search_results.append(
                SearchResult(
                    repo=item["project_id"],  # GitLab uses project ID
                    path=item["filename"],
                    content=item.get("data", ""),
                    line_number=item.get("startline"),
                )
            )
        
        return search_results

    async def list_commits(
        self,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[CommitInfo]:
        """List commits via GitLab API."""
        url = f"{self.base_url}/api/v4/projects/{quote(repo, safe='')}/repository/commits"
        
        params = {}
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        
        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        
        commits = []
        for commit in resp.json():
            commits.append(
                CommitInfo(
                    sha=commit["id"],
                    message=commit["message"],
                    author=commit["author_name"],
                    date=commit["created_at"],
                    files=[],  # GitLab API doesn't include files in list
                )
            )
        
        return commits

    async def create_branch(
        self, repo: str, branch: str, from_ref: str
    ) -> str:
        """Create branch via GitLab API."""
        url = f"{self.base_url}/api/v4/projects/{quote(repo, safe='')}/repository/branches"
        
        data = {"branch": branch, "ref": from_ref}
        
        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        
        return resp.json()["name"]

    async def push_files(
        self, repo: str, branch: str, files: list[FileChange]
    ) -> str:
        """Push files via GitLab API."""
        url = f"{self.base_url}/api/v4/projects/{quote(repo, safe='')}/repository/commits"
        
        actions = []
        for file in files:
            actions.append(
                {
                    "action": file.operation,
                    "file_path": file.path,
                    "content": file.content,
                }
            )
        
        data = {
            "branch": branch,
            "commit_message": f"SAFS auto-fix: Update {len(files)} file(s)",
            "actions": actions,
        }
        
        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        
        return resp.json()["id"]

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> str:
        """Create merge request via GitLab API."""
        url = f"{self.base_url}/api/v4/projects/{quote(repo, safe='')}/merge_requests"
        
        data = {
            "source_branch": head,
            "target_branch": base,
            "title": "[Draft] " + title if draft else title,
            "description": body,
        }
        
        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        
        return resp.json()["web_url"]

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class BitbucketAdapter(RepositoryAdapter):
    """
    Direct REST API adapter for Bitbucket Cloud-hosted repos.

    Uses Bitbucket REST API 2.0 directly with app password authentication.
    """

    def __init__(
        self,
        workspace: str,
        username: str,
        app_password: str,
        base_url: str = "https://api.bitbucket.org/2.0",
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize Bitbucket adapter.

        Args:
            workspace: Bitbucket workspace slug.
            username: Bitbucket username.
            app_password: Bitbucket app password (not account password).
            base_url: Bitbucket API base URL.
            timeout: Request timeout in seconds.
        """
        import httpx
        from httpx import BasicAuth

        self.workspace = workspace
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            auth=BasicAuth(username, app_password),
            timeout=timeout,
        )

    async def get_file(self, repo: str, path: str, ref: str = "main") -> str:
        """
        Get file contents via Bitbucket API.

        Args:
            repo: Repository slug (name only, not workspace/name).
            path: File path in the repository.
            ref: Branch, tag, or commit SHA.

        Returns:
            File contents as string.
        """
        # Use the src endpoint for raw file content
        url = f"{self.base_url}/repositories/{self.workspace}/{repo}/src/{ref}/{path}"
        resp = await self.client.get(url)
        resp.raise_for_status()
        return resp.text

    async def search_code(
        self,
        query: str,
        language: Optional[str] = None,
    ) -> list[SearchResult]:
        """
        Search code across workspace repositories.

        Args:
            query: Search query string.
            language: Optional language filter (not enforced by Bitbucket API).

        Returns:
            List of search results.
        """
        url = f"{self.base_url}/workspaces/{self.workspace}/search/code"
        params = {"search_query": query}

        resp = await self.client.get(url, params=params)
        resp.raise_for_status()

        search_results: list[SearchResult] = []
        for item in resp.json().get("values", []):
            file_data = item.get("file", {})
            repo_name = (
                item.get("path_matches", [{}])[0]
                .get("path", {})
                .get("toString", lambda: "unknown")()
                if item.get("path_matches")
                else "unknown"
            )
            search_results.append(
                SearchResult(
                    repo=f"{self.workspace}/{file_data.get('path', '')}",
                    path=file_data.get("path", ""),
                    content="\n".join(
                        m.get("line", {}).get("text", "")
                        for m in item.get("content_matches", [])
                    ),
                    line_number=None,
                )
            )

        return search_results

    async def list_commits(
        self,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[CommitInfo]:
        """
        List commits for a repository.

        Args:
            repo: Repository slug.
            path: Optional file path filter.
            since: Optional ISO8601 timestamp.

        Returns:
            List of commit information.
        """
        url = f"{self.base_url}/repositories/{self.workspace}/{repo}/commits"
        params: dict = {}
        if path:
            params["path"] = path

        resp = await self.client.get(url, params=params)
        resp.raise_for_status()

        commits: list[CommitInfo] = []
        for commit in resp.json().get("values", []):
            author_raw = commit.get("author", {})
            author_name = (
                author_raw.get("user", {}).get("display_name")
                or author_raw.get("raw", "")
            )
            commits.append(
                CommitInfo(
                    sha=commit["hash"],
                    message=commit.get("message", ""),
                    author=author_name,
                    date=commit.get("date", ""),
                    files=[],
                )
            )

        return commits

    async def create_branch(
        self, repo: str, branch: str, from_ref: str
    ) -> str:
        """
        Create a new branch.

        Args:
            repo: Repository slug.
            branch: New branch name.
            from_ref: Source branch or commit SHA.

        Returns:
            Created branch name.
        """
        url = f"{self.base_url}/repositories/{self.workspace}/{repo}/refs/branches"
        data = {
            "name": branch,
            "target": {"hash": from_ref},
        }

        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        return resp.json()["name"]

    async def push_files(
        self,
        repo: str,
        branch: str,
        files: list[FileChange],
    ) -> str:
        """
        Push file changes to *branch* using Bitbucket's src upload endpoint.

        Bitbucket 2.0 supports multipart ``/src`` uploads for committing files.

        Args:
            repo: Repository slug.
            branch: Target branch.
            files: File changes to commit.

        Returns:
            Commit SHA of the new commit.
        """
        url = f"{self.base_url}/repositories/{self.workspace}/{repo}/src"

        # Build multipart form data
        form_data: dict = {"branch": branch, "message": f"SAFS auto-fix: {len(files)} file(s)"}
        for fc in files:
            form_data[fc.path] = fc.content

        resp = await self.client.post(url, data=form_data)
        resp.raise_for_status()

        # Fetch latest commit SHA from the branch
        branch_url = f"{self.base_url}/repositories/{self.workspace}/{repo}/refs/branches/{branch}"
        branch_resp = await self.client.get(branch_url)
        branch_resp.raise_for_status()
        return branch_resp.json().get("target", {}).get("hash", "")

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> str:
        """
        Create a pull request.

        Note: Bitbucket does not natively support draft PRs; the title is
        prefixed with ``[Draft]`` when *draft=True*.

        Args:
            repo: Repository slug.
            title: PR title.
            body: PR description.
            head: Source branch.
            base: Target branch.
            draft: Prefix title with ``[Draft]``.

        Returns:
            PR web URL.
        """
        url = (
            f"{self.base_url}/repositories/{self.workspace}/{repo}/pullrequests"
        )
        data = {
            "title": f"[Draft] {title}" if draft else title,
            "description": body,
            "source": {"branch": {"name": head}},
            "destination": {"branch": {"name": base}},
            "close_source_branch": False,
        }

        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        return resp.json().get("links", {}).get("html", {}).get("href", "")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
