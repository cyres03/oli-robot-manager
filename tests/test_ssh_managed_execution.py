import threading

import pytest

from network.ssh_client import (
    SshClient,
    SshExecutionCancelled,
    SshOutputLimitError,
)


class FakeChannel:
    def __init__(self, stdout_chunks=None, stderr_chunks=None, exit_code=0, finish=True):
        self.stdout_chunks = list(stdout_chunks or [])
        self.stderr_chunks = list(stderr_chunks or [])
        self.exit_code = exit_code
        self.finish = finish
        self.closed = False

    def recv_ready(self):
        return bool(self.stdout_chunks)

    def recv(self, _size):
        return self.stdout_chunks.pop(0)

    def recv_stderr_ready(self):
        return bool(self.stderr_chunks)

    def recv_stderr(self, _size):
        return self.stderr_chunks.pop(0)

    def exit_status_ready(self):
        return self.finish and not self.stdout_chunks and not self.stderr_chunks

    def recv_exit_status(self):
        return self.exit_code

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, channel):
        self.channel = channel


class FakeConnection:
    def __init__(self, channel, termination_exit_code=0):
        self.channel = channel
        self.termination_exit_code = termination_exit_code
        self.commands = []

    def exec_command(self, command, timeout=None):
        self.commands.append((command, timeout))
        if "kill -TERM" in command:
            termination = FakeChannel(exit_code=self.termination_exit_code)
            return None, FakeStream(termination), FakeStream(termination)
        return None, FakeStream(self.channel), FakeStream(self.channel)


def _client(channel):
    client = SshClient("host", "user")
    connection = FakeConnection(channel)
    client._client = connection
    return client, connection


def test_managed_execution_streams_compound_command_output():
    channel = FakeChannel(
        stdout_chunks=[b"__OLI_TEST_PID__=42\nnode=test\ncores=8\n"],
        stderr_chunks=[b"warning\n"],
    )
    client, connection = _client(channel)
    lines = []

    result = client.execute_managed(
        "printf 'node='; hostname; printf 'cores='; nproc",
        lambda line, stream: lines.append((line, stream)),
        threading.Event(),
        timeout=5,
    )

    assert result.exit_code == 0
    assert result.stdout == "node=test\ncores=8"
    assert result.stderr == "warning"
    assert lines == [
        ("node=test", "stdout"),
        ("cores=8", "stdout"),
        ("warning", "stderr"),
    ]
    assert "setsid sh -c" in connection.commands[0][0]
    assert "hostname" in connection.commands[0][0]


def test_managed_execution_cancels_remote_process_group():
    channel = FakeChannel(
        stdout_chunks=[b"__OLI_TEST_PID__=77\n"],
        finish=False,
    )
    client, connection = _client(channel)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(SshExecutionCancelled):
        client.execute_managed(
            "long-running-command",
            lambda *_: None,
            cancel,
            timeout=5,
        )

    assert any("pid=77" in command and "kill -TERM" in command for command, _ in connection.commands)
    assert channel.closed is True


def test_managed_execution_cancels_via_pid_file_before_marker_arrives():
    channel = FakeChannel(finish=False)
    client, connection = _client(channel)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(SshExecutionCancelled):
        client.execute_managed(
            "long-running-command",
            lambda *_: None,
            cancel,
            timeout=5,
        )

    termination = next(
        command for command, _ in connection.commands if "kill -TERM" in command
    )
    assert "pid_file=/tmp/.oli-robot-manager-" in termination
    assert "pkill" not in termination
    assert channel.closed is True


def test_managed_execution_reports_unconfirmed_termination():
    channel = FakeChannel(finish=False)
    client = SshClient("host", "user")
    connection = FakeConnection(channel, termination_exit_code=4)
    client._client = connection
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(SshExecutionCancelled, match="无法确认"):
        client.execute_managed(
            "long-running-command",
            lambda *_: None,
            cancel,
            timeout=5,
        )


def test_managed_execution_times_out_and_terminates_remote_group():
    channel = FakeChannel(stdout_chunks=[b"__OLI_TEST_PID__=91\n"], finish=False)
    client, connection = _client(channel)

    with pytest.raises(TimeoutError, match="远端进程已终止"):
        client.execute_managed(
            "long-running-command",
            lambda *_: None,
            threading.Event(),
            timeout=0,
        )

    assert any("pid=91" in command and "kill -TERM" in command for command, _ in connection.commands)
    assert channel.closed is True


def test_managed_execution_enforces_output_limit():
    channel = FakeChannel(
        stdout_chunks=[b"__OLI_TEST_PID__=88\n" + b"x" * 100],
    )
    client, connection = _client(channel)

    with pytest.raises(SshOutputLimitError):
        client.execute_managed(
            "noisy-command",
            lambda *_: None,
            threading.Event(),
            timeout=5,
            max_output_bytes=50,
        )

    assert channel.closed is True
    assert any("pid=88" in command and "kill -TERM" in command for command, _ in connection.commands)