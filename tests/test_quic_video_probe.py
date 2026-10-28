import hashlib
import json
import sys

import pytest

from scripts import quic_video_probe as probe
from scripts.quic_video_probe import assess_video


@pytest.mark.parametrize(
    "codec_name, frames, complete, errors, expected",
    [
        ("h264", 12, True, False, ("PASS", "PASS", "PASS")),
        ("mjpeg", 12, True, False, ("FAIL", "FAIL", "PASS")),
        ("hevc", 12, True, False, ("FAIL", "FAIL", "PASS")),
        ("h264", 0, True, False, ("BLOCKED", "BLOCKED", "BLOCKED")),
        ("h264", 0, False, True, ("BLOCKED", "BLOCKED", "FAIL")),
        ("h264", 1, True, False, ("BLOCKED", "PASS", "BLOCKED")),
        ("h264", 4, True, True, ("FAIL", "PASS", "FAIL")),
        ("h264", 4, False, False, ("BLOCKED", "PASS", "BLOCKED")),
        (None, 0, False, False, ("BLOCKED", "BLOCKED", "BLOCKED")),
    ],
)
def test_assessment_requires_decoded_frames(codec_name, frames, complete, errors, expected):
    result = assess_video(
        codec_name, frames, decode_complete=complete, decode_errors=errors
    )

    assert (result["status"], result["codec_status"], result["decode_status"]) == expected


def test_assessment_rejects_invalid_frame_limits():
    with pytest.raises(ValueError):
        assess_video("h264", 0, decode_complete=True, decode_errors=False, min_frames=1)


@pytest.fixture
def fake_tools(monkeypatch):
    monkeypatch.setattr(probe, "check_tools", lambda *arguments: {
        "ffprobe": {"path": "ffprobe", "version": "test"},
        "ffmpeg": {"path": "ffmpeg", "version": "test"},
    })


@pytest.fixture
def exported_sample(tmp_path):
    sample = tmp_path / "export.bin"
    sample.write_bytes(b"protocol-independent fixture")
    return sample


def metadata(codec="h264", width=320, height=240):
    return json.dumps({"streams": [{"index": 0, "codec_name": codec, "width": width, "height": height}]})


def test_inspect_uses_auto_detection_and_real_decode(fake_tools, exported_sample, monkeypatch):
    calls = []

    def runner(arguments, timeout):
        calls.append(arguments)
        if arguments[0] == "ffprobe":
            return probe.ProcessResult(0, metadata())
        return probe.ProcessResult(0, "frame=12\nprogress=end\n")

    monkeypatch.setattr(probe, "run_process", runner)
    result = probe.inspect_video(exported_sample)

    assert result["status"] == "PASS"
    assert result["sample"]["sha256"] == hashlib.sha256(exported_sample.read_bytes()).hexdigest()
    assert result["sample"]["origin_verified"] is False
    assert result["quic_connection_attempted"] is False
    assert result["pico_session_verified"] is False
    assert result["decoded_frames"] == 12
    assert "-f" not in calls[0]
    assert "copy" not in calls[1]
    assert calls[1][-3:] == ["-f", "null", "-"]
    assert calls[1][calls[1].index("-protocol_whitelist") + 1] == "file,pipe"
    assert calls[1][calls[1].index("-vsync") + 1] == "0"


@pytest.mark.parametrize(
    "process, status, reason",
    [
        (probe.ProcessResult(0, "frame=0\nprogress=end\n"), "BLOCKED", "no_decoded_frames"),
        (probe.ProcessResult(0, "frame=1\nprogress=end\n"), "BLOCKED", "insufficient_frames"),
        (probe.ProcessResult(0, "frame=4\nprogress=end\n", "corrupt frame"), "FAIL", "decode_failed"),
        (probe.ProcessResult(1, "frame=4\nprogress=end\n"), "FAIL", "decode_failed"),
        (probe.ProcessResult(None, "frame=4\n", timed_out=True), "BLOCKED", "decode_timeout"),
    ],
)
def test_decode_failure_reports(fake_tools, exported_sample, monkeypatch, process, status, reason):
    monkeypatch.setattr(probe, "run_process", lambda arguments, timeout: probe.ProcessResult(0, metadata()) if arguments[0] == "ffprobe" else process)

    result = probe.inspect_video(exported_sample)

    assert result["status"] == status
    assert result["reason_code"] == reason


@pytest.mark.parametrize(
    "process, reason",
    [
        (probe.ProcessResult(1, stderr="unknown format"), "probe_failed"),
        (probe.ProcessResult(None, timed_out=True), "probe_timeout"),
        (probe.ProcessResult(0, "bad json"), "invalid_probe_result"),
        (probe.ProcessResult(0, "[]"), "no_video"),
        (probe.ProcessResult(0, '{"streams": []}'), "no_video"),
        (probe.ProcessResult(0, metadata(width=0)), "missing_video_parameters"),
        (probe.ProcessResult(0, metadata(width=999999)), "pixel_limit"),
    ],
)
def test_probe_failure_never_guesses_codec(fake_tools, exported_sample, monkeypatch, process, reason):
    monkeypatch.setattr(probe, "run_process", lambda *arguments: process)

    result = probe.inspect_video(exported_sample)

    assert result["status"] == "BLOCKED"
    assert result["codec_status"] == "BLOCKED"
    assert result["decode_attempted"] is False
    assert result["reason_code"] == reason


def test_empty_and_oversized_files_are_blocked(tmp_path, monkeypatch):
    def unexpected_call(*arguments):
        pytest.fail("No media process should run for invalid input")

    monkeypatch.setattr(probe, "run_process", unexpected_call)
    sample = tmp_path / "sample.bin"
    sample.touch()
    assert probe.inspect_video(sample)["reason_code"] == "empty_input"
    sample.write_bytes(b"12345")
    assert probe.inspect_video(sample, max_bytes=4)["reason_code"] == "input_limit"
    assert probe.inspect_video("https://example.invalid/video")["reason_code"] == "invalid_input"
    assert probe.inspect_video(tmp_path)["reason_code"] == "invalid_input"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_blocked(exported_sample, timeout):
    assert probe.inspect_video(exported_sample, timeout=timeout)["reason_code"] == "invalid_limits"


def test_missing_tools_are_explicit(exported_sample, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    result = probe.inspect_video(exported_sample)
    assert result["reason_code"] == "missing_tools"
    assert result["decode_attempted"] is False


def test_receive_is_an_explicit_non_networking_gate(monkeypatch, capsys):
    def unexpected_call(*arguments, **keywords):
        pytest.fail("The protocol gate must not start a process or a connection")

    monkeypatch.setattr(probe.subprocess, "run", unexpected_call)
    assert probe.main(["receive"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert result["quic_connection_attempted"] is False
    assert result["requirements"]


def test_run_process_captures_output():
    result = probe.run_process([sys.executable, "-c", "print('frame=2')"], 5)
    assert result.returncode == 0
    assert result.stdout == "frame=2\n"


def test_report_directory_is_not_overwritten(exported_sample, tmp_path, capsys):
    result = probe.main(["inspect", str(exported_sample), "--output-dir", str(tmp_path)])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["reason_code"] == "file_error"


def datagram(frame_id, index, total, payload, camera_id=0):
    return probe.CHUNK_HEADER.pack(camera_id, frame_id, index, total) + payload


def test_authentication_prefix_counts_utf8_bytes():
    token = "\u6d4b\u8bd5".encode("utf-8")
    assert probe.authentication_payload(token) == b"\x06\x00" + token
    for invalid in (b"", b"x" * 65536, b"\xff"):
        with pytest.raises(ValueError):
            probe.authentication_payload(invalid)


def test_fragment_order_does_not_change_frame():
    assembler = probe.FrameAssembler(0)
    assert assembler.feed(datagram(10, 2, 3, b"C"), 0.0) == []
    assert assembler.feed(datagram(10, 0, 3, b"A"), 0.1) == []
    assert assembler.feed(datagram(10, 1, 3, b"B"), 0.2) == [(10, b"ABC")]
    assert assembler.buffered_bytes == 0


def test_frames_are_emitted_in_order_across_id_wrap():
    assembler = probe.FrameAssembler(0)
    assert assembler.feed(datagram(65535, 1, 2, b"B"), 0.0) == []
    assert assembler.feed(datagram(0, 0, 1, b"C"), 0.1) == []
    assert assembler.feed(datagram(65535, 0, 2, b"A"), 0.2) == [(65535, b"AB"), (0, b"C")]
    assert assembler.feed(datagram(65535, 0, 2, b"A"), 0.3) == []
    assert assembler.stats["late_datagrams"] == 1


def test_duplicate_chunks_do_not_count_as_complete():
    assembler = probe.FrameAssembler(0)
    packet = datagram(1, 0, 2, b"A")
    assert assembler.feed(packet, 0.0) == []
    assert assembler.feed(packet, 0.1) == []
    assert assembler.feed(datagram(1, 1, 2, b"B"), 0.2) == [(1, b"AB")]
    assert assembler.stats["duplicate_chunks"] == 1


@pytest.mark.parametrize("changed", [datagram(1, 0, 2, b"bad"), datagram(1, 1, 3, b"B")])
def test_conflicting_chunks_invalidate_the_frame(changed):
    assembler = probe.FrameAssembler(0)
    assembler.feed(datagram(1, 0, 2, b"A"), 0.0)
    assert assembler.feed(changed, 0.1) == []
    assert assembler.feed(datagram(1, 1, 2, b"B"), 0.2) == []
    assert assembler.stats["invalid_frames"] == 1
    assert assembler.buffered_bytes == 0


def test_incomplete_and_missing_frames_expire_without_fabricating_payload():
    assembler = probe.FrameAssembler(0)
    assembler.feed(datagram(7, 0, 2, b"A"), 0.0)
    assembler.feed(datagram(9, 0, 1, b"C"), 0.1)
    assert assembler.expire(1.0) == [(9, b"C")]
    assert assembler.stats["incomplete_frames"] == 1
    assert assembler.stats["missing_frame_ids"] == 1


def test_camera_selection_and_malformed_datagrams():
    assembler = probe.FrameAssembler(1)
    assert assembler.feed(datagram(1, 0, 1, b"A", camera_id=0), 0.0) == []
    for packet in (b"short", datagram(1, 0, 0, b"A", 1), datagram(1, 2, 1, b"A", 1)):
        assert assembler.feed(packet, 0.1) == []
    assert assembler.feed(datagram(1, 0, 1, b"B", 1), 0.2) == [(1, b"B")]
    assert assembler.stats["other_camera_datagrams"] == 1
    assert assembler.stats["invalid_datagrams"] == 3


def test_reassembly_memory_and_pending_limits():
    assembler = probe.FrameAssembler(0, max_pending=1, max_frame_bytes=2, max_buffer_bytes=3)
    assembler.feed(datagram(1, 0, 2, b"AA"), 0.0)
    assert assembler.feed(datagram(1, 1, 2, b"B"), 0.1) == []
    assert assembler.stats["invalid_frames"] == 1
    assert assembler.buffered_bytes == 0
    assembler.feed(datagram(2, 0, 2, b"A"), 0.2)
    assert assembler.feed(datagram(3, 0, 1, b"B"), 0.3) == [(3, b"B")]
    assert len(assembler.pending) <= 1


def test_finish_flushes_complete_frames_only():
    assembler = probe.FrameAssembler(0)
    assembler.feed(datagram(1, 0, 2, b"A"), 0.0)
    assembler.feed(datagram(2, 0, 1, b"B"), 0.1)
    assert assembler.finish(0.2) == [(2, b"B")]
    assert assembler.buffered_bytes == 0