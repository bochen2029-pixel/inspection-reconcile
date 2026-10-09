"""The read-only Quickbase client (SPEC §12.1, §12.4) against httpx.MockTransport, with a fake clock and sleep."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from inspection_reconcile.adapters.qb_client import (
    BASE_URL,
    LOGGER_NAME,
    REDACTED,
    AllowlistError,
    QuickbaseClient,
    TokenRedactionFilter,
    check_allowlist,
    parse_content_disposition_filename,
    redact,
)
from inspection_reconcile.errors import RunError

TOKEN = "b7syn_TEST_TOKEN_0123456789abcdef"  # synthetic; it must never surface anywhere
REALM = "synthetic.quickbase.invalid"
UA = "inspection-reconcile/0.1.0"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid", "mode": ""},
    {"id": 6, "label": "Inspection ID", "fieldType": "text", "mode": ""},
]
QUERY_OK = {
    "data": [{"3": {"value": 101}, "6": {"value": "INS-001"}}],
    "fields": [
        {"id": 3, "label": "Record ID#", "type": "recordid"},
        {"id": 6, "label": "Inspection ID", "type": "text"},
    ],
    "metadata": {"totalRecords": 1, "numRecords": 1, "numFields": 2, "skip": 0, "top": 1000},
}

Outcome = Callable[[httpx.Request], httpx.Response]


def respond(status: int, **kwargs: Any) -> Outcome:
    """A fresh response per request, so retried requests never share a consumed response object."""
    return lambda request: httpx.Response(status, **kwargs)


def fail(exc_type: type[httpx.TransportError], message: str = "synthetic transport failure") -> Outcome:
    def raise_it(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)

    return raise_it


class Recorder:
    """A MockTransport handler: replays the outcomes in order (the last one repeats) and records requests."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return outcome(request)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


@pytest.fixture
def make(ft: FakeTime) -> Iterator[Callable[..., QuickbaseClient]]:
    clients: list[QuickbaseClient] = []

    def factory(recorder: Recorder, **kwargs: Any) -> QuickbaseClient:
        client = QuickbaseClient(
            REALM,
            TOKEN,
            user_agent=UA,
            transport=httpx.MockTransport(recorder),
            clock=ft.clock,
            sleep=ft.sleep,
            **kwargs,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


# -- the four operations: URLs, headers and bodies -------------------------------------------------------------


def test_operations_send_the_documented_urls_headers_and_bodies(make: Callable[..., QuickbaseClient]) -> None:
    pdf = b"%PDF-1.4 synthetic"
    rec = Recorder(
        respond(200, json=FIELDS),
        respond(200, json={"id": "bsyn00001", "name": "Obligations"}),
        respond(200, json=QUERY_OK),
        respond(
            200,
            content=base64.b64encode(pdf),
            headers={"content-disposition": 'attachment; filename="report.pdf"'},
        ),
    )
    client = make(rec)
    body = {
        "from": "bsyn00002",
        "select": [2, 3, 6],
        "where": "{3.GT.0}",
        "sortBy": [{"fieldId": 3, "order": "ASC"}],
        "options": {"skip": 0, "top": 1000},
    }

    assert client.get_fields("bsyn00001") == FIELDS
    assert client.get_table("bsyn00001", "bsyn00000") == {"id": "bsyn00001", "name": "Obligations"}
    assert client.run_query(body) == QUERY_OK
    assert client.download_file("bsyn00003", 201, 13, 1) == (pdf, "report.pdf")

    assert [(r.method, str(r.url)) for r in rec.requests] == [
        ("GET", f"{BASE_URL}/fields?tableId=bsyn00001"),
        ("GET", f"{BASE_URL}/tables/bsyn00001?appId=bsyn00000"),
        ("POST", f"{BASE_URL}/records/query"),
        ("GET", f"{BASE_URL}/files/bsyn00003/201/13/1"),
    ]
    for request in rec.requests:
        assert request.headers["QB-Realm-Hostname"] == REALM
        assert request.headers["Authorization"] == f"QB-USER-TOKEN {TOKEN}"
        assert request.headers["User-Agent"] == UA
        assert request.headers["Content-Type"] == "application/json"
    assert json.loads(rec.requests[2].content) == body
    assert rec.requests[0].content == b""


def test_base_url_is_the_verified_v1_endpoint() -> None:
    assert BASE_URL == "https://api.quickbase.com/v1"


# -- the allowlist ---------------------------------------------------------------------------------------------


def test_check_allowlist_names_exactly_the_four_operations() -> None:
    assert check_allowlist("GET", "/fields", {"tableId": "bsyn00001"}) == "getFields"
    assert check_allowlist("GET", "/tables/bsyn00001", {"appId": "bsyn00000"}) == "getTable"
    assert check_allowlist("POST", "/records/query") == "runQuery"
    assert check_allowlist("GET", "/files/bsyn00003/201/13/1") == "downloadFile"


@pytest.mark.parametrize(
    ("method", "path", "params"),
    [
        ("DELETE", "/files/bsyn00003/201/13/1", None),
        ("DELETE", "/files/bsyn00003/201/13/0", None),
        ("GET", "/files/bsyn00003/201/13/0", None),
        ("GET", "/files/bsyn00003/201/13/-1", None),
        ("GET", "/files/bsyn00003/201/13", None),
        ("GET", "/files/bsyn00003/201/13/1/extra", None),
        ("POST", "/records", None),
        ("DELETE", "/records", None),
        ("GET", "/records", None),
        ("POST", "/records/query/extra", None),
        ("PUT", "/records/query", None),
        ("GET", "/records/query", None),
        ("GET", "/apps/bsyn00000", None),
        ("POST", "/fields", {"tableId": "bsyn00001"}),
        ("GET", "/fields", None),
        ("GET", "/fields", {"tableId": "bsyn00001", "includeFieldPerms": "true"}),
        ("GET", "/fields", {"tableId": "../tables"}),
        ("GET", "/tables/bsyn00001", None),
        ("GET", "/tables/../records", {"appId": "bsyn00000"}),
        ("POST", "/records/query", {"tableId": "bsyn00001"}),
    ],
)
def test_allowlist_refuses_everything_else_before_any_io(
    make: Callable[..., QuickbaseClient], method: str, path: str, params: dict[str, str] | None
) -> None:
    rec = Recorder(respond(200, json={}))
    client = make(rec)
    with pytest.raises(AllowlistError):
        client._request(method, path, params=params)
    assert rec.requests == []


@pytest.mark.parametrize(
    "numbers",
    [(201, 13, 0), (201, 13, -1), (0, 13, 1), (201, 0, 1), (201, 13, True), (201, 13, "1"), (201, 13, 1.0)],
)
def test_download_file_refuses_version_zero_and_non_positive_ids(
    make: Callable[..., QuickbaseClient], numbers: tuple[Any, Any, Any]
) -> None:
    rec = Recorder(respond(200, content=b""))
    client = make(rec)
    with pytest.raises(AllowlistError):
        client.download_file("bsyn00003", *numbers)
    assert rec.requests == []


def test_a_table_id_cannot_smuggle_a_path(make: Callable[..., QuickbaseClient]) -> None:
    rec = Recorder(respond(200, json=FIELDS))
    client = make(rec)
    with pytest.raises(AllowlistError):
        client.get_fields("bsyn00001&appId=x")
    with pytest.raises(AllowlistError):
        client.get_table("../records", "bsyn00000")
    with pytest.raises(AllowlistError):
        client.download_file("bsyn/00003", 201, 13, 1)
    assert rec.requests == []


def test_run_query_requires_a_dict_body(make: Callable[..., QuickbaseClient]) -> None:
    rec = Recorder(respond(200, json=QUERY_OK))
    with pytest.raises(TypeError):
        make(rec).run_query([("from", "bsyn00002")])  # type: ignore[arg-type]
    assert rec.requests == []


# -- retries ---------------------------------------------------------------------------------------------------


def test_429_waits_retry_after_capped_at_max_retry_wait(
    make: Callable[..., QuickbaseClient], ft: FakeTime
) -> None:
    rec = Recorder(
        respond(429, headers={"retry-after": "120"}, json={"message": "Too Many Requests"}),
        respond(429, headers={"Retry-After": "2"}),
        respond(200, json=FIELDS),
    )
    assert make(rec, max_retry_wait_s=60.0).get_fields("bsyn00001") == FIELDS
    assert ft.sleeps == [60.0, 2.0]
    assert len(rec.requests) == 3


def test_429_without_a_usable_retry_after_falls_back_to_backoff(
    make: Callable[..., QuickbaseClient], ft: FakeTime
) -> None:
    rec = Recorder(
        respond(429),
        respond(429, headers={"retry-after": "soon"}),
        respond(429, headers={"retry-after": "-5"}),
        respond(200, json=FIELDS),
    )
    assert make(rec).get_fields("bsyn00001") == FIELDS
    assert ft.sleeps == [1.0, 2.0, 0.0]


def test_5xx_backs_off_1_2_4_8_then_exhausts(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(503, json={"message": "Service Unavailable"}))
    with pytest.raises(RunError) as info:
        make(rec, max_attempts=5).get_fields("bsyn00001")
    assert info.value.code == "QB_RETRIES_EXHAUSTED"
    assert "getFields" in info.value.message
    assert "HTTP 503" in info.value.message
    assert ft.sleeps == [1.0, 2.0, 4.0, 8.0]
    assert len(rec.requests) == 5


def test_backoff_is_capped_at_30_seconds(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(500))
    with pytest.raises(RunError) as info:
        make(rec, max_attempts=8).run_query({"from": "bsyn00002"})
    assert info.value.code == "QB_RETRIES_EXHAUSTED"
    assert ft.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_a_5xx_followed_by_success_returns_the_success(
    make: Callable[..., QuickbaseClient], ft: FakeTime
) -> None:
    rec = Recorder(respond(502), respond(200, json=QUERY_OK))
    assert make(rec).run_query({"from": "bsyn00002"}) == QUERY_OK
    assert ft.sleeps == [1.0]


@pytest.mark.parametrize(
    "exc_type", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_transport_errors_and_timeouts_are_retried(
    make: Callable[..., QuickbaseClient], ft: FakeTime, exc_type: type[httpx.TransportError]
) -> None:
    rec = Recorder(fail(exc_type), respond(200, json=FIELDS))
    assert make(rec).get_fields("bsyn00001") == FIELDS
    assert ft.sleeps == [1.0]
    assert len(rec.requests) == 2


def test_transport_errors_exhaust_with_the_error_named(
    make: Callable[..., QuickbaseClient], ft: FakeTime
) -> None:
    rec = Recorder(fail(httpx.ConnectError, "connection refused"))
    with pytest.raises(RunError) as info:
        make(rec, max_attempts=3).get_fields("bsyn00001")
    assert info.value.code == "QB_RETRIES_EXHAUSTED"
    assert "ConnectError" in info.value.message
    assert ft.sleeps == [1.0, 2.0]


def test_429_exhaustion(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(429, headers={"retry-after": "1"}))
    with pytest.raises(RunError) as info:
        make(rec, max_attempts=3).get_table("bsyn00001", "bsyn00000")
    assert info.value.code == "QB_RETRIES_EXHAUSTED"
    assert "HTTP 429" in info.value.message
    assert ft.sleeps == [1.0, 1.0]


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_other_4xx_is_never_retried(make: Callable[..., QuickbaseClient], ft: FakeTime, status: int) -> None:
    rec = Recorder(respond(status, json={"message": "Bad Request", "description": "Invalid query string"}))
    with pytest.raises(RunError) as info:
        make(rec).run_query({"from": "bsyn00002"})
    assert info.value.code == "QB_HTTP_ERROR"
    assert f"HTTP {status}" in info.value.message
    assert "runQuery" in info.value.message
    assert "Bad Request" in info.value.message and "Invalid query string" in info.value.message
    assert len(rec.requests) == 1
    assert ft.sleeps == []


def test_a_non_json_error_body_is_reported_as_text(make: Callable[..., QuickbaseClient]) -> None:
    rec = Recorder(respond(404, content=b"Not Found"))
    with pytest.raises(RunError) as info:
        make(rec).get_fields("bsyn00001")
    assert info.value.code == "QB_HTTP_ERROR"
    assert "Not Found" in info.value.message


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_are_permission_errors_naming_the_operation(
    make: Callable[..., QuickbaseClient], ft: FakeTime, status: int
) -> None:
    rec = Recorder(respond(status, json={"message": "Access denied", "description": "Not authorized"}))
    with pytest.raises(RunError) as info:
        make(rec).get_table("bsyn00001", "bsyn00000")
    assert info.value.code == "QB_PERMISSION"
    assert "getTable" in info.value.message
    assert f"HTTP {status}" in info.value.message
    assert len(rec.requests) == 1
    assert ft.sleeps == []


# -- the rate limiter ------------------------------------------------------------------------------------------


def test_the_91st_request_inside_10_seconds_waits(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(200, json=FIELDS))
    client = make(rec)
    for _ in range(90):
        client.get_fields("bsyn00001")
    assert ft.sleeps == []
    client.get_fields("bsyn00001")
    assert ft.sleeps == [10.0]
    assert len(rec.requests) == 91


def test_the_limiter_is_a_sliding_window(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(200, json=FIELDS))
    client = make(rec, requests_per_10s=3)
    client.get_fields("bsyn00001")  # t = 1000
    ft.now += 4
    client.get_fields("bsyn00001")  # t = 1004
    ft.now += 4
    client.get_fields("bsyn00001")  # t = 1008
    ft.now += 1
    client.get_fields("bsyn00001")  # t = 1009: the 1000 request leaves the window at 1010
    assert ft.sleeps == [pytest.approx(1.0)]
    ft.now += 20
    client.get_fields("bsyn00001")
    assert len(ft.sleeps) == 1


def test_retries_count_against_the_limit(make: Callable[..., QuickbaseClient], ft: FakeTime) -> None:
    rec = Recorder(respond(429, headers={"retry-after": "0"}), respond(200, json=FIELDS))
    client = make(rec, requests_per_10s=2)
    client.get_fields("bsyn00001")  # two requests at t = 1000 (the 429 and its retry)
    client.get_fields("bsyn00001")  # the third request must wait for the window
    assert ft.sleeps == [0.0, 10.0]


# -- file downloads --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('attachment; filename="report.pdf"', "report.pdf"),
        ("attachment; filename=photo.png", "photo.png"),
        ("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9%20v2.pdf", "résumé v2.pdf"),
        ("attachment; filename=\"fallback.pdf\"; filename*=utf-8''%E2%9C%93.pdf", "✓.pdf"),
        ('attachment; filename="a \\"quoted\\" name.pdf"', 'a "quoted" name.pdf'),
        ("attachment; filename*=x-unknown''abc.pdf; filename=plain.pdf", "plain.pdf"),
        ("attachment", None),
        (None, None),
    ],
)
def test_download_decodes_base64_and_reads_the_file_name(
    make: Callable[..., QuickbaseClient], header: str | None, expected: str | None
) -> None:
    payload = bytes(range(256)) * 4
    encoded = base64.b64encode(payload)
    wrapped = b"\r\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76)) + b"\n"
    headers = {"content-disposition": header} if header is not None else {}
    rec = Recorder(respond(200, content=wrapped, headers=headers))
    content, name = make(rec).download_file("bsyn00003", 201, 13, 2)
    assert content == payload
    assert name == expected
    assert str(rec.requests[0].url) == f"{BASE_URL}/files/bsyn00003/201/13/2"


def test_parse_content_disposition_filename_edge_cases() -> None:
    assert parse_content_disposition_filename(None) is None
    assert parse_content_disposition_filename("") is None
    assert parse_content_disposition_filename('inline; FILENAME="Upper.pdf"') == "Upper.pdf"
    assert parse_content_disposition_filename("attachment; filename=") is None
    assert parse_content_disposition_filename("attachment; filename*=UTF-8''%FF%FE.pdf") is None


# -- protocol errors -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "outcome"),
    [
        (lambda c: c.get_fields("bsyn00001"), respond(200, content=b"not json")),
        (lambda c: c.get_fields("bsyn00001"), respond(200, json={"not": "a list"})),
        (lambda c: c.get_fields("bsyn00001"), respond(200, json=[1, 2])),
        (lambda c: c.get_table("bsyn00001", "bsyn00000"), respond(200, json=[1, 2])),
        (lambda c: c.run_query({"from": "bsyn00002"}), respond(200, json={"data": []})),
        (lambda c: c.run_query({"from": "bsyn00002"}), respond(200, content=b"\xff\xfe")),
        (lambda c: c.download_file("bsyn00003", 201, 13, 1), respond(200, content=b"@@not-base64@@")),
        (lambda c: c.download_file("bsyn00003", 201, 13, 1), respond(200, content=b"abc")),
    ],
    ids=[
        "fields-not-json",
        "fields-object",
        "fields-not-objects",
        "table-array",
        "query-missing-members",
        "query-not-utf8",
        "file-bad-alphabet",
        "file-bad-padding",
    ],
)
def test_malformed_responses_are_protocol_errors(
    make: Callable[..., QuickbaseClient],
    ft: FakeTime,
    call: Callable[[QuickbaseClient], object],
    outcome: Outcome,
) -> None:
    rec = Recorder(outcome)
    with pytest.raises(RunError) as info:
        call(make(rec))
    assert info.value.code == "QB_PROTOCOL"
    assert len(rec.requests) == 1
    assert ft.sleeps == []


# -- the token never leaks -------------------------------------------------------------------------------------


def test_the_token_never_appears_in_errors_reprs_or_logs(
    make: Callable[..., QuickbaseClient], ft: FakeTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    echo = {"message": f"Invalid token {TOKEN}", "description": f"Authorization: QB-USER-TOKEN {TOKEN}"}
    rec = Recorder(
        respond(503, json=echo),
        fail(httpx.ConnectError, f"refused; header Authorization: QB-USER-TOKEN {TOKEN}"),
        respond(403, json=echo),
    )
    client = make(rec)
    with pytest.raises(RunError) as info:
        client.get_fields("bsyn00001")
    assert info.value.code == "QB_PERMISSION"
    for text in (str(info.value), repr(info.value), info.value.message, repr(client)):
        assert TOKEN not in text
    assert REDACTED in info.value.message

    logger = logging.getLogger(LOGGER_NAME)
    logger.warning("request headers were %s", {"Authorization": f"QB-USER-TOKEN {TOKEN}"})
    logger.info("Authorization: QB-USER-TOKEN %s", TOKEN)
    try:
        raise ValueError(f"failure with token {TOKEN}")
    except ValueError:
        logger.exception("unexpected failure while using %s", TOKEN)

    assert caplog.records, "the retries and the explicit calls must have logged"
    assert TOKEN not in caplog.text
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in (record.exc_text or "")
    assert REDACTED in caplog.text
    assert TOKEN not in repr(TokenRedactionFilter(TOKEN))


def test_redact_masks_the_token_and_any_authorization_value() -> None:
    text = (
        f"Authorization: QB-USER-TOKEN {TOKEN}\n"
        "{'Authorization': 'QB-USER-TOKEN other_secret_value'} "
        f'"authorization"="Bearer zzz_hidden" QB-TEMP-TOKEN tmp_hidden_42 bare {TOKEN}'
    )
    out = redact(text, TOKEN)
    for secret in (TOKEN, "other_secret_value", "zzz_hidden", "tmp_hidden_42"):
        assert secret not in out
    assert redact("QB-USER-TOKEN abc123def", None) == f"QB-USER-TOKEN {REDACTED}"


@pytest.mark.parametrize(
    ("realm", "token"),
    [
        ("", TOKEN),
        ("bad/host", TOKEN),
        ("host name", TOKEN),
        (REALM, ""),
        (REALM, "two words"),
        (REALM, "line\nbreak"),
    ],
)
def test_bad_configuration_is_rejected_without_echoing_the_token(realm: str, token: str) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.filters)
    with pytest.raises(RunError) as info:
        QuickbaseClient(realm, token, user_agent=UA, transport=httpx.MockTransport(respond(200)))
    assert info.value.code == "QB_CONFIG"
    if token:
        assert token not in str(info.value)
    assert logger.filters == before


@pytest.mark.parametrize(
    "kwargs",
    [{"requests_per_10s": 0}, {"requests_per_10s": True}, {"max_attempts": 0}, {"max_retry_wait_s": -1.0}],
)
def test_bad_limits_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(RunError) as info:
        QuickbaseClient(REALM, TOKEN, user_agent=UA, transport=httpx.MockTransport(respond(200)), **kwargs)
    assert info.value.code == "QB_CONFIG"


def test_close_detaches_the_filter_and_refuses_further_requests(make: Callable[..., QuickbaseClient]) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.filters)
    rec = Recorder(respond(200, json=FIELDS))
    client = make(rec)
    assert len(logger.filters) == len(before) + 1
    client.close()
    client.close()
    assert logger.filters == before
    with pytest.raises(RunError) as info:
        client.get_fields("bsyn00001")
    assert info.value.code == "QB_CLIENT_CLOSED"
    assert rec.requests == []


def test_the_client_is_a_context_manager(ft: FakeTime) -> None:
    rec = Recorder(respond(200, json=FIELDS))
    with QuickbaseClient(
        REALM, TOKEN, user_agent=UA, transport=httpx.MockTransport(rec), clock=ft.clock, sleep=ft.sleep
    ) as client:
        assert client.get_fields("bsyn00001") == FIELDS
    with pytest.raises(RunError):
        client.get_fields("bsyn00001")
