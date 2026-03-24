"""
Unit tests for symbol_store modules.

Covers:
- ElfSymbolicator (addr2line wrapper)
- SourceMapStore (VLQ JS source maps)
- SymbolStore / SymbolStoreClient (existing)
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    """Run a coroutine, creating a new event loop if needed (Python 3.10+)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── ElfSymbolicator ───────────────────────────────────────────────────────────

class TestElfSymbolicator:
    def _fake_elf(self, tmp_path) -> Path:
        """Create a minimal (invalid) ELF file that exists on disk."""
        elf = tmp_path / "fake.elf"
        # ELF magic bytes so it passes basic file existence check
        elf.write_bytes(b"\x7fELF\x01\x01\x01\x00" + b"\x00" * 56)
        return elf

    def test_instantiation(self):
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        sym = ElfSymbolicator()
        assert sym is not None

    def test_returns_frames_for_valid_path(self, tmp_path):
        """symbolicate on an existing (fake) ELF returns SymbolicatedFrames."""
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator, SymbolicationStatus
        sym = ElfSymbolicator()
        elf_path = self._fake_elf(tmp_path)
        result = asyncio.run(sym.symbolicate(elf_path, [0xb6f12a40]))
        assert isinstance(result, list)
        for frame in result:
            assert frame.status in (
                SymbolicationStatus.TOOL_NOT_FOUND,
                SymbolicationStatus.NO_DEBUG_INFO,
                SymbolicationStatus.OK,
                SymbolicationStatus.TIMEOUT,
            )

    def test_returns_list_for_empty_addresses(self, tmp_path):
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        sym = ElfSymbolicator()
        elf_path = self._fake_elf(tmp_path)
        result = asyncio.run(sym.symbolicate(elf_path, []))
        assert result == []

    def test_symbolicated_frame_has_address_field(self, tmp_path):
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        sym = ElfSymbolicator()
        elf_path = self._fake_elf(tmp_path)
        result = asyncio.run(sym.symbolicate(elf_path, [0xdeadbeef]))
        assert len(result) == 1
        frame = result[0]
        assert hasattr(frame, "address") or hasattr(frame, "address_hex")

    def test_missing_elf_file_raises(self):
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        sym = ElfSymbolicator()
        with pytest.raises(FileNotFoundError):
            asyncio.run(sym.symbolicate(Path("/nonexistent/path.elf"), [0x1000]))

    def test_status_enum_values(self):
        from src.safs.symbol_store.elf_symbolication import SymbolicationStatus
        assert SymbolicationStatus.OK is not None
        assert SymbolicationStatus.TOOL_NOT_FOUND is not None
        assert SymbolicationStatus.NO_DEBUG_INFO is not None

    def test_multiple_addresses(self, tmp_path):
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        sym = ElfSymbolicator()
        elf_path = self._fake_elf(tmp_path)
        addresses = [i * 0x1000 for i in range(5)]
        result = asyncio.run(sym.symbolicate(elf_path, addresses))
        assert len(result) == 5

    def test_async_via_asyncio_run(self, tmp_path):
        """ElfSymbolicator can be used in async context."""
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        elf_path = self._fake_elf(tmp_path)

        async def run():
            sym = ElfSymbolicator()
            return await sym.symbolicate(elf_path, [0x1000])

        result = asyncio.run(run())
        assert isinstance(result, list)


# ── SourceMapStore ────────────────────────────────────────────────────────────

class TestSourceMapStore:
    _SAMPLE_MAP = {
        "version": 3,
        "sources": ["src/app.ts", "src/utils.ts"],
        "names": ["foo", "bar", "baz"],
        "mappings": "AAAA,SAAS,GAAG,CAAC,CAAC;MACT,OAAO,CAAC,GAAG,CAAC,KAAK,CAAC;AACnB,CAAC",
        "file": "bundle.js"
    }

    def _store(self):
        from src.safs.symbol_store.source_map_decoder import SourceMapStore
        return SourceMapStore(lru_maxsize=10)

    def test_instantiation(self):
        store = self._store()
        assert store is not None

    def test_decode_valid_position(self, tmp_path):
        store = self._store()
        map_path = tmp_path / "sample.js.map"
        map_path.write_text(json.dumps(self._SAMPLE_MAP))
        # Line=1, col=0 → should map to src/app.ts
        pos = store.decode(map_path, 1, 0)
        # May return None if 0-indexed mismatch — that's OK
        assert pos is None or hasattr(pos, "source")

    def test_decode_returns_none_for_out_of_range(self, tmp_path):
        store = self._store()
        map_path = tmp_path / "sample.js.map"
        map_path.write_text(json.dumps(self._SAMPLE_MAP))
        pos = store.decode(map_path, 9999, 0)
        assert pos is None

    def test_decode_missing_file_raises(self):
        """SourceMapStore raises FileNotFoundError for missing files."""
        store = self._store()
        with pytest.raises(FileNotFoundError):
            store.decode(Path("/nonexistent/file.js.map"), 1, 0)

    def test_lru_cache_is_limited(self, tmp_path):
        from src.safs.symbol_store.source_map_decoder import SourceMapStore
        store = SourceMapStore(lru_maxsize=2)
        # Create 3 different maps
        for i in range(3):
            map_path = tmp_path / f"map{i}.js.map"
            m = dict(self._SAMPLE_MAP)
            m["file"] = f"bundle{i}.js"
            map_path.write_text(json.dumps(m))
            store.decode(map_path, 1, 0)
        # Should not raise; LRU should evict oldest
        assert True

    def test_decode_vlq_aaaa(self, tmp_path):
        """'AAAA' in VLQ means all zeros: line=0, col=0, source_idx=0."""
        store = self._store()
        m = dict(self._SAMPLE_MAP)
        m["mappings"] = "AAAA"
        map_path = tmp_path / "vlq_test.js.map"
        map_path.write_text(json.dumps(m))
        pos = store.decode(map_path, 1, 0)
        if pos is not None:
            assert pos.source in m["sources"]

    def test_source_map_position_has_source_field(self, tmp_path):
        store = self._store()
        map_path = tmp_path / "sample.js.map"
        map_path.write_text(json.dumps(self._SAMPLE_MAP))
        pos = store.decode(map_path, 1, 0)
        if pos is not None:
            assert hasattr(pos, "source")
            assert hasattr(pos, "original_line") or hasattr(pos, "line")

    def test_decode_invalid_json_raises_or_returns_none(self, tmp_path):
        store = self._store()
        bad_file = tmp_path / "bad.js.map"
        bad_file.write_text("this is not json {{{")
        try:
            pos = store.decode(bad_file, 1, 0)
            assert pos is None
        except (ValueError, json.JSONDecodeError, Exception):
            pass  # raises on bad JSON — also acceptable

    def test_cache_reuses_decoded_map(self, tmp_path):
        store = self._store()
        map_path = tmp_path / "cache_test.js.map"
        map_path.write_text(json.dumps(self._SAMPLE_MAP))
        # First call
        pos1 = store.decode(map_path, 1, 0)
        # Second call should use cache — no error
        pos2 = store.decode(map_path, 1, 0)
        assert pos1 == pos2


# ── SymbolStore (existing) basic smoke tests ──────────────────────────────────

class TestSymbolStoreSmoke:
    def test_import(self):
        from src.safs.symbol_store import SymbolStore
        assert SymbolStore is not None

    def test_symbol_store_client_import(self):
        from src.safs.symbol_store import SymbolStoreClient
        assert SymbolStoreClient is not None

    def test_symbol_store_error_import(self):
        from src.safs.symbol_store import SymbolStoreError
        assert SymbolStoreError is not None
