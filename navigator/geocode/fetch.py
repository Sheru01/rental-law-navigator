#!/usr/bin/env python3
"""Stage 2 of the geocoding pipeline: the ONLY module that touches the network.

A thin, reproducible wrapper. Same inputs give the same request bytes. The
raw responses are saved verbatim and never discarded or rewritten.

    python3 navigator/geocode/fetch.py batch  [--input CSV] [--out CSV]
    python3 navigator/geocode/fetch.py places [--batch-output CSV] [--out JSONL]

batch   POSTs the five column input file to
        https://geocoding.geo.census.gov/geocoder/geographies/addressbatch
        in chunks of 100 rows (form fields addressFile, benchmark, vintage),
        and saves the concatenated raw responses to
        navigator/out/census_batch_output.csv.
places  The batch endpoint returns state, county, tract and block but NOT the
        incorporated place. For every matched row this GETs
        .../geocoder/geographies/coordinates (x=lon, y=lat, layers=all,
        format=json) and saves one JSON line per lookup, with the raw body,
        to navigator/out/census_places_output.jsonl.

Both write a sidecar navigator/out/census_fetch_meta.json with the time of
the live call, so the parsed records can carry request_timestamp.

ALL OR NOTHING. If the host is blocked or unreachable, if a chunk is still
truncated after the retries, or if any lookup fails after its retries, the
command prints a clear message naming the host, exits non-zero, and writes
nothing. Files are assembled in memory and moved into place atomically only
after every request succeeded. An existing output file is never replaced
unless --overwrite is given.

Truncation check, per chunk: every request id must come back exactly once and
every line must be well formed (see parse.parse_batch_lines). A chunk that
fails the check is retried with exponential backoff (backoff * 2**attempt).

Exit codes: 0 ok, 1 other error, 2 host blocked or unreachable, 3 still
incomplete after retries, 4 output already exists.

Plain ASCII output. Standard library only.
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import normalize  # noqa: E402
import parse  # noqa: E402

ROOT = _HERE.parents[1]
DEFAULT_OUT_DIR = ROOT / "navigator" / "out"

BASE_URL = "https://geocoding.geo.census.gov"
BATCH_PATH = "/geocoder/geographies/addressbatch"
COORD_PATH = "/geocoder/geographies/coordinates"
BENCHMARK = parse.BENCHMARK
VINTAGE = parse.VINTAGE
CHUNK_SIZE = 100

BATCH_OUT = "census_batch_output.csv"
PLACES_OUT = "census_places_output.jsonl"
META_OUT = "census_fetch_meta.json"


class FetchError(Exception):
    exit_code = 1


class HostUnreachable(FetchError):
    exit_code = 2


class HostBlocked(HostUnreachable):
    """The host answered with a policy block (HTTP 403 or a proxy 403 on
    CONNECT). Not transient, so it is never retried."""


class IncompleteResponse(FetchError):
    exit_code = 3


class OutputExists(FetchError):
    exit_code = 4


class Retryable(Exception):
    """Internal: a failed attempt that is worth another try."""

    def __init__(self, final_error):
        Exception.__init__(self, str(final_error))
        self.final_error = final_error


def host_of(base_url):
    return urllib.parse.urlsplit(base_url).hostname or base_url


# --------------------------------------------------------------------------
# request construction (pure, byte for byte reproducible)
# --------------------------------------------------------------------------

def build_multipart(file_bytes, benchmark, vintage):
    """Return (body, content_type) for the addressbatch POST.

    The boundary is derived from the payload so identical inputs give
    identical bytes. Field order: addressFile, benchmark, vintage.
    """
    boundary = "----CensusBatch" + hashlib.sha256(file_bytes).hexdigest()[:16]
    crlf = b"\r\n"
    sep = ("--" + boundary).encode("ascii")
    parts = [
        sep + crlf
        + b'Content-Disposition: form-data; name="addressFile"; filename="addresses.csv"' + crlf
        + b"Content-Type: text/csv" + crlf + crlf + file_bytes + crlf,
        sep + crlf + b'Content-Disposition: form-data; name="benchmark"' + crlf + crlf
        + benchmark.encode("ascii") + crlf,
        sep + crlf + b'Content-Disposition: form-data; name="vintage"' + crlf + crlf
        + vintage.encode("ascii") + crlf,
        sep + b"--" + crlf,
    ]
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def build_batch_request(base_url, file_bytes, benchmark=BENCHMARK, vintage=VINTAGE):
    """Return (url, headers, body) for one addressbatch chunk."""
    body, ctype = build_multipart(file_bytes, benchmark, vintage)
    return base_url.rstrip("/") + BATCH_PATH, {"Content-Type": ctype}, body


def build_coordinates_url(base_url, coordinates, benchmark=BENCHMARK, vintage=VINTAGE):
    """URL for one place lookup. coordinates is the batch "lon,lat" string."""
    x, y = [c.strip() for c in coordinates.split(",")]
    query = urllib.parse.urlencode([
        ("x", x), ("y", y), ("benchmark", benchmark), ("vintage", vintage),
        ("layers", "all"), ("format", "json"),
    ])
    return base_url.rstrip("/") + COORD_PATH + "?" + query


def chunk_rows(rows, size=CHUNK_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def rows_to_bytes(rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def read_input_rows(path):
    raw = Path(path).read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    rows = [r for r in rows if r]
    bad = [i + 1 for i, r in enumerate(rows) if len(r) != 5]
    if bad:
        raise FetchError("input %s has rows without exactly 5 columns (first at line %d)"
                         % (Path(path).name, bad[0]))
    ids = [r[0] for r in rows]
    if len(set(ids)) != len(ids):
        raise FetchError("input %s has duplicate address ids" % Path(path).name)
    if rows and rows[0][0] == "address_id":
        raise FetchError("input %s starts with a header row; the Census file has none"
                         % Path(path).name)
    return raw, rows


# --------------------------------------------------------------------------
# the one place the network is touched
# --------------------------------------------------------------------------

def http_call(url, data=None, headers=None, timeout=60, opener=None):
    """Return (status, body_bytes). Raises HostUnreachable on transport failure."""
    opener = opener or urllib.request.build_opener()
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b""
        return exc.code, body
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        cls = HostBlocked if _is_policy_block(reason) else HostUnreachable
        raise cls(
            "cannot reach %s (%s: %s). The host may be blocked by an egress allowlist "
            "or proxy. Nothing was written."
            % (host_of(url), reason.__class__.__name__, str(reason)[:160]))


def _is_policy_block(reason):
    text = str(reason)
    return "403" in text and ("Tunnel" in text or "Forbidden" in text)


def _attempt_loop(call, retries, backoff, sleep):
    """Run call() until it returns, retrying only Retryable failures."""
    last = None
    for attempt in range(retries + 1):
        try:
            return call(), attempt + 1
        except Retryable as exc:
            last = exc
            if attempt < retries:
                sleep(backoff * (2 ** attempt))
        except HostBlocked:
            raise  # a policy block is not transient, fail fast
        except HostUnreachable as exc:
            last = Retryable(exc)
            if attempt < retries:
                sleep(backoff * (2 ** attempt))
    raise last.final_error


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_write_all(files):
    """files is a list of (path, bytes). All are moved into place together
    or none are. Temp files are always cleaned up."""
    tmps = []
    try:
        for path, data in files:
            tmp = Path(str(path) + ".tmp")
            tmp.write_bytes(data)
            tmps.append((tmp, Path(path)))
        for tmp, final in tmps:
            os.replace(str(tmp), str(final))
        tmps = []
    finally:
        for tmp, _ in tmps:
            try:
                tmp.unlink()
            except OSError:
                pass


def _load_meta(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _dump_meta(meta):
    return (json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def _check_exists(path, overwrite):
    if Path(path).exists() and not overwrite:
        raise OutputExists("%s already exists and is never replaced silently. "
                           "Move it aside or pass --overwrite." % Path(path).name)


# --------------------------------------------------------------------------
# stage 2a: the address batch
# --------------------------------------------------------------------------

def fetch_batch(input_path, out_path, base_url=BASE_URL, benchmark=BENCHMARK,
                vintage=VINTAGE, chunk_size=CHUNK_SIZE, retries=3, backoff=2.0,
                timeout=60, sleep=time.sleep, now=_utc_now, overwrite=False, opener=None):
    out_path = Path(out_path)
    _check_exists(out_path, overwrite)
    raw_input, rows = read_input_rows(input_path)
    started = now()
    pieces, attempts = [], []
    chunks = list(chunk_rows(rows, chunk_size))
    for n, chunk in enumerate(chunks, start=1):
        payload = rows_to_bytes(chunk)
        expected = [r[0] for r in chunk]
        url, headers, body = build_batch_request(base_url, payload, benchmark, vintage)

        def call():
            status, resp = http_call(url, body, headers, timeout, opener)
            if status == 403:
                raise HostBlocked(
                    "HTTP 403 from %s: the host is blocked for this machine "
                    "(egress allowlist or policy). Nothing was written." % host_of(url))
            if status >= 500 or status == 429:
                raise Retryable(IncompleteResponse(
                    "chunk %d/%d: HTTP %d from %s" % (n, len(chunks), status, host_of(url))))
            if status != 200:
                raise FetchError("chunk %d/%d: HTTP %d from %s" % (n, len(chunks), status, host_of(url)))
            problems = completeness_problems(resp, expected)
            if problems:
                raise Retryable(IncompleteResponse(
                    "chunk %d/%d still incomplete after %d retries (%s). Nothing was written."
                    % (n, len(chunks), retries, "; ".join(problems))))
            return resp

        resp, used = _attempt_loop(call, retries, backoff, sleep)
        pieces.append(resp if resp.endswith(b"\n") else resp + b"\n")
        attempts.append(used)

    output = b"".join(pieces)
    meta = _load_meta(out_path.parent / META_OUT)
    meta["batch"] = {
        "endpoint": base_url.rstrip("/") + BATCH_PATH,
        "benchmark": benchmark, "vintage": vintage,
        "retrieved_at": started,
        "input_file": Path(input_path).name, "input_sha256": _sha256(raw_input),
        "rows": len(rows), "chunk_size": chunk_size, "chunks": len(chunks),
        "attempts_per_chunk": attempts, "output_sha256": _sha256(output),
    }
    _atomic_write_all([(out_path, output), (out_path.parent / META_OUT, _dump_meta(meta))])
    return meta["batch"]


def completeness_problems(resp_bytes, expected_ids):
    """Why a chunk response cannot be accepted. Empty list means accept."""
    problems = []
    if not resp_bytes.strip():
        return ["empty response"]
    entries, malformed = parse.parse_batch_lines(resp_bytes)
    if malformed:
        problems.append("%d malformed line(s), first at line %d (%s)"
                        % (len(malformed), malformed[0]["line_no"], malformed[0]["reason"]))
    seen = {}
    for e in entries:
        seen[e["id"]] = seen.get(e["id"], 0) + 1
    missing = [i for i in expected_ids if i not in seen]
    if missing:
        problems.append("%d of %d ids missing (first %s)" % (len(missing), len(expected_ids), missing[0]))
    extra = [i for i in seen if i not in set(expected_ids)]
    if extra:
        problems.append("%d unexpected id(s)" % len(extra))
    dups = [i for i, c in seen.items() if c > 1]
    if dups:
        problems.append("%d duplicated id(s)" % len(dups))
    return problems


# --------------------------------------------------------------------------
# stage 2b: the place lookups
# --------------------------------------------------------------------------

def fetch_places(batch_output_path, out_path, base_url=BASE_URL, benchmark=BENCHMARK,
                 vintage=VINTAGE, retries=3, backoff=2.0, timeout=60, delay=0.2,
                 sleep=time.sleep, now=_utc_now, overwrite=False, opener=None):
    out_path = Path(out_path)
    _check_exists(out_path, overwrite)
    batch_path = Path(batch_output_path)
    if not batch_path.exists():
        raise FetchError("batch output %s not found; run the batch step first" % batch_path.name)
    entries, _ = parse.parse_batch_lines(batch_path.read_bytes())
    matched = [e for e in entries if e["kind"] == "match"]
    started = now()
    lines = []
    for k, e in enumerate(matched, start=1):
        url = build_coordinates_url(base_url, e["coordinates"], benchmark, vintage)

        def call():
            status, resp = http_call(url, None, None, timeout, opener)
            if status == 403:
                raise HostBlocked(
                    "HTTP 403 from %s: the host is blocked for this machine "
                    "(egress allowlist or policy). Nothing was written." % host_of(url))
            if status >= 500 or status == 429:
                raise Retryable(IncompleteResponse(
                    "lookup %s: HTTP %d from %s. Nothing was written." % (e["id"], status, host_of(url))))
            if status != 200:
                raise FetchError("lookup %s: HTTP %d from %s" % (e["id"], status, host_of(url)))
            text = resp.decode("utf-8", "replace")
            try:
                geos = json.loads(text)["result"]["geographies"]
                if not isinstance(geos, dict):
                    raise ValueError("geographies")
            except (ValueError, KeyError, TypeError):
                raise Retryable(IncompleteResponse(
                    "lookup %s: response has no result.geographies after %d retries. "
                    "Nothing was written." % (e["id"], retries)))
            return text

        text, _used = _attempt_loop(call, retries, backoff, sleep)
        x, y = [c.strip() for c in e["coordinates"].split(",")]
        lines.append(json.dumps({"address_id": e["id"], "x": x, "y": y, "url": url,
                                 "http_status": 200, "body": text}, ensure_ascii=True))
        if delay and k < len(matched):
            sleep(delay)

    output = ("\n".join(lines) + ("\n" if lines else "")).encode("ascii")
    meta = _load_meta(out_path.parent / META_OUT)
    meta["places"] = {
        "endpoint": base_url.rstrip("/") + COORD_PATH,
        "benchmark": benchmark, "vintage": vintage, "layers": "all",
        "retrieved_at": started, "lookups": len(lines),
        "batch_output_sha256": _sha256(batch_path.read_bytes()),
        "output_sha256": _sha256(output),
    }
    _atomic_write_all([(out_path, output), (out_path.parent / META_OUT, _dump_meta(meta))])
    return meta["places"]


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=BASE_URL)
    common.add_argument("--benchmark", default=BENCHMARK)
    common.add_argument("--vintage", default=VINTAGE)
    common.add_argument("--retries", type=int, default=3)
    common.add_argument("--backoff", type=float, default=2.0, help="base seconds, doubled per retry")
    common.add_argument("--timeout", type=float, default=60.0)
    common.add_argument("--overwrite", action="store_true")
    ap = argparse.ArgumentParser(description="The only module that touches the network.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", parents=[common])
    b.add_argument("--input", default=None, help="five column CSV; default is the pipeline input file")
    b.add_argument("--out", default=str(DEFAULT_OUT_DIR / BATCH_OUT))
    b.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    p = sub.add_parser("places", parents=[common])
    p.add_argument("--batch-output", default=str(DEFAULT_OUT_DIR / BATCH_OUT))
    p.add_argument("--out", default=str(DEFAULT_OUT_DIR / PLACES_OUT))
    p.add_argument("--delay", type=float, default=0.2, help="seconds between lookups")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "batch":
            inp = args.input or str(normalize.send_path(DEFAULT_OUT_DIR))
            info = fetch_batch(inp, args.out, args.base_url, args.benchmark, args.vintage,
                               args.chunk_size, args.retries, args.backoff, args.timeout,
                               overwrite=args.overwrite)
            print("batch ok: %d rows, %d chunks, attempts per chunk %s"
                  % (info["rows"], info["chunks"], info["attempts_per_chunk"]))
            print("saved %s" % Path(args.out).name)
        else:
            info = fetch_places(args.batch_output, args.out, args.base_url, args.benchmark,
                                args.vintage, args.retries, args.backoff, args.timeout,
                                args.delay, overwrite=args.overwrite)
            print("places ok: %d lookups" % info["lookups"])
            print("saved %s" % Path(args.out).name)
    except FetchError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
