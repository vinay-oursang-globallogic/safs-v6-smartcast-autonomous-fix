"""
SAFS v6.0 — LLM Client for Root Cause Analysis

Provides async LLM client for Claude Haiku (cost-efficient analysis model).
Supports structured output via Pydantic models for reliable parsing.

Usage:
    client = LLMClient(api_key=os.getenv("ANTHROPIC_API_KEY"))
    result = await client.complete(
        system_prompt=LOKI_RCA_SYSTEM_PROMPT,
        user_prompt=evidence_summary,
        response_model=RootCauseResult,
        temperature=0.0,
    )
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base exception for LLM client errors."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when LLM API rate limit is exceeded."""
    pass


class LLMValidationError(LLMError):
    """Raised when LLM response fails Pydantic validation."""
    pass


class LLMClient:
    """
    Async LLM client for Claude Haiku root cause analysis.
    
    Features:
    - Async httpx with connection pooling
    - Structured output via Pydantic models
    - Retry logic with exponential backoff
    - Token usage tracking
    - Rate limit handling
    
    Models:
    - claude-haiku: Cost-efficient for analysis (Stage 3: Root Cause)
    - claude-opus: High capability for code generation (Stage 6: Fix Gen)
    """
    
    ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"
    
    # Model configurations
    MODELS = {
        "claude-haiku": {
            "name": "claude-3-haiku-20240307",
            "max_tokens": 4096,
            "cost_per_1k_input": 0.00025,
            "cost_per_1k_output": 0.00125,
        },
        "claude-opus": {
            "name": "claude-3-opus-20240229",
            "max_tokens": 4096,
            "cost_per_1k_input": 0.015,
            "cost_per_1k_output": 0.075,
        },
        "claude-opus-4": {
            "name": "claude-3-opus-20240229",  # TODO: Update when Opus 4 is released
            "max_tokens": 8192,
            "cost_per_1k_input": 0.015,
            "cost_per_1k_output": 0.075,
        },
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
    ):
        """
        Initialize LLM client.
        
        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            timeout: Request timeout in seconds (default: 120)
            max_retries: Max retry attempts for transient failures (default: 3)
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var or pass api_key parameter."
            )
        
        self.timeout = timeout
        self.max_retries = max_retries
        
        # Create async HTTP client with connection pooling
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            follow_redirects=True,
        )
        
        # Usage tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        
        logger.info(f"LLMClient initialized (timeout={timeout}s, max_retries={max_retries})")
    
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        model: str = "claude-haiku",
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> T:
        """
        Generate LLM completion with structured output.
        
        Args:
            system_prompt: System prompt (role definition, instructions)
            user_prompt: User prompt (evidence, question)
            response_model: Pydantic model for structured output
            model: Model name (claude-haiku, claude-opus)
            temperature: Sampling temperature 0.0-1.0 (0.0 = deterministic)
            max_tokens: Max output tokens (defaults to model config)
        
        Returns:
            Pydantic model instance parsed from LLM response
        
        Raises:
            LLMError: On API errors
            LLMRateLimitError: On rate limit exceeded
            LLMValidationError: On Pydantic validation failure
        """
        if model not in self.MODELS:
            raise ValueError(f"Unknown model: {model}. Available: {list(self.MODELS.keys())}")
        
        model_config = self.MODELS[model]
        model_name = model_config["name"]
        max_tokens = max_tokens or model_config["max_tokens"]
        
        # Build request payload
        payload = {
            "model": model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt}
            ],
        }
        
        # Add JSON schema hint for structured output
        schema_hint = f"\n\nIMPORTANT: Respond with valid JSON matching this schema:\n{response_model.model_json_schema()}"
        payload["messages"][0]["content"] += schema_hint
        
        # Retry logic with exponential backoff
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"LLM request attempt {attempt + 1}/{self.max_retries} (model={model})")
                
                response = await self.client.post(
                    self.ANTHROPIC_API_URL,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": self.ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                
                # Handle rate limits
                if response.status_code == 429:
                    retry_after = float(response.headers.get("retry-after", 60))
                    if attempt < self.max_retries - 1:
                        logger.warning(f"Rate limit hit, retrying after {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue
                    raise LLMRateLimitError(f"Rate limit exceeded after {self.max_retries} retries")
                
                # Handle other errors
                response.raise_for_status()
                
                # Parse response
                data = response.json()
                content = data["content"][0]["text"]
                usage = data.get("usage", {})
                
                # Track usage
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
                
                # Calculate cost
                cost = (
                    (input_tokens / 1000.0) * model_config["cost_per_1k_input"]
                    + (output_tokens / 1000.0) * model_config["cost_per_1k_output"]
                )
                self.total_cost += cost
                
                logger.info(
                    f"LLM completion success: {input_tokens} input + {output_tokens} output tokens, "
                    f"cost=${cost:.4f} (total=${self.total_cost:.4f})"
                )
                
                # Parse structured output
                try:
                    # Extract JSON from response (handle markdown code blocks)
                    json_str = self._extract_json(content)
                    json_data = json.loads(json_str)
                    result = response_model.model_validate(json_data)
                    return result
                    
                except (json.JSONDecodeError, ValidationError) as e:
                    logger.error(f"Failed to parse LLM response: {e}")
                    logger.debug(f"Raw response: {content[:500]}")
                    raise LLMValidationError(
                        f"LLM response failed validation: {e}\n"
                        f"Response preview: {content[:200]}..."
                    )
                
            except httpx.HTTPStatusError as e:
                if attempt < self.max_retries - 1 and e.response.status_code >= 500:
                    # Retry on 5xx server errors
                    backoff = 2 ** attempt
                    logger.warning(f"Server error {e.response.status_code}, retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                raise LLMError(f"HTTP error: {e.response.status_code} - {e.response.text}")
            
            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    backoff = 2 ** attempt
                    logger.warning(f"Request error {type(e).__name__}, retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                raise LLMError(f"Request failed: {e}")
        
        raise LLMError(f"LLM request failed after {self.max_retries} attempts")
    
    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from LLM response, handling markdown code blocks.
        
        Args:
            text: Raw LLM response text
        
        Returns:
            JSON string
        """
        # Try to find JSON in markdown code block
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()
        
        # Try to find JSON in generic code block
        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                content = text[start:end].strip()
                # Skip language identifier if present
                if "\n" in content:
                    content = "\n".join(content.split("\n")[1:])
                return content
        
        # Assume entire text is JSON
        return text.strip()
    
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = "claude-haiku",
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate LLM completion with raw text output (for fix generation).
        
        Similar to complete() but returns raw text instead of structured output.
        
        Args:
            system_prompt: System prompt (role definition, instructions)
            user_prompt: User prompt (evidence, question)
            model: Model name (claude-haiku, claude-opus, claude-opus-4)
            temperature: Sampling temperature 0.0-1.0
            max_tokens: Max output tokens
        
        Returns:
            Raw text response from LLM
        """
        if model not in self.MODELS:
            raise ValueError(f"Unknown model: {model}. Available: {list(self.MODELS.keys())}")
        
        model_config = self.MODELS[model]
        model_name = model_config["name"]
        max_tokens = max_tokens or model_config["max_tokens"]
        
        # Build request payload
        payload = {
            "model": model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt}
            ],
        }
        
        # Make API request with retries
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self.client.post(
                    self.ANTHROPIC_API_URL,
                    json=payload,
                    headers={
                        "anthropic-version": "2024-01-01",
                        "x-api-key": self.api_key,
                        "content-type": "application/json",
                    },
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("retry-after", 60))
                    logger.warning(f"Rate limited. Retry after {retry_after}s")
                    raise LLMRateLimitError(f"Rate limit exceeded. Retry after {retry_after}s")
                
                response.raise_for_status()
                data = response.json()
                
                # Extract text from response
                if "content" in data and len(data["content"]) > 0:
                    text = data["content"][0].get("text", "")
                    
                    # Track usage
                    usage = data.get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    
                    input_cost = (input_tokens / 1000) * model_config["cost_per_1k_input"]
                    output_cost = (output_tokens / 1000) * model_config["cost_per_1k_output"]
                    
                    self.total_input_tokens += input_tokens
                    self.total_output_tokens += output_tokens  
                    self.total_cost += input_cost + output_cost
                    
                    logger.info(
                        f"Generate SUCCESS (model={model}, tokens={input_tokens}/{output_tokens}, "
                        f"cost=${input_cost + output_cost:.4f})"
                    )
                    
                    return text
                else:
                    raise LLMError("Empty response from API")
                
            except httpx.TimeoutException as e:
                logger.warning(f"Request timeout (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    raise LLMError(f"Request timeout after {attempt} attempts")
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
                raise LLMError(f"API error: {e.response.status_code}")
            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    raise LLMError(f"Failed after {attempt} attempts: {str(e)}")
    
    async def close(self):
        """Close HTTP client connection pool."""
        await self.client.aclose()
        logger.info("LLMClient closed")
    
    def get_usage_summary(self) -> Dict[str, Any]:
        """
        Get token usage and cost summary.
        
        Returns:
            Dict with total_input_tokens, total_output_tokens, total_cost
        """
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
        }
