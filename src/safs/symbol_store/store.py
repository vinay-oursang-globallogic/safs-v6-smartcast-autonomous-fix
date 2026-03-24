"""
SAFS v6.0 — Symbol Store Client

MinIO/S3-backed storage for debug symbols (ELF .debug files, JS .map files).

Architecture:
- LOKi C++ debug symbols organized by ELF Build-ID
- JavaScript source maps keyed by bundle URL hash
- Async content-addressed upload/download
- Local cache to avoid repeated downloads

Storage layout:
  elf/<build_id[:2]>/<build_id[2:]>/<library>.debug
  maps/<url_hash>/<bundle>.js.map

Usage:
    client = SymbolStoreClient(
        endpoint=config.symbol_store_endpoint,
        access_key=config.symbol_store_access_key,
        secret_key=config.symbol_store_secret_key,
        bucket=config.symbol_store_bucket,
    )
    await client.upload_elf_debug(build_id="a1b2c3...", debug_path=Path("loki_core.debug"))
    local_path = await client.download_elf_debug(build_id="a1b2c3...")
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class SymbolStoreError(Exception):
    """Raised on symbol store operation failures."""


class SymbolStoreClient:
    """
    Async MinIO/S3 client for debug symbol storage.

    Uses presigned URLs for uploads/downloads when a URL endpoint is provided.
    Falls back to local filesystem when endpoint starts with ``file://``.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket: str = "vizio-symbols",
        local_cache_dir: Optional[Path] = None,
    ) -> None:
        """
        Args:
            endpoint: MinIO/S3 endpoint URL (e.g., http://localhost:9000)
                      Use ``file:///path/to/dir`` for local filesystem mode.
            access_key: Access key / AWS_ACCESS_KEY_ID
            secret_key: Secret key / AWS_SECRET_ACCESS_KEY
            bucket: Bucket name (created on first use if missing)
            local_cache_dir: Local cache for downloaded symbols
        """
        self._endpoint = endpoint.rstrip("/")
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._cache_dir = local_cache_dir or Path(tempfile.mkdtemp(prefix="safs_symbols_"))
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(endpoint)
        self._local_mode = parsed.scheme == "file"
        if self._local_mode:
            self._local_root = Path(parsed.path)
            self._local_root.mkdir(parents=True, exist_ok=True)

        logger.info(
            "SymbolStoreClient initialized (mode=%s, bucket=%s)",
            "local" if self._local_mode else "s3",
            bucket,
        )

    # ------------------------------------------------------------------
    # ELF debug symbols
    # ------------------------------------------------------------------

    async def upload_elf_debug(
        self, build_id: str, debug_path: Path
    ) -> str:
        """
        Upload a debug ELF (.debug file) indexed by Build-ID.

        Args:
            build_id: ELF Build-ID (hex string)
            debug_path: Path to the .debug file to upload

        Returns:
            Object key in the store
        """
        key = self._elf_key(build_id, debug_path.name)
        await self._put_object(key, debug_path)
        logger.info("Uploaded ELF debug %s → %s", debug_path.name, key)
        return key

    async def download_elf_debug(
        self, build_id: str, library_name: Optional[str] = None
    ) -> Optional[Path]:
        """
        Download a debug ELF by Build-ID.

        Args:
            build_id: ELF Build-ID
            library_name: Optional library name hint for the filename

        Returns:
            Local path to downloaded file, or None if not found
        """
        cache_path = self._cache_dir / "elf" / build_id[:2] / build_id[2:]
        cache_path.mkdir(parents=True, exist_ok=True)

        # Check cache first
        existing = list(cache_path.glob("*.debug"))
        if existing:
            return existing[0]

        # Try to download from store
        prefix = f"elf/{build_id[:2]}/{build_id[2:]}/"
        keys = await self._list_objects(prefix)
        if not keys:
            return None

        dest = cache_path / Path(keys[0]).name
        try:
            await self._get_object(keys[0], dest)
            return dest
        except SymbolStoreError as exc:
            logger.warning("Failed to download ELF debug: %s", exc)
            return None

    # ------------------------------------------------------------------
    # JavaScript source maps
    # ------------------------------------------------------------------

    async def upload_source_map(
        self, bundle_url: str, map_path: Path
    ) -> str:
        """
        Upload a JS source map indexed by bundle URL hash.

        Args:
            bundle_url: URL of the minified JS bundle
            map_path: Path to the .js.map file

        Returns:
            Object key in the store
        """
        url_hash = hashlib.sha256(bundle_url.encode()).hexdigest()[:16]
        key = f"maps/{url_hash}/{map_path.name}"
        await self._put_object(key, map_path)
        logger.info("Uploaded source map %s → %s", map_path.name, key)
        return key

    async def download_source_map(self, bundle_url: str) -> Optional[str]:
        """
        Download a JS source map by bundle URL, returning JSON string.

        Args:
            bundle_url: URL of the minified JS bundle

        Returns:
            Source map JSON string, or None if not found
        """
        url_hash = hashlib.sha256(bundle_url.encode()).hexdigest()[:16]
        prefix = f"maps/{url_hash}/"

        keys = await self._list_objects(prefix)
        if not keys:
            return None

        cache_path = self._cache_dir / "maps" / url_hash / Path(keys[0]).name
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        if not cache_path.exists():
            try:
                await self._get_object(keys[0], cache_path)
            except SymbolStoreError:
                return None

        return cache_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _elf_key(build_id: str, filename: str) -> str:
        return f"elf/{build_id[:2]}/{build_id[2:]}/{filename}"

    async def _put_object(self, key: str, source_path: Path) -> None:
        if self._local_mode:
            dest = self._local_root / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source_path.read_bytes())
            return

        # S3/MinIO: use basic PUT with AWS Signature v4 (via boto3 if available)
        try:
            import aioboto3  # type: ignore[import]

            session = aioboto3.Session(
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
            async with session.client(
                "s3",
                endpoint_url=self._endpoint,
            ) as s3:
                await s3.upload_file(str(source_path), self._bucket, key)
        except ImportError:
            # Fallback: plain HTTP PUT (MinIO supports it)
            await self._http_put(key, source_path)

    async def _get_object(self, key: str, dest_path: Path) -> None:
        if self._local_mode:
            src = self._local_root / key
            if not src.exists():
                raise SymbolStoreError(f"Local object not found: {src}")
            dest_path.write_bytes(src.read_bytes())
            return

        try:
            import aioboto3  # type: ignore[import]

            session = aioboto3.Session(
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
            async with session.client("s3", endpoint_url=self._endpoint) as s3:
                await s3.download_file(self._bucket, key, str(dest_path))
        except ImportError:
            await self._http_get(key, dest_path)

    async def _list_objects(self, prefix: str) -> list[str]:
        if self._local_mode:
            search_root = self._local_root / prefix
            if not search_root.exists():
                return []
            return [
                str(p.relative_to(self._local_root))
                for p in search_root.rglob("*")
                if p.is_file()
            ]

        try:
            import aioboto3  # type: ignore[import]

            session = aioboto3.Session(
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
            async with session.client("s3", endpoint_url=self._endpoint) as s3:
                response = await s3.list_objects_v2(
                    Bucket=self._bucket, Prefix=prefix
                )
                return [
                    obj["Key"]
                    for obj in response.get("Contents", [])
                ]
        except ImportError:
            return []

    async def _http_put(self, key: str, source_path: Path) -> None:
        """Fallback HTTP PUT for MinIO."""
        url = f"{self._endpoint}/{self._bucket}/{key}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            data = source_path.read_bytes()
            response = await client.put(url, content=data)
            if response.status_code not in (200, 201, 204):
                raise SymbolStoreError(f"PUT {url} failed: {response.status_code}")

    async def _http_get(self, key: str, dest_path: Path) -> None:
        """Fallback HTTP GET for MinIO."""
        url = f"{self._endpoint}/{self._bucket}/{key}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url)
            if response.status_code == 404:
                raise SymbolStoreError(f"Object not found: {key}")
            if response.status_code >= 400:
                raise SymbolStoreError(f"GET {url} failed: {response.status_code}")
            dest_path.write_bytes(response.content)
