"""
SAFS v6.0 — Attachment Handler

Downloads Jira attachments to a local temp directory and extracts log files
from ZIP/tar archives. Returns populated LogFile objects.

Handles:
- Direct .log / .txt files
- ZIP archives containing multiple log files
- .tar.gz / .tar.bz2 archives
- Nested ZIP-in-ZIP structures (one level deep)

Security:
- Guards against path-traversal attacks in archive members
- Rejects members > 500 MB to prevent ZIP bomb exhaustion
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import IO, Optional

from safs.log_analysis.models import Attachment, LogFile

logger = logging.getLogger(__name__)

# Max extracted file size (500 MB)
_MAX_EXTRACT_BYTES = 500 * 1024 * 1024

# Log file extensions we care about
_LOG_EXTENSIONS = {".log", ".txt", ".dmesg", ".crash", ".tombstone", ".logcat"}

# Regex patterns that mark a file as a Vizio/SmartCast log
_LOG_NAME_RE = re.compile(
    r"(loki|smartcast|dmesg|tombstone|logcat|bugreport|android|kernel"
    r"|crash|diag|debug|error|syslog|messages)",
    re.IGNORECASE,
)


class AttachmentHandlerError(Exception):
    """Raised when attachment download or extraction fails."""


class AttachmentHandler:
    """
    Downloads Jira attachments and extracts log files.

    Typical usage with JiraClient:
        handler = AttachmentHandler(work_dir=Path("/tmp/safs-work"))
        log_files = await handler.process(attachment, jira_client)
    """

    def __init__(self, work_dir: Optional[Path] = None) -> None:
        """
        Args:
            work_dir: Base directory for downloaded files.
                      Created if it does not exist.
                      Defaults to a system temp directory.
        """
        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="safs_attachments_"))
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(
        self,
        attachment: Attachment,
        jira_client: "JiraClient",  # type: ignore[name-defined]  # noqa: F821
    ) -> list[LogFile]:
        """
        Download and extract log files from a single Jira attachment.

        Args:
            attachment: Attachment metadata (from JiraClient)
            jira_client: Authenticated JiraClient for download

        Returns:
            List of LogFile objects with path_to_file populated
        """
        # Determine local download path
        attachment_dest = self.work_dir / attachment.id / attachment.filename
        attachment_dest.parent.mkdir(parents=True, exist_ok=True)

        # Download if not already present
        if not attachment_dest.exists():
            logger.info("Downloading %s → %s", attachment.filename, attachment_dest)
            await jira_client.download_attachment(
                attachment.content_url, str(attachment_dest)
            )
        else:
            logger.debug("Using cached %s", attachment_dest)

        # Extract log files
        log_files = self._extract(attachment_dest, attachment.filename)

        # Attach local file paths to the attachment model
        attachment.path_to_file = str(attachment_dest)
        attachment.log_files = log_files

        return log_files

    def cleanup(self) -> None:
        """Remove all downloaded files from work_dir."""
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract(self, source: Path, original_filename: str) -> list[LogFile]:
        """Dispatch to correct extractor based on file type."""
        suffix = source.suffix.lower()
        stem_lower = source.stem.lower()
        name_lower = source.name.lower()

        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            return self._extract_tar(source, original_filename, "r:gz")
        if name_lower.endswith(".tar.bz2"):
            return self._extract_tar(source, original_filename, "r:bz2")
        if suffix == ".tar":
            return self._extract_tar(source, original_filename, "r:")
        if suffix == ".zip":
            return self._extract_zip(source, original_filename)
        if suffix in _LOG_EXTENSIONS or _LOG_NAME_RE.search(name_lower):
            return [self._make_log_file(source, original_filename, from_archive=False)]

        logger.debug("Skipping non-log attachment: %s", source.name)
        return []

    def _extract_zip(self, zip_path: Path, original_filename: str) -> list[LogFile]:
        """Extract log files from a ZIP archive (one level deep)."""
        extract_dir = zip_path.parent / (zip_path.stem + "_extracted")
        extract_dir.mkdir(exist_ok=True)
        log_files: list[LogFile] = []

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    # Security: reject path traversal
                    if ".." in info.filename or info.filename.startswith("/"):
                        logger.warning("Skipping unsafe zip member: %s", info.filename)
                        continue
                    # Security: reject large files
                    if info.file_size > _MAX_EXTRACT_BYTES:
                        logger.warning(
                            "Skipping oversized zip member (%d bytes): %s",
                            info.file_size,
                            info.filename,
                        )
                        continue

                    member_path = extract_dir / info.filename
                    member_name_lower = Path(info.filename).name.lower()
                    member_suffix = Path(info.filename).suffix.lower()

                    if member_suffix in _LOG_EXTENSIONS or _LOG_NAME_RE.search(member_name_lower):
                        zf.extract(info, extract_dir)
                        log_files.append(
                            self._make_log_file(
                                member_path, original_filename, from_archive=True,
                                rel_path=info.filename
                            )
                        )
        except zipfile.BadZipFile as exc:
            logger.error("Bad ZIP file %s: %s", zip_path, exc)

        return log_files

    def _extract_tar(
        self, tar_path: Path, original_filename: str, mode: str
    ) -> list[LogFile]:
        """Extract log files from a tar archive."""
        extract_dir = tar_path.parent / (tar_path.name + "_extracted")
        extract_dir.mkdir(exist_ok=True)
        log_files: list[LogFile] = []

        try:
            with tarfile.open(tar_path, mode) as tf:
                for member in tf.getmembers():
                    # Security: reject path traversal
                    if member.name.startswith("/") or ".." in member.name:
                        logger.warning("Skipping unsafe tar member: %s", member.name)
                        continue
                    # Security: reject large files
                    if member.size > _MAX_EXTRACT_BYTES:
                        logger.warning(
                            "Skipping oversized tar member (%d bytes): %s",
                            member.size,
                            member.name,
                        )
                        continue

                    member_name_lower = Path(member.name).name.lower()
                    member_suffix = Path(member.name).suffix.lower()

                    if member_suffix in _LOG_EXTENSIONS or _LOG_NAME_RE.search(member_name_lower):
                        tf.extract(member, extract_dir, filter="data")
                        extracted_path = extract_dir / member.name
                        log_files.append(
                            self._make_log_file(
                                extracted_path, original_filename,
                                from_archive=True, rel_path=member.name
                            )
                        )
        except tarfile.TarError as exc:
            logger.error("Error reading tar %s: %s", tar_path, exc)

        return log_files

    @staticmethod
    def _make_log_file(
        path: Path,
        attachment_filename: str,
        from_archive: bool,
        rel_path: Optional[str] = None,
    ) -> LogFile:
        return LogFile(
            path_to_file=str(path),
            path_from_log_root=rel_path or path.name,
            attachment_filename=attachment_filename,
            from_archive=from_archive,
        )
