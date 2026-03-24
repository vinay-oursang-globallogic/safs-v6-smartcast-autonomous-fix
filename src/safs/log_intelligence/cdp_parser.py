"""
SAFS v6.0 - CDP Parser & Source Map Decoder

Chrome DevTools Protocol (CDP) log parser and JavaScript source map decoder
for HTML5 streaming app debugging.

**CDP Parsing**:
- Parses CDP JSON trace logs (port 9555 dumps)
- Extracts Runtime.exceptionThrown events
- Extracts Console.messageAdded (console.error)
- Extracts Network.requestFailed events

**Source Map Decoding**:
- Decodes JS source maps (VLQ base64 mappings)
- Maps minified (bundle.js:L10:C5) → original (VideoPlayer.js:L142:C12)
- Handles Webpack/Rollup/esbuild source maps

**Example CDP JSON**:
```json
{
  "method": "Runtime.exceptionThrown",
  "params": {
    "timestamp": 1702345678.123,
    "exceptionDetails": {
      "text": "TypeError: Cannot read property 'play' of null",
      "url": "https://app.vizio.com/bundle.min.js",
      "lineNumber": 42,
      "columnNumber": 1024,
      "stackTrace": {
        "callFrames": [
          {"url": "https://app.vizio.com/bundle.min.js", "lineNumber": 42, "columnNumber": 1024}
        ]
      }
    }
  }
}
```

**Source Map Example**:
```json
{
  "version": 3,
  "sources": ["src/VideoPlayer.js", "src/utils.js"],
  "names": ["play", "video", "null"],
  "mappings": "AAAA,SAASA,IAAI,C..."
}
```
"""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    CDPEvent,
    CDPException,
    CDPParseResult,
    SourceMapPosition,
    SourceMappedFrame,
)


# ==================================================================================
# CDP LOG PARSER
# ==================================================================================


class CDPLogParser:
    """
    Parses Chrome DevTools Protocol JSON trace logs.

    Supports:
    - Runtime.exceptionThrown (JS exceptions)
    - Console.messageAdded (console.error messages)
    - Network.requestFailed (network errors)
    """

    def parse(self, cdp_json: str | dict) -> CDPParseResult:
        """
        Parse CDP JSON trace.

        Args:
            cdp_json: CDP JSON string or dict

        Returns:
            CDPParseResult with parsed events
        """
        if isinstance(cdp_json, str):
            try:
                data = json.loads(cdp_json)
            except json.JSONDecodeError:
                return CDPParseResult(
                    events=[], exceptions=[], console_errors=[], network_errors=[]
                )
        else:
            data = cdp_json

        # CDP trace can be:
        # 1. Single event dict: {"method": "...", "params": {...}}
        # 2. Array of events: [{"method": "...", "params": {...}}, ...]
        # 3. Wrapped in envelope: {"traceEvents": [...]}

        if isinstance(data, list):
            events_list = data
        elif "traceEvents" in data:
            events_list = data["traceEvents"]
        elif "method" in data:
            events_list = [data]
        else:
            events_list = []

        events = []
        exceptions = []
        console_errors = []
        network_errors = []

        for event_data in events_list:
            if not isinstance(event_data, dict) or "method" not in event_data:
                continue

            # Parse CDP event
            event = self._parse_event(event_data)
            if event:
                events.append(event)

            # Extract specific event types
            method = event_data.get("method")
            params = event_data.get("params", {})

            if method == "Runtime.exceptionThrown":
                exception = self._extract_exception(params)
                if exception:
                    exceptions.append(exception)

            elif method == "Console.messageAdded":
                console_error = self._extract_console_error(params)
                if console_error:
                    console_errors.append(console_error)

            elif method == "Network.requestFailed":
                network_error = self._extract_network_error(params)
                if network_error:
                    network_errors.append(network_error)

        return CDPParseResult(
            events=events,
            exceptions=exceptions,
            console_errors=console_errors,
            network_errors=network_errors,
        )

    def _parse_event(self, event_data: dict) -> Optional[CDPEvent]:
        """Parse generic CDP event"""
        try:
            method = event_data.get("method")
            params = event_data.get("params", {})

            # Extract timestamp (can be in params or top-level)
            timestamp_ms = (
                params.get("timestamp")
                or event_data.get("timestamp")
                or datetime.now(timezone.utc).timestamp() * 1000
            )
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            return CDPEvent(
                timestamp=timestamp, method=method, params=params
            )
        except Exception:
            return None

    def _extract_exception(self, params: dict) -> Optional[CDPException]:
        """Extract JavaScript exception from Runtime.exceptionThrown"""
        try:
            exception_details = params.get("exceptionDetails", {})

            timestamp_ms = params.get("timestamp", datetime.now(timezone.utc).timestamp() * 1000)
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            text = exception_details.get("text", "Unknown exception")

            # Extract exception type from text (e.g., "TypeError: ...")
            exception_type = "Error"
            if ":" in text:
                exception_type = text.split(":")[0].strip()

            url = exception_details.get("url")
            line_number = exception_details.get("lineNumber")
            column_number = exception_details.get("columnNumber")

            # Extract stack trace
            stack_trace_data = exception_details.get("stackTrace", {})
            call_frames = stack_trace_data.get("callFrames", [])
            stack_trace = []
            for frame in call_frames:
                frame_url = frame.get("url", "")
                frame_line = frame.get("lineNumber", 0)
                frame_col = frame.get("columnNumber", 0)
                frame_func = frame.get("functionName", "<anonymous>")
                stack_trace.append(
                    f"{frame_func} at {frame_url}:{frame_line}:{frame_col}"
                )

            return CDPException(
                timestamp=timestamp,
                exception_type=exception_type,
                message=text,
                stack_trace=stack_trace,
                url=url,
                line_number=line_number,
                column_number=column_number,
            )
        except Exception:
            return None

    def _extract_console_error(self, params: dict) -> Optional[str]:
        """Extract console.error message"""
        try:
            message_data = params.get("message", {})
            level = message_data.get("level")
            if level != "error":
                return None

            text = message_data.get("text", "")
            return text
        except Exception:
            return None

    def _extract_network_error(self, params: dict) -> Optional[str]:
        """Extract network request failure"""
        try:
            request_id = params.get("requestId", "")
            error_text = params.get("errorText", "Unknown network error")
            url = params.get("url", "")
            return f"Network error: {error_text} for {url}"
        except Exception:
            return None


# ==================================================================================
# SOURCE MAP DECODER
# ==================================================================================


class SourceMapDecoder:
    """
    JavaScript source map decoder (VLQ base64).

    Supports source map v3 format (Webpack, Rollup, esbuild).
    """

    # Base64 VLQ alphabet
    VLQ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    def __init__(self, source_map: dict | str | Path):
        """
        Initialize source map decoder.

        Args:
            source_map: Source map JSON (dict, JSON string, or file path)
        """
        if isinstance(source_map, Path):
            with open(source_map) as f:
                self.source_map = json.load(f)
        elif isinstance(source_map, str):
            self.source_map = json.loads(source_map)
        else:
            self.source_map = source_map

        # Decode mappings once on init
        self.decoded_mappings = self._decode_mappings()

    def map_position(
        self, minified_line: int, minified_column: int
    ) -> Optional[SourceMapPosition]:
        """
        Map minified position to original source position.

        Args:
            minified_line: Line number in minified file (1-based)
            minified_column: Column number in minified file (0-based)

        Returns:
            SourceMapPosition or None if no mapping found
        """
        # Find closest mapping for this line
        line_idx = minified_line - 1
        if line_idx < 0 or line_idx >= len(self.decoded_mappings):
            return None

        line_mappings = self.decoded_mappings[line_idx]

        # Find mapping with closest column <= minified_column
        best_mapping = None
        for mapping in line_mappings:
            if mapping["generated_column"] <= minified_column:
                best_mapping = mapping
            else:
                break  # Mappings are sorted by column

        if not best_mapping:
            return None

        # Extract original position
        sources = self.source_map.get("sources", [])
        source_idx = best_mapping.get("source_idx")
        if source_idx is None or source_idx >= len(sources):
            return None

        original_file = sources[source_idx]
        original_line = best_mapping.get("original_line")
        original_column = best_mapping.get("original_column")

        # Extract original name if available
        names = self.source_map.get("names", [])
        name_idx = best_mapping.get("name_idx")
        original_name = names[name_idx] if name_idx is not None and name_idx < len(names) else None

        return SourceMapPosition(
            original_file=original_file,
            original_line=original_line,
            original_column=original_column,
            original_name=original_name,
        )

    def _decode_mappings(self) -> list[list[dict]]:
        """
        Decode VLQ base64 mappings.

        Returns:
            List of lines, each containing list of mappings (dict with columns/positions)
        """
        mappings_str = self.source_map.get("mappings", "")
        lines = mappings_str.split(";")

        decoded = []
        prev_source_idx = 0
        prev_original_line = 0
        prev_original_column = 0
        prev_name_idx = 0

        for line_str in lines:
            line_mappings = []
            segments = line_str.split(",")

            prev_generated_column = 0

            for segment_str in segments:
                if not segment_str:
                    continue

                # Decode VLQ segment
                values = self._decode_vlq_segment(segment_str)
                if not values:
                    continue

                # values[0] = generated column (delta)
                # values[1] = source index (delta)
                # values[2] = original line (delta)
                # values[3] = original column (delta)
                # values[4] = name index (delta) [optional]

                generated_column = prev_generated_column + values[0]
                prev_generated_column = generated_column

                mapping = {"generated_column": generated_column}

                if len(values) >= 4:
                    source_idx = prev_source_idx + values[1]
                    original_line = prev_original_line + values[2]
                    original_column = prev_original_column + values[3]

                    prev_source_idx = source_idx
                    prev_original_line = original_line
                    prev_original_column = original_column

                    mapping["source_idx"] = source_idx
                    mapping["original_line"] = original_line + 1  # Convert to 1-based
                    mapping["original_column"] = original_column

                    if len(values) >= 5:
                        name_idx = prev_name_idx + values[4]
                        prev_name_idx = name_idx
                        mapping["name_idx"] = name_idx

                line_mappings.append(mapping)

            decoded.append(line_mappings)

        return decoded

    def _decode_vlq_segment(self, segment: str) -> Optional[list[int]]:
        """Decode VLQ base64 segment"""
        try:
            values = []
            value = 0
            shift = 0

            for char in segment:
                digit = self.VLQ_CHARS.index(char)
                has_continuation = digit & 32
                digit &= 31

                value += digit << shift
                shift += 5

                if not has_continuation:
                    # Sign bit is LSB
                    is_negative = value & 1
                    value >>= 1
                    if is_negative:
                        value = -value
                    values.append(value)
                    value = 0
                    shift = 0

            return values
        except Exception:
            return None


# ==================================================================================
# HTML5 FRAME MAPPER (COMBINES CDP + SOURCE MAPS)
# ==================================================================================


class HTML5FrameMapper:
    """
    Maps minified JS stack frames to original source using source maps.

    Combines CDPLogParser exceptions with SourceMapDecoder.
    """

    def __init__(self, source_maps: dict[str, SourceMapDecoder]):
        """
        Initialize frame mapper.

        Args:
            source_maps: Dict of {minified_url → SourceMapDecoder}
        """
        self.source_maps = source_maps

    def map_exception(self, exception: CDPException) -> list[SourceMappedFrame]:
        """
        Map exception stack frames to original source.

        Args:
            exception: CDPException with stack trace

        Returns:
            List of SourceMappedFrame (minified + original positions)
        """
        mapped_frames = []

        # Map exception location
        if exception.url and exception.line_number is not None:
            frame = self._map_frame(
                exception.url, exception.line_number, exception.column_number or 0
            )
            mapped_frames.append(frame)

        # Map stack trace frames
        for stack_line in exception.stack_trace:
            # Parse stack line: "functionName at url:line:col"
            if " at " in stack_line:
                location = stack_line.split(" at ")[1]
                if ":" in location:
                    parts = location.rsplit(":", 2)
                    if len(parts) == 3:
                        url, line_str, col_str = parts
                        try:
                            line = int(line_str)
                            col = int(col_str)
                            frame = self._map_frame(url, line, col)
                            mapped_frames.append(frame)
                        except ValueError:
                            pass

        return mapped_frames

    def _map_frame(
        self, url: str, line: int, column: int
    ) -> SourceMappedFrame:
        """Map a single frame"""
        # Find matching source map decoder
        decoder = self.source_maps.get(url)
        if not decoder:
            return SourceMappedFrame(
                minified_file=url,
                minified_line=line,
                minified_column=column,
                original_position=None,
                status="NO_SOURCE_MAP",
            )

        # Map position
        original_pos = decoder.map_position(line, column)
        if not original_pos:
            return SourceMappedFrame(
                minified_file=url,
                minified_line=line,
                minified_column=column,
                original_position=None,
                status="PARSE_ERROR",
            )

        return SourceMappedFrame(
            minified_file=url,
            minified_line=line,
            minified_column=column,
            original_position=original_pos,
            status="OK",
        )
