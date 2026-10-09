"""Read-only Quickbase JSON API client (SPEC §12.1, §12.4).

Exactly four read operations exist. Anything else, including any ``DELETE`` and any file version below 1,
raises :class:`AllowlistError` before a request is built. The user token is held privately and never
appears in an exception message, a ``repr`` or a log record emitted through ``inspection_reconcile.qb``.
Time is injected (``clock`` and ``sleep``) so the limiter and the retry policy are testable without waiting.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote_to_bytes

import httpx

from inspection_reconcile.errors import RunError

BASE_URL = "https://api.quickbase.com/v1"
LOGGER_NAME = "inspection_reconcile.qb"
WINDOW_SECONDS = 10.0
BACKOFF_CAP_SECONDS = 30.0
REDACTED = "***"

log = logging.getLogger(LOGGER_NAME)

_DBID = r"[A-Za-z0-9]{1,64}"
_POSITIVE_INT = r"[1-9][0-9]{0,18}"
_DBID_RE = re.compile(_DBID)
_HOSTNAME_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,512}")
_USER_AGENT_RE = re.compile(r"[\x20-\x7e]{1,200}")

# (method, path pattern, required query parameter names, operation id). Nothing else can be issued.
_ALLOWLIST: tuple[tuple[str, re.Pattern[str], frozenset[str], str], ...] = (
    ("GET", re.compile(r"/fields"), frozenset({"tableId"}), "getFields"),
    ("GET", re.compile(rf"/tables/{_DBID}"), frozenset({"appId"}), "getTable"),
    ("POST", re.compile(r"/records/query"), frozenset(), "runQuery"),
    (
        "GET",
        re.compile(rf"/files/{_DBID}/{_POSITIVE_INT}/{_POSITIVE_INT}/{_POSITIVE_INT}"),
        frozenset(),
        "downloadFile",
    ),
)

ERROR_BODY_LIMIT = 64 * 1024
_BASE64_WHITESPACE = b" \t\n\r\x0b\x0c"  # what bytes.split() treats as whitespace

_AUTH_VALUE_RE = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^\r\n'\",}]*")
_SCHEME_TOKEN_RE = re.compile(r"(?i)\b(QB-(?:USER|TEMP)-TOKEN)\s+[^\s'\",}]+")


class AllowlistError(Exception):
    """A request outside the read-only allowlist: a programming error, raised before any I/O."""


class QuickbaseHTTPError(RunError):
    """``QB_HTTP_ERROR``: a 4xx other than 401, 403 and 429, never retried. ``status`` lets a caller act on the
    status itself instead of parsing a message that also carries server-supplied text."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__("QB_HTTP_ERROR", message)
        self.status = status


class FileTooLarge(RunError):
    """``QB_FILE_TOO_LARGE``: a file over ``max_bytes`` (SPEC §12.5 step 3). ``size`` is the decoded size when the
    whole body was read, and ``None`` when the download stopped at the limit, because the full size is unknown."""

    def __init__(self, message: str, size: int | None) -> None:
        super().__init__("QB_FILE_TOO_LARGE", message)
        self.size = size


def base64_cap(max_bytes: int) -> int:
    """The longest base64 payload (whitespace excluded) of a file of at most ``max_bytes`` bytes."""
    return 4 * ((max_bytes + 2) // 3)


def _read_all(response: httpx.Response) -> bytes:
    return response.read()


def _read_prefix(response: httpx.Response, limit: int) -> bytes:
    """At most ``limit`` bytes of a streamed body. An error body never needs more, and a failure while reading it
    must not turn a non-retryable status into a retried transport error."""
    data = bytearray()
    try:
        for chunk in response.iter_bytes():
            data += chunk[: limit - len(data)]
            if len(data) >= limit:
                break
    except httpx.TransportError:
        pass
    return bytes(data)


def redact(text: str, token: str | None = None) -> str:
    """Remove a user token, any ``QB-USER-TOKEN <value>`` pair and any Authorization value from ``text``."""
    if token:
        text = text.replace(token, REDACTED)
    text = _AUTH_VALUE_RE.sub(lambda m: m.group(1) + REDACTED, text)
    return _SCHEME_TOKEN_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


class TokenRedactionFilter(logging.Filter):
    """Rewrites every record on the ``inspection_reconcile.qb`` logger with the token and credentials masked."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed format string must not leak the raw arguments either
            message = f"{record.msg!s} {record.args!r}"
        record.msg = redact(message, self._token)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self._token)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, self._token)
        return True

    def __repr__(self) -> str:
        return "TokenRedactionFilter()"


def check_allowlist(method: str, path: str, params: Mapping[str, object] | None = None) -> str:
    """Return the operation id for an allowed request, or raise :class:`AllowlistError`."""
    query = dict(params or {})
    for allowed_method, pattern, required, operation in _ALLOWLIST:
        if method != allowed_method or pattern.fullmatch(path) is None:
            continue
        if set(query) != required:
            raise AllowlistError(f"{operation} takes exactly the query parameters {sorted(required)}")
        for name, value in query.items():
            if not isinstance(value, str) or _DBID_RE.fullmatch(value) is None:
                raise AllowlistError(f"{operation}: query parameter {name} is not a Quickbase id")
        return operation
    raise AllowlistError(f"{method} {path} is not one of the allowed read operations")


def parse_content_disposition_filename(header: str | None) -> str | None:
    """The file name from a Content-Disposition header (RFC 6266): ``filename*`` wins over ``filename``."""
    if not header:
        return None
    params = _parse_header_params(header)
    extended = params.get("filename*")
    if extended is not None:
        decoded = _decode_rfc5987(extended)
        if decoded:
            return decoded
    plain = params.get("filename")
    return plain if plain else None


def _parse_header_params(header: str) -> dict[str, str]:
    """Split ``type; a=b; c="d"`` into lower-cased parameter names and unquoted values."""
    params: dict[str, str] = {}
    i, n = 0, len(header)
    while i < n and header[i] != ";":
        i += 1
    while i < n:
        i += 1  # skip ';'
        while i < n and header[i] in " \t":
            i += 1
        start = i
        while i < n and header[i] not in "=;":
            i += 1
        name = header[start:i].strip().lower()
        if i >= n or header[i] == ";":
            continue
        i += 1  # skip '='
        while i < n and header[i] in " \t":
            i += 1
        if i < n and header[i] == '"':
            i += 1
            chars: list[str] = []
            while i < n and header[i] != '"':
                if header[i] == "\\" and i + 1 < n:
                    i += 1
                chars.append(header[i])
                i += 1
            i += 1  # closing quote
            value = "".join(chars)
            while i < n and header[i] != ";":
                i += 1
        else:
            start = i
            while i < n and header[i] != ";":
                i += 1
            value = header[start:i].strip()
        if name and name not in params:
            params[name] = value
    return params


def _decode_rfc5987(value: str) -> str | None:
    """Decode ``charset'language'percent-encoded`` (RFC 5987); unknown charsets give ``None``."""
    parts = value.split("'", 2)
    if len(parts) != 3:
        return None
    charset = parts[0].strip().lower()
    if charset not in ("utf-8", "iso-8859-1"):
        return None
    try:
        return unquote_to_bytes(parts[2]).decode(charset)
    except UnicodeDecodeError:
        return None


class QuickbaseClient:
    """The only way this project talks to Quickbase: four read operations, sequential, rate-limited."""

    def __init__(
        self,
        realm_hostname: str,
        token: str,
        *,
        user_agent: str,
        transport: httpx.BaseTransport | None = None,
        requests_per_10s: int = 90,
        max_attempts: int = 5,
        max_retry_wait_s: float = 60.0,
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(realm_hostname, str) or _HOSTNAME_RE.fullmatch(realm_hostname) is None:
            raise RunError(
                "QB_CONFIG", "realm_hostname must be a plain host name such as example.quickbase.com"
            )
        if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
            raise RunError("QB_CONFIG", "the Quickbase user token is empty or malformed (value not shown)")
        if not isinstance(user_agent, str) or _USER_AGENT_RE.fullmatch(user_agent) is None:
            raise RunError("QB_CONFIG", "user_agent must be 1-200 printable ASCII characters")
        if (
            isinstance(requests_per_10s, bool)
            or not isinstance(requests_per_10s, int)
            or requests_per_10s < 1
        ):
            raise RunError("QB_CONFIG", "requests_per_10s must be a positive integer")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise RunError("QB_CONFIG", "max_attempts must be a positive integer")
        if not (max_retry_wait_s >= 0 and math.isfinite(max_retry_wait_s)):
            raise RunError("QB_CONFIG", "max_retry_wait_s must be a finite number >= 0")
        self._realm = realm_hostname
        self._token = token
        self._user_agent = user_agent
        self._limit = requests_per_10s
        self._max_attempts = max_attempts
        self._max_retry_wait_s = float(max_retry_wait_s)
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._filter = TokenRedactionFilter(token)
        log.addFilter(self._filter)
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            follow_redirects=False,
            headers={
                "QB-Realm-Hostname": realm_hostname,
                "Authorization": f"QB-USER-TOKEN {token}",
                "User-Agent": user_agent,
                "Content-Type": "application/json",
            },
        )
        self._closed = False

    def __repr__(self) -> str:
        return f"QuickbaseClient(realm_hostname={self._realm!r}, user_agent={self._user_agent!r})"

    def __enter__(self) -> QuickbaseClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- the four allowed operations -------------------------------------------------------------------------

    def get_fields(self, table_id: str) -> list[dict[str, Any]]:
        """``GET /v1/fields?tableId=…``: every field of the table, with ``fieldType`` and ``mode``."""
        data = self._json(self._request("GET", "/fields", params={"tableId": table_id}), "getFields")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise RunError("QB_PROTOCOL", "getFields did not return a JSON array of field objects")
        return data

    def get_table(self, table_id: str, app_id: str) -> dict[str, Any]:
        """``GET /v1/tables/{tableId}?appId=…``."""
        data = self._json(self._request("GET", f"/tables/{table_id}", params={"appId": app_id}), "getTable")
        if not isinstance(data, dict):
            raise RunError("QB_PROTOCOL", "getTable did not return a JSON object")
        return data

    def run_query(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /v1/records/query`` (a read by its documented semantics, not by its verb)."""
        if not isinstance(body, dict):
            raise TypeError("run_query takes the query body as a dict")
        data = self._json(self._request("POST", "/records/query", json_body=body), "runQuery")
        if not isinstance(data, dict):
            raise RunError("QB_PROTOCOL", "runQuery did not return a JSON object")
        if (
            not isinstance(data.get("data"), list)
            or not isinstance(data.get("fields"), list)
            or not isinstance(data.get("metadata"), dict)
        ):
            raise RunError("QB_PROTOCOL", "runQuery response lacks the data, fields or metadata members")
        return data

    def download_file(
        self, table_id: str, record_id: int, field_id: int, version: int, *, max_bytes: int | None = None
    ) -> tuple[bytes, str | None]:
        """``GET /v1/files/{t}/{r}/{f}/{v}`` with ``v >= 1``: the base64-decoded bytes and the file name, if any.

        The body is streamed. With ``max_bytes``, it is never held beyond the base64 size of ``max_bytes``: once more
        than ``base64_cap(max_bytes)`` payload characters (whitespace excluded) have arrived, the download stops and
        :class:`FileTooLarge` is raised with an unknown size. A complete body that decodes to more than
        ``max_bytes`` raises it with the size, so the two checks agree at the boundary."""
        for name, value in (("record_id", record_id), ("field_id", field_id), ("version", version)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise AllowlistError(f"downloadFile: {name} must be an integer >= 1")
        if max_bytes is not None and (
            isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1
        ):
            raise ValueError("max_bytes must be a positive integer or None")
        cap = None if max_bytes is None else base64_cap(max_bytes)
        what = f"downloadFile {table_id}/{record_id}/{field_id}/v{version}"

        def read_base64(response: httpx.Response) -> tuple[bytes, str | None]:
            parts: list[bytes] = []
            count = 0
            for chunk in response.iter_bytes():
                payload = chunk.translate(None, _BASE64_WHITESPACE)
                count += len(payload)
                if cap is not None and count > cap:
                    raise FileTooLarge(f"{what}: more than {max_bytes} bytes; the download was stopped", None)
                parts.append(payload)
            filename = parse_content_disposition_filename(response.headers.get("content-disposition"))
            return b"".join(parts), filename

        compact, filename = self._request(
            "GET", f"/files/{table_id}/{record_id}/{field_id}/{version}", read_body=read_base64
        )
        try:
            content = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            raise RunError("QB_PROTOCOL", "downloadFile returned a body that is not valid base64") from None
        if max_bytes is not None and len(content) > max_bytes:
            raise FileTooLarge(
                f"{what}: {len(content)} bytes is more than the limit of {max_bytes}", len(content)
            )
        return content, filename

    def close(self) -> None:
        """Close the connection pool and detach the log filter. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self._http.close()
        log.removeFilter(self._filter)

    # -- transport, retries and the limiter --------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        read_body: Callable[[httpx.Response], Any] = _read_all,
    ) -> Any:
        """One allowed operation with the limiter and the retry policy; returns ``read_body(response)``.

        The response is streamed: the status line decides a retry before any body is read, a 2xx body is consumed by
        ``read_body`` (the whole body by default), and a transport error while the body arrives is retried like any
        other. The stream is closed before any retry wait."""
        operation = check_allowlist(method, path, params)
        if self._closed:
            raise RunError("QB_CLIENT_CLOSED", f"{operation} was called after close()")
        url = BASE_URL + path
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            retry: tuple[str, float] | None = None
            try:
                with self._http.stream(method, url, params=dict(params or {}), json=json_body) as response:
                    status = response.status_code
                    if 200 <= status < 300:
                        log.debug("%s %s -> %d (attempt %d)", method, path, status, attempt)
                        return read_body(response)
                    if status == 429:
                        retry = ("HTTP 429", self._retry_after(response, attempt))
                    elif 500 <= status < 600:
                        retry = (f"HTTP {status}", self._backoff(attempt))
                    else:
                        detail = self._error_detail(_read_prefix(response, ERROR_BODY_LIMIT))
                        if status in (401, 403):
                            raise RunError(
                                "QB_PERMISSION", f"{operation} was refused with HTTP {status}: {detail}"
                            )
                        raise QuickbaseHTTPError(status, f"{operation} failed with HTTP {status}: {detail}")
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {self._redact(str(exc))}"
                self._retry_or_raise(operation, attempt, last, self._backoff(attempt))
                continue
            assert retry is not None  # every other path above returned or raised
            self._retry_or_raise(operation, attempt, *retry)

    def _retry_or_raise(self, operation: str, attempt: int, last: str, delay: float) -> None:
        if attempt >= self._max_attempts:
            raise RunError(
                "QB_RETRIES_EXHAUSTED",
                f"{operation} failed after {attempt} attempt(s); last error: {last}",
            )
        log.warning(
            "%s: %s; retrying in %.3f s (attempt %d of %d)",
            operation,
            last,
            delay,
            attempt,
            self._max_attempts,
        )
        self._sleep(delay)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** (attempt - 1), BACKOFF_CAP_SECONDS)

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after")
        try:
            wait = float(raw) if raw is not None else math.nan
        except ValueError:
            wait = math.nan
        if not math.isfinite(wait):
            wait = self._backoff(attempt)
        return min(max(wait, 0.0), self._max_retry_wait_s)

    def _throttle(self) -> None:
        now = self._clock()
        window = self._sent
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self._limit:
            wait = WINDOW_SECONDS - (now - window[0])
            if wait > 0:
                log.debug(
                    "rate limit: %d requests in the last %.0f s; waiting %.3f s",
                    len(window),
                    WINDOW_SECONDS,
                    wait,
                )
                self._sleep(wait)
            now = self._clock()
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
        window.append(now)

    # -- response decoding -----------------------------------------------------------------------------------

    def _json(self, content: bytes, operation: str) -> Any:
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RunError(
                "QB_PROTOCOL", f"{operation} returned a body that is not valid UTF-8 JSON"
            ) from None

    def _error_detail(self, content: bytes) -> str:
        text = ""
        try:
            body = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        if isinstance(body, dict):
            parts = [str(body[k]) for k in ("message", "description") if body.get(k) not in (None, "")]
            text = " - ".join(parts)
        if not text:
            text = content[:200].decode("utf-8", errors="replace").strip() or "(no body)"
        return self._redact(text)

    def _redact(self, text: str) -> str:
        return redact(text, self._token)
