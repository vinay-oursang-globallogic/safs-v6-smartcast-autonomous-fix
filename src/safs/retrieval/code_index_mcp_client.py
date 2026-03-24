"""
Code Index MCP Client - Wrapper for Code-Index-MCP server tools.

Thin wrapper around Code-Index-MCP (PVC-backed semantic search).

Master Prompt Reference: Section 4.1 - PATH B (Code-Index-MCP)
"""

from typing import Any, Optional


class CodeIndexMCPClient:
    """
    Wrapper for Code-Index-MCP server tools.
    
    PATH B provides semantic search and AST symbol extraction WITHOUT
    rate limits (PVC-backed, no external API calls).
    """

    def __init__(self, mcp_connection):
        """
        Initialize Code-Index-MCP client.
        
        Args:
            mcp_connection: MCP connection object to Code-Index-MCP server
        """
        self.connection = mcp_connection

    async def call(self, tool_name: str, **kwargs) -> dict[str, Any]:
        """
        Call a Code-Index-MCP tool.
        
        Args:
            tool_name: Name of Code-Index-MCP tool
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

    async def semantic_search(
        self,
        query: str,
        language: Optional[str] = None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """
        Semantic code search (embedding-based).
        
        Args:
            query: Natural language or code query
            language: Optional language filter
            top_k: Number of results to return
        
        Returns:
            Search results with similarity scores
        """
        params = {"query": query, "top_k": top_k}
        if language:
            params["language"] = language
        
        return await self.call("semantic_search", **params)

    async def symbol_search(
        self, symbol: str, symbol_type: Optional[str] = None
    ) -> dict[str, Any]:
        """
        AST-based symbol search (function, class, variable).
        
        Args:
            symbol: Symbol name to find
            symbol_type: Optional type filter (function, class, variable)
        
        Returns:
            Exact symbol matches with AST metadata
        """
        params = {"symbol": symbol}
        if symbol_type:
            params["symbol_type"] = symbol_type
        
        return await self.call("symbol_search", **params)

    async def get_file_ast(self, repo: str, path: str) -> dict[str, Any]:
        """
        Get AST representation of file.
        
        Args:
            repo: Repository identifier
            path: File path
        
        Returns:
            AST structure
        """
        return await self.call("get_file_ast", repo=repo, path=path)
