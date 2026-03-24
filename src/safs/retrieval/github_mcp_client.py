"""
GitHub MCP Client - Wrapper for GitHub MCP server tools.

Thin wrapper around GitHub MCP server for use in RepositoryAdapter.

Master Prompt Reference: Section 4.1 - PATH A (RepositoryAdapter)
"""

from typing import Any, Optional


class GitHubMCPClient:
    """
    Wrapper for GitHub MCP server tools.
    
    Provides a typed interface for calling GitHub MCP tools via MCP protocol.
    """

    def __init__(self, mcp_connection):
        """
        Initialize GitHub MCP client.
        
        Args:
            mcp_connection: MCP connection object to GitHub server
        """
        self.connection = mcp_connection

    async def call(self, tool_name: str, **kwargs) -> dict[str, Any]:
        """
        Call a GitHub MCP tool.
        
        Args:
            tool_name: Name of GitHub MCP tool
            **kwargs: Tool parameters
        
        Returns:
            Tool response as dictionary
        
        Raises:
            MCPError: If tool call fails
        """
        # Handle both real MCP connection and mock
        if hasattr(self.connection, 'call_tool'):
            result = await self.connection.call_tool(tool_name, kwargs)
        else:
            # Mock interface for testing
            result = await self.connection.call(tool_name, **kwargs)
        return result

    async def get_file_contents(
        self, owner: str, repo: str, path: str, ref: str = "main"
    ) -> dict[str, Any]:
        """Get file contents from repository."""
        return await self.call(
            "get_file_contents",
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
        )

    async def search_code(
        self, q: str, per_page: int = 30
    ) -> dict[str, Any]:
        """Search code across GitHub."""
        return await self.call("search_code", q=q, per_page=per_page)

    async def list_commits(
        self,
        owner: str,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> dict[str, Any]:
        """List commits for repository or file."""
        params = {"owner": owner, "repo": repo}
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        
        return await self.call("list_commits", **params)

    async def create_branch(
        self, owner: str, repo: str, branch: str, from_branch: str
    ) -> dict[str, Any]:
        """Create a new branch."""
        return await self.call(
            "create_branch",
            owner=owner,
            repo=repo,
            branch=branch,
            from_branch=from_branch,
        )

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
    ) -> dict[str, Any]:
        """Create or update a file in repository."""
        return await self.call(
            "create_or_update_file",
            owner=owner,
            repo=repo,
            path=path,
            content=content,
            message=message,
            branch=branch,
        )

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, Any]:
        """Create a pull request."""
        return await self.call(
            "create_pull_request",
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base,
            draft=draft,
        )
