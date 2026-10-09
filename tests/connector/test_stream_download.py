"""T5: streamed file downloads with a size cap (SPEC §12.4, §12.5 step 3, AM-4 as amended).

Expectations come from the spec and the arithmetic of base64, never from src/: a file of at most ``m`` bytes has a
base64 payload (whitespace excluded) of at most ``4 * ceil(m / 3)`` characters.
"""

import base64
import json
import logging
import math
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import test_qb_capture as cap
import test_qb_client as qc

from inspection_reconcile.adapters.mapping import load_mapping
from inspection_reconcile.adapters.qb_capture import capture
from inspection_reconcile.adapters.qb_client import (
    LOGGER_NAME,
    FileTooLarge,
    QuickbaseClient,
    QuickbaseHTTPError,
)
from inspection_reconcile.adapters.qb_export import normalize
from inspection_reconcile.errors import RunError

FILE = ("bsyn00003", 201, 13, 1)


def cap_chars(max_bytes: int) -> int:
    return 4 * math.ceil(max_bytes / 3)


def payload_of(n: int) -> bytes:
    return bytes((i * 37 + 11) % 256 for i in range(n))


def chunks(data: bytes, size: int) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


class Counted:
    """A response body that yields ``data`` in chunks and counts how many bytes ever left the source."""

    def __init__(self, data: bytes, size: int) -> None:
        self.data, self.size, self.sent = data, size, 0

    def __iter__(self) -> Iterator[bytes]:
        for part in chunks(self.data, self.size):
            self.sent += len(part)
            yield part


def client_for(rec: qc.Recorder, ft: qc.FakeTime) -> QuickbaseClient:
    return QuickbaseClient(
        qc.REALM,
        qc.TOKEN,
        user_agent=qc.UA,
        transport=httpx.MockTransport(rec),
        clock=ft.clock,
        sleep=ft.sleep,
    )


def body(data: bytes, size: int = 5, **headers: str) -> qc.Outcome:
    """A fresh streamed 200 response per request."""
    return lambda request: httpx.Response(200, content=chunks(data, size), headers=headers)


# -- the boundary: both checks agree ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_bytes", [9, 10, 11, 12, 1000])
@pytest.mark.parametrize("extra", [0, 1, 2, 3])
def test_the_limit_is_exact_at_the_boundary(max_bytes: int, extra: int) -> None:
    payload = payload_of(max_bytes + extra)
    encoded = base64.b64encode(payload)
    ft = qc.FakeTime()
    with client_for(qc.Recorder(body(encoded)), ft) as client:
        if extra == 0:
            assert client.download_file(*FILE, max_bytes=max_bytes) == (payload, None)
            return
        with pytest.raises(FileTooLarge) as info:
            client.download_file(*FILE, max_bytes=max_bytes)
    assert info.value.code == "QB_FILE_TOO_LARGE"
    whole_body_read = len(encoded) <= cap_chars(max_bytes)
    assert info.value.size == (len(payload) if whole_body_read else None)


@pytest.mark.parametrize("size", [1, 2, 3, 4, 7, 76, 4096])
def test_base64_split_across_chunks_and_lines_decodes(size: int) -> None:
    payload = bytes(range(256)) * 5
    encoded = base64.b64encode(payload)
    wrapped = b"\r\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76)) + b"\n"
    assert len(wrapped) > cap_chars(len(payload))  # whitespace must not count toward the cap
    for max_bytes in (None, len(payload)):
        with client_for(qc.Recorder(body(wrapped, size)), qc.FakeTime()) as client:
            content, _ = client.download_file(*FILE, max_bytes=max_bytes)
        assert content == payload


def test_without_max_bytes_a_large_file_is_returned_whole() -> None:
    payload = payload_of(300_000)
    with client_for(qc.Recorder(body(base64.b64encode(payload), 8192)), qc.FakeTime()) as client:
        assert client.download_file(*FILE)[0] == payload


def test_max_bytes_must_be_a_positive_integer() -> None:
    with client_for(qc.Recorder(body(b"")), qc.FakeTime()) as client:
        for bad in (0, -1, True, 1.5):
            with pytest.raises(ValueError):
                client.download_file(*FILE, max_bytes=bad)  # type: ignore[arg-type]


# -- memory stays O(cap) -------------------------------------------------------------------------------------


def test_a_body_ten_times_the_limit_is_never_read_beyond_the_cap() -> None:
    max_bytes, chunk = 10_000, 1024
    source = Counted(base64.b64encode(payload_of(10 * max_bytes)), chunk)
    rec = qc.Recorder(lambda request: httpx.Response(200, content=iter(source)))
    ft = qc.FakeTime()
    with client_for(rec, ft) as client, pytest.raises(FileTooLarge) as info:
        client.download_file(*FILE, max_bytes=max_bytes)
    assert info.value.size is None
    assert source.sent <= cap_chars(max_bytes) + chunk  # at most one chunk past the cap ever arrived
    assert len(rec.requests) == 1 and ft.sleeps == []  # a too-large file is not retried


# -- the same retry machinery, decided on the status line ----------------------------------------------------------


def test_download_retries_are_decided_on_the_status_line() -> None:
    payload = payload_of(500)
    rec = qc.Recorder(
        qc.respond(503), qc.respond(429, headers={"retry-after": "7"}), body(base64.b64encode(payload))
    )
    ft = qc.FakeTime()
    with client_for(rec, ft) as client:
        assert client.download_file(*FILE, max_bytes=500)[0] == payload
    assert len(rec.requests) == 3
    assert ft.sleeps == [1.0, 7.0]


def test_a_transport_error_while_the_body_arrives_is_retried() -> None:
    payload = payload_of(600)
    encoded = base64.b64encode(payload)

    def dropped(request: httpx.Request) -> httpx.Response:
        def stream() -> Iterator[bytes]:
            yield encoded[:100]
            raise httpx.ReadError("synthetic: the connection dropped", request=request)

        return httpx.Response(200, content=stream())

    rec = qc.Recorder(dropped, body(encoded))
    ft = qc.FakeTime()
    with client_for(rec, ft) as client:
        assert client.download_file(*FILE, max_bytes=600)[0] == payload
    assert len(rec.requests) == 2
    assert ft.sleeps == [1.0]


def test_download_errors_keep_their_codes_and_read_at_most_a_bounded_error_body() -> None:
    ft = qc.FakeTime()
    with client_for(qc.Recorder(qc.respond(403, json={"message": "Access denied"})), ft) as client:
        with pytest.raises(RunError) as denied:
            client.download_file(*FILE, max_bytes=100)
    assert denied.value.code == "QB_PERMISSION"

    junk = Counted(b"x" * (1024 * 1024), 4096)  # a 1 MiB error body
    rec = qc.Recorder(lambda request: httpx.Response(404, content=iter(junk)))
    with client_for(rec, ft) as client:
        with pytest.raises(QuickbaseHTTPError) as missing:
            client.download_file(*FILE, max_bytes=100)
    assert missing.value.status == 404 and len(rec.requests) == 1
    assert junk.sent <= 64 * 1024 + 4096
    assert ft.sleeps == []


def test_the_token_stays_out_of_download_errors_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    echo = {"message": f"bad token {qc.TOKEN}", "description": f"Authorization: QB-USER-TOKEN {qc.TOKEN}"}
    with client_for(qc.Recorder(qc.respond(400, json=echo)), qc.FakeTime()) as client:
        with pytest.raises(QuickbaseHTTPError) as info:
            client.download_file(*FILE, max_bytes=100)
    assert qc.TOKEN not in str(info.value)
    assert qc.TOKEN not in caplog.text


# -- capture: too_large without holding the file; a file at the limit is captured -----------------------------------


MAPPING = load_mapping(cap.DEMO)


def run_capture(app: cap.MockApp, out: Path, limit: int) -> dict:
    ft = cap.FakeTime()
    transport = httpx.MockTransport(app)
    with QuickbaseClient(
        cap.REALM,
        cap.TOKEN,
        user_agent="t/1",
        transport=transport,
        max_attempts=3,
        clock=ft.clock,
        sleep=ft.sleep,
    ) as client:
        return capture(
            client, cap.config_with(limits__max_file_bytes=limit), MAPPING, out, now=lambda: cap.NOW
        )


def s16_sizes() -> dict[int, int]:
    manifest = json.loads((cap.S16 / "capture-manifest.json").read_text(encoding="utf-8"))
    return {f["record_id"]: f["bytes"] for f in manifest["files"]}


def test_capture_stops_a_huge_file_at_the_limit_and_records_no_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    limit, chunk = max(s16_sizes().values()), 4096  # every real S16 file fits
    huge = Counted(base64.b64encode(payload_of(10 * limit)), chunk)
    app = cap.MockApp()
    app.hooks.append(
        lambda request, _body: (
            httpx.Response(200, content=iter(huge))
            if request.url.path == "/v1/files/bsyn00003/201/13/1"
            else None
        )
    )
    export = tmp_path / "export"
    manifest = run_capture(app, export, limit)
    entry = next(f for f in manifest["files"] if f["record_id"] == 201)
    assert {k: entry[k] for k in ("status", "path", "bytes", "sha256")} == {
        "status": "too_large",
        "path": None,
        "bytes": None,
        "sha256": None,
    }
    assert huge.sent <= cap_chars(limit) + chunk
    assert {f["status"] for f in manifest["files"] if f["record_id"] != 201} == {"captured"}
    assert manifest["datasets"]["evidence_files"]["basis"] == ["extraction_interrupted"]
    normalize(export, MAPPING, tmp_path / "snapshot")  # AM-4: bytes null on too_large is valid
    for path in export.rglob("*"):
        if path.is_file():
            assert cap.TOKEN.encode() not in path.read_bytes(), path
    assert cap.TOKEN not in caplog.text


def test_capture_takes_a_file_exactly_at_the_limit(tmp_path: Path) -> None:
    sizes = s16_sizes()
    limit = sizes[202]
    manifest = run_capture(cap.MockApp(), tmp_path / "export", limit)
    by_record = {f["record_id"]: f for f in manifest["files"]}
    assert by_record[202]["status"] == "captured" and by_record[202]["bytes"] == limit
    for rid, size in sizes.items():
        if size > limit:
            assert by_record[rid]["status"] == "too_large"
            expected = (
                size if math.ceil(size / 3) == math.ceil(limit / 3) else None
            )  # whole body read, or not
            assert by_record[rid]["bytes"] == expected
        else:
            assert by_record[rid]["status"] == "captured"
