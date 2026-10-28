"""Inspect exported video without assuming an undocumented Oli QUIC protocol."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT_BYTES = 65536
MEDIA_FORMATS = "h264,hevc,mjpeg,mov,matroska,webm,mpegts,avi"
EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}
PROTOCOL_REQUIREMENTS = [
    "Version-matched Oli/PICO video-only receiver demo, SDK or application protocol",
    "Connection roles, QUIC version, ALPN and approved video endpoint",
    "Certificate trust and application authentication using local credential files",
    "Video subscription, channel selection and PICO-equivalent codec negotiation",
    "STREAM/DATAGRAM framing, fragment ordering and codec initialization data",
    "Confirmation that a video observer does not take control or replace PICO",
]

CHUNK_HEADER = struct.Struct("<BHHH")


def authentication_payload(token: bytes) -> bytes:
    if not token or len(token) > 65535:
        raise ValueError("Authentication token must contain 1..65535 UTF-8 bytes")
    try:
        token.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Authentication token must be UTF-8") from error
    return struct.pack("<H", len(token)) + token


@dataclass
class PendingFrame:
    total: int
    created_at: float
    chunks: dict[int, bytes] = field(default_factory=dict)
    size: int = 0
    invalid: bool = False


class FrameAssembler:
    """Reassemble one camera using quic_view.py's seven-byte datagram header."""

    def __init__(
        self,
        camera_id: int,
        *,
        frame_timeout: float = 0.5,
        max_pending: int = 8,
        max_frame_bytes: int = 8 * 1024 * 1024,
        max_buffer_bytes: int = 16 * 1024 * 1024,
        max_chunks: int = 4096,
    ):
        if not 0 <= camera_id <= 255 or not math.isfinite(frame_timeout) or frame_timeout <= 0:
            raise ValueError("Invalid camera or frame timeout")
        if min(max_pending, max_frame_bytes, max_buffer_bytes, max_chunks) <= 0:
            raise ValueError("Reassembly limits must be positive")
        self.camera_id = camera_id
        self.frame_timeout = frame_timeout
        self.max_pending = max_pending
        self.max_frame_bytes = max_frame_bytes
        self.max_buffer_bytes = max_buffer_bytes
        self.max_chunks = max_chunks
        self.pending: dict[int, PendingFrame] = {}
        self.expected_id: int | None = None
        self.buffered_bytes = 0
        self.stats = dict.fromkeys((
            "datagrams", "other_camera_datagrams", "invalid_datagrams", "duplicate_chunks",
            "late_datagrams", "invalid_frames", "incomplete_frames", "missing_frame_ids", "complete_frames",
        ), 0)

    def _remove(self, frame_id: int) -> PendingFrame:
        entry = self.pending.pop(frame_id)
        self.buffered_bytes -= entry.size
        return entry

    def _invalidate(self, entry: PendingFrame) -> None:
        self.stats["invalid_frames"] += 1
        entry.invalid = True
        self.buffered_bytes -= entry.size
        entry.chunks.clear()
        entry.size = 0

    def _drain(self, now: float, *, finish: bool = False) -> list[tuple[int, bytes]]:
        completed = []
        while self.pending:
            entry = self.pending.get(self.expected_id)
            if entry is None:
                next_id = min(self.pending, key=lambda frame_id: (frame_id - self.expected_id) % 65536)
                entry = self.pending[next_id]
                if not finish and len(self.pending) <= self.max_pending and now - entry.created_at < self.frame_timeout:
                    break
                self.stats["missing_frame_ids"] += (next_id - self.expected_id) % 65536
                self.expected_id = next_id
                continue
            if not entry.invalid and len(entry.chunks) == entry.total:
                frame_id = self.expected_id
                entry = self._remove(frame_id)
                completed.append((frame_id, b"".join(entry.chunks[index] for index in range(entry.total))))
                self.stats["complete_frames"] += 1
            elif entry.invalid or finish or now - entry.created_at >= self.frame_timeout or len(self.pending) > self.max_pending:
                self._remove(self.expected_id)
                self.stats["incomplete_frames"] += 1
            else:
                break
            self.expected_id = (self.expected_id + 1) % 65536
        return completed

    def feed(self, datagram: bytes, now: float) -> list[tuple[int, bytes]]:
        completed = self._drain(now)
        self.stats["datagrams"] += 1
        if len(datagram) <= CHUNK_HEADER.size:
            self.stats["invalid_datagrams"] += 1
            return completed
        camera_id, frame_id, index, total = CHUNK_HEADER.unpack_from(datagram)
        if camera_id != self.camera_id:
            self.stats["other_camera_datagrams"] += 1
            return completed
        if total == 0 or total > self.max_chunks or index >= total:
            self.stats["invalid_datagrams"] += 1
            return completed
        if self.expected_id is None:
            self.expected_id = frame_id
        if (frame_id - self.expected_id) % 65536 >= 32768:
            self.stats["late_datagrams"] += 1
            return completed
        entry = self.pending.setdefault(frame_id, PendingFrame(total, now))
        if entry.invalid:
            return completed
        payload = datagram[CHUNK_HEADER.size:]
        if entry.total != total:
            self._invalidate(entry)
        elif index in entry.chunks:
            self.stats["duplicate_chunks"] += 1
            if entry.chunks[index] != payload:
                self._invalidate(entry)
        elif entry.size + len(payload) > self.max_frame_bytes or self.buffered_bytes + len(payload) > self.max_buffer_bytes:
            self._invalidate(entry)
        else:
            entry.chunks[index] = payload
            entry.size += len(payload)
            self.buffered_bytes += len(payload)
        return completed + self._drain(now)

    def expire(self, now: float) -> list[tuple[int, bytes]]:
        return self._drain(now)

    def finish(self, now: float) -> list[tuple[int, bytes]]:
        return self._drain(now, finish=True)


class ProbeBlocked(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


def _read_output(stream) -> str:
    length = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if length <= MAX_OUTPUT_BYTES:
        content = stream.read()
    else:
        content = stream.read(MAX_OUTPUT_BYTES // 2)
        stream.seek(-MAX_OUTPUT_BYTES // 2, os.SEEK_END)
        content += b"\n[output truncated]\n" + stream.read()
    return content.decode("utf-8", errors="replace")


def run_process(arguments: list[str], timeout: float) -> ProcessResult:
    environment = os.environ.copy()
    environment.pop("FFREPORT", None)
    environment["AV_LOG_FORCE_NOCOLOR"] = "1"
    environment["LC_ALL"] = "C"
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            completed = subprocess.run(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(None, _read_output(stdout), _read_output(stderr), True)
        except OSError as error:
            raise ProbeBlocked("tool_unavailable", str(error)) from error
        return ProcessResult(completed.returncode, _read_output(stdout), _read_output(stderr))


def check_tools(ffprobe: str, ffmpeg: str, timeout: float) -> dict:
    paths = {name: shutil.which(value) for name, value in (("ffprobe", ffprobe), ("ffmpeg", ffmpeg))}
    missing = [name for name, value in paths.items() if value is None]
    if missing:
        raise ProbeBlocked("missing_tools", "Missing executables: " + ", ".join(missing))
    tools = {}
    for name, executable in paths.items():
        version = run_process([executable, "-version"], min(timeout, 5.0))
        if version.timed_out or version.returncode != 0 or not version.stdout.strip():
            raise ProbeBlocked("tool_unavailable", f"Cannot run {name}: {version.stderr}")
        tools[name] = {"path": executable, "version": version.stdout.splitlines()[0]}
    decoders = run_process([paths["ffmpeg"], "-hide_banner", "-decoders"], min(timeout, 5.0))
    has_h264 = any(
        len(fields) >= 2 and fields[0].startswith("V") and fields[1] == "h264"
        for fields in (line.split() for line in decoders.stdout.splitlines())
    )
    if decoders.timed_out or decoders.returncode != 0 or not has_h264:
        raise ProbeBlocked("missing_decoder", "FFmpeg has no usable listed H.264 software decoder")
    tools["ffmpeg"]["h264_decoder_listed"] = True
    return tools


def _snapshot(source: Path, destination: Path, max_bytes: int) -> dict:
    if not source.is_file():
        raise ProbeBlocked("invalid_input", "Input must be a local regular file, not a URL, device or pipe")
    digest = hashlib.sha256()
    total_bytes = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
            raise ProbeBlocked("empty_input", "Input must be a nonempty regular file")
        if before.st_size > max_bytes:
            raise ProbeBlocked("input_limit", f"Input exceeds the {max_bytes}-byte resource limit")
        while chunk := reader.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise ProbeBlocked("input_limit", "Input grew beyond the configured resource limit")
            digest.update(chunk)
            writer.write(chunk)
        after = os.fstat(reader.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or total_bytes != before.st_size:
            raise ProbeBlocked("input_changed", "Input changed during capture; export a finalized sample first")
    return {
        "path": str(source),
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
        "source_mtime_ns": before.st_mtime_ns,
        "transcoded": False,
        "origin_verified": False,
    }


def _input_options() -> list[str]:
    return [
        "-protocol_whitelist", "file,pipe",
        "-format_whitelist", MEDIA_FORMATS,
        "-probesize", "5000000",
        "-analyzeduration", "5000000",
        "-max_streams", "16",
        "-max_alloc", "268435456",
    ]


def _decoded_frames(progress: str) -> int:
    frame_count = 0
    for line in progress.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "frame" and value.strip().isdigit():
            frame_count = max(frame_count, int(value.strip()))
    return frame_count


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(content)


def _save_preview(snapshot: Path, output: Path, ffmpeg: str, video_stream: int, timeout: float) -> dict:
    preview = snapshot.parent / "preview.png"
    result = run_process(
        [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-n"]
        + _input_options()
        + [
            "-threads", "1", "-i", str(snapshot), "-map", f"0:v:{video_stream}",
            "-frames:v", "1", "-threads", "1", "-c:v", "png", "-update", "1", str(preview),
        ],
        timeout,
    )
    if result.timed_out or result.returncode != 0 or result.stderr.strip() or not preview.is_file():
        raise ProbeBlocked("preview_failed", result.stderr or "Preview did not finish")
    image = preview.read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProbeBlocked("preview_failed", "Decoder did not produce a PNG image")
    _write_private(output, image)
    return {"path": str(output), "sha256": hashlib.sha256(image).hexdigest()}


def assess_video(
    codec_name: str | None,
    decoded_frames: int,
    *,
    decode_complete: bool,
    decode_errors: bool,
    min_frames: int = 2,
) -> dict:
    """Keep codec evidence separate from a complete, multi-frame decode."""
    if decoded_frames < 0 or min_frames < 2:
        raise ValueError("Frame counts must be nonnegative and min_frames >= 2")
    if not codec_name or decoded_frames == 0:
        codec_status = "BLOCKED"
    else:
        codec_status = "PASS" if codec_name == "h264" else "FAIL"

    if decode_errors:
        decode_status = "FAIL"
    elif not decode_complete or decoded_frames < min_frames:
        decode_status = "BLOCKED"
    else:
        decode_status = "PASS"

    if codec_status == "BLOCKED":
        status = "BLOCKED"
    elif "FAIL" in (codec_status, decode_status):
        status = "FAIL"
    elif decode_status == "BLOCKED":
        status = "BLOCKED"
    else:
        status = "PASS"
    return {
        "status": status,
        "codec_status": codec_status,
        "decode_status": decode_status,
        "detected_codec": codec_name,
        "decoded_frames": decoded_frames,
        "decode_complete": decode_complete,
        "decode_errors": decode_errors,
        "min_frames": min_frames,
    }


def inspect_video(
    source: str | Path,
    *,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    timeout: float = 30.0,
    max_bytes: int = 64 * 1024 * 1024,
    max_pixels: int = 4096 * 4096,
    min_frames: int = 2,
    video_stream: int = 0,
    preview_path: Path | None = None,
) -> dict:
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "mode": "offline",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Exported sample only; not proof of an Oli/PICO session or network performance",
        "quic_connection_attempted": False,
        "live_capture_attempted": False,
        "decode_attempted": False,
        "pico_session_verified": False,
        "preview": None,
        **assess_video(None, 0, decode_complete=False, decode_errors=False),
    }
    try:
        if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0 or max_pixels <= 0 or min_frames < 2 or video_stream < 0:
            raise ProbeBlocked("invalid_limits", "Positive finite resource limits, min_frames >= 2 and video_stream >= 0 are required")
        if "://" in str(source) or str(source) == "-":
            raise ProbeBlocked("invalid_input", "Only local exported media is supported, not network URLs or stdin")
        if preview_path is not None and (preview_path.exists() or preview_path.resolve().is_relative_to(PROJECT_ROOT)):
            raise ProbeBlocked("invalid_output", "Preview must be a new file outside the repository")
        source_path = Path(source).expanduser().resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="oli-video-probe-") as scratch:
            snapshot = Path(scratch) / "capture.bin"
            report["sample"] = _snapshot(source_path, snapshot, max_bytes)
            tools = check_tools(ffprobe, ffmpeg, timeout)
            report["tools"] = tools
            probe = run_process(
                [tools["ffprobe"]["path"], "-v", "error"]
                + _input_options()
                + [
                    "-select_streams", f"v:{video_stream}",
                    "-show_entries", "stream=index,codec_name,codec_type,profile,level,width,height,pix_fmt:format=format_name",
                    "-of", "json", "-i", str(snapshot),
                ],
                timeout,
            )
            report["probe_log"] = probe.stderr
            if probe.timed_out:
                raise ProbeBlocked("probe_timeout", "Format detection timed out")
            if probe.returncode != 0:
                raise ProbeBlocked("probe_failed", "Cannot identify the media; check framing and codec initialization data")
            try:
                metadata = json.loads(probe.stdout)
            except json.JSONDecodeError as error:
                raise ProbeBlocked("invalid_probe_result", "FFprobe did not return complete JSON") from error
            if not isinstance(metadata, dict) or not isinstance(metadata.get("streams"), list) or len(metadata["streams"]) != 1:
                raise ProbeBlocked("no_video", "Selected video stream is missing or ambiguous")
            stream = metadata["streams"][0]
            if not isinstance(stream, dict):
                raise ProbeBlocked("invalid_probe_result", "Invalid video stream metadata")
            report["media"] = metadata
            codec_name = stream.get("codec_name")
            report["detected_codec"] = codec_name
            width, height = stream.get("width", 0), stream.get("height", 0)
            if not isinstance(codec_name, str) or not codec_name or not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ProbeBlocked("missing_video_parameters", "No usable codec and dimensions; initialization data may be missing")
            if width * height > max_pixels:
                raise ProbeBlocked("pixel_limit", "Video dimensions exceed the configured resource limit")

            report["decode_attempted"] = True
            decoded = run_process(
                [tools["ffmpeg"]["path"], "-hide_banner", "-nostdin", "-v", "error", "-xerror"]
                + _input_options()
                + [
                    "-err_detect", "explode", "-threads", "1", "-i", str(snapshot),
                    "-map", f"0:v:{video_stream}", "-an", "-sn", "-dn", "-vsync", "0",
                    "-progress", "pipe:1", "-nostats", "-f", "null", "-",
                ],
                timeout,
            )
            report["decode_log"] = decoded.stderr
            report["decode_returncode"] = decoded.returncode
            report["decode_timed_out"] = decoded.timed_out
            complete = not decoded.timed_out and decoded.returncode == 0 and "progress=end" in decoded.stdout.splitlines()
            report.update(assess_video(
                codec_name,
                _decoded_frames(decoded.stdout),
                decode_complete=complete,
                decode_errors=bool(decoded.stderr.strip()) or (not decoded.timed_out and decoded.returncode != 0),
                min_frames=min_frames,
            ))
            if decoded.timed_out:
                report["status"] = "BLOCKED"
                report["reason_code"] = "decode_timeout"
                report["reason"] = "Decode budget exhausted; no complete-sample verdict"
            elif report["decoded_frames"] == 0:
                report["reason_code"] = "no_decoded_frames"
                report["reason"] = "No decoded image; codec labels alone are insufficient evidence"
            elif report["decode_errors"]:
                report["reason_code"] = "decode_failed"
                report["reason"] = "Codec detected, but actual decoding reported errors"
            elif report["codec_status"] == "FAIL":
                report["reason_code"] = "not_h264"
                report["reason"] = f"Successfully decoded a different codec: {codec_name}"
            elif report["status"] == "BLOCKED":
                report["reason_code"] = "insufficient_frames"
                report["reason"] = "Not enough frames or no complete decode progress record"
            else:
                report["reason_code"] = "sample_verified"
                report["reason"] = "Sample is H.264 and decoded completely; original session and visual correctness still require evidence"
            if preview_path is not None and report["status"] == "PASS":
                report["preview"] = _save_preview(snapshot, preview_path, tools["ffmpeg"]["path"], video_stream, timeout)
    except ProbeBlocked as error:
        report.update(status="BLOCKED", reason_code=error.code, reason=str(error))
    except OSError as error:
        report.update(status="BLOCKED", reason_code="file_error", reason=str(error))
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ffprobe", default="ffprobe", help="FFprobe executable path or command name")
    common.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable path or command name")
    common.add_argument("--timeout", type=float, default=30.0, help="Wall-clock limit per media subprocess in seconds")
    commands.add_parser("doctor", parents=[common], help="Check tools, without reading media or opening network connections")
    inspect = commands.add_parser("inspect", parents=[common], help="Probe and decode an already exported local video sample")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--video-stream", type=int, default=0, help="Zero-based video stream number; inspect each camera separately")
    inspect.add_argument("--min-frames", type=int, default=2, help="Tool evidence minimum, not a product acceptance threshold")
    inspect.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    inspect.add_argument("--max-pixels", type=int, default=4096 * 4096)
    inspect.add_argument("--output-dir", type=Path, help="New private evidence directory outside this repository")
    inspect.add_argument("--save-frame", action="store_true", help="Save one decoded PNG in --output-dir; opt-in only")
    commands.add_parser("receive", help="Report missing Oli protocol prerequisites; networking is NOT implemented")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    output_dir = None
    try:
        if arguments.command == "receive":
            report = {
                "schema_version": 1, "mode": "receive", "status": "BLOCKED",
                "reason_code": "protocol_required",
                "reason": "Oli QUIC receiver is not implemented without its application protocol",
                "quic_connection_attempted": False, "live_capture_attempted": False,
                "requirements": PROTOCOL_REQUIREMENTS,
            }
        elif not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
            raise ProbeBlocked("invalid_limits", "timeout must be positive and finite")
        elif arguments.command == "doctor":
            report = {
                "schema_version": 1, "mode": "doctor", "status": "PASS",
                "scope": "Tool availability only; no video or network verified",
                "tools": check_tools(arguments.ffprobe, arguments.ffmpeg, arguments.timeout),
                "quic_connection_attempted": False,
            }
        else:
            if arguments.save_frame and arguments.output_dir is None:
                parser.error("--save-frame requires --output-dir")
            if arguments.output_dir is not None:
                candidate = arguments.output_dir.expanduser().resolve()
                if candidate.is_relative_to(PROJECT_ROOT):
                    raise ProbeBlocked("invalid_output", "Evidence must be stored outside the repository")
                candidate.mkdir(mode=0o700, parents=True, exist_ok=False)
                output_dir = candidate
            preview = output_dir / "preview.png" if arguments.save_frame else None
            report = inspect_video(
                arguments.input, ffprobe=arguments.ffprobe, ffmpeg=arguments.ffmpeg,
                timeout=arguments.timeout, video_stream=arguments.video_stream,
                min_frames=arguments.min_frames, max_bytes=arguments.max_bytes,
                max_pixels=arguments.max_pixels, preview_path=preview,
            )
            if output_dir is not None:
                report["report_path"] = str(output_dir / "report.json")
                _write_private(output_dir / "report.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    except (OSError, ProbeBlocked) as error:
        report = {
            "schema_version": 1, "mode": arguments.command, "status": "BLOCKED",
            "reason_code": error.code if isinstance(error, ProbeBlocked) else "file_error",
            "reason": str(error), "quic_connection_attempted": False,
        }
    except KeyboardInterrupt:
        report = {"schema_version": 1, "mode": arguments.command, "status": "BLOCKED", "reason_code": "interrupted"}
    print(json.dumps(report, indent=2))
    print(
        f"{report['status']}: {report.get('reason', report.get('scope', 'Interrupted'))}",
        file=sys.stderr,
    )
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())