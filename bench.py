#!/usr/bin/env python3
"""Guardrail latency benchmark: three Azure RAI postures + optional Prisma AIRS direct API.

All scanners run IN PARALLEL per prompt for a true head-to-head. Captures full
client-side round-trip, server-side processing time (openai-processing-ms header),
RAI verdicts, and request IDs into a long-format CSV, then emits a console
summary + JSON with percentiles, pairwise deltas, and fastest-endpoint win rate.

Azure postures (always active):
  default   embedding-default-endpoint  (Microsoft.Default — system-managed)
  strict    embedding-strict-endpoint   (custom low-severity content filters)
  prisma    embedding-prisma-endpoint   (Prisma-policy RAI — medium thresholds)

Prisma AIRS direct API (optional — added when env vars are present):
  airs      POST /v1/scan/sync/request  (inline synchronous scan, no embedding)

Required env / .env:
  AZURE_AI_ENDPOINT        https://<subdomain>.cognitiveservices.azure.com/
  AZURE_AI_API_KEY         <primary key from: terraform output -raw ai_services_primary_key>
  DEPLOYMENT_DEFAULT       embedding-default-endpoint
  DEPLOYMENT_STRICT        embedding-strict-endpoint
  DEPLOYMENT_PRISMA        embedding-prisma-endpoint

Optional env (enables the airs leg):
  PRISMA_AIRS_API_KEY      <x-pan-token>
  PRISMA_AIRS_PROFILE_NAME <security profile name>
  PRISMA_AIRS_ENDPOINT     https://service.api.aisecurity.paloaltonetworks.com  (default)
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import logging
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, AzureOpenAI

log = logging.getLogger("bench")

REQUIRED_ENV = (
    "AZURE_AI_ENDPOINT",
    "AZURE_AI_API_KEY",
    "DEPLOYMENT_DEFAULT",
    "DEPLOYMENT_STRICT",
    "DEPLOYMENT_PRISMA",
)
AIRS_DEFAULT_ENDPOINT = "https://service.api.aisecurity.paloaltonetworks.com"
API_VERSION = "2024-02-01"
VALID_SCAN_STATUSES = ("SUCCESS", "BLOCKED")


@dataclass
class ScanResult:
    timestamp: str
    prompt_index: int
    repeat: int
    endpoint: str       # default | strict | prisma
    latency_ms: float   # full client-side round trip
    server_ms: float | None  # openai-processing-ms header
    status: str         # SUCCESS | BLOCKED | HTTP_<code> | TIMEOUT | CONN_ERROR
    http_status: int | None
    request_id: str     # apim-request-id / x-request-id
    region: str         # x-ms-region
    vector_dims: int | None   # 1536 on success; None on error
    prompt_chars: int
    prompt: str
    error: str


class EmbeddingScanner:
    """Sends a text embedding request to one Azure AI deployment.

    A content_filter 400 means the RAI policy fired (BLOCKED).
    Any other non-2xx is recorded as HTTP_<code>.
    """

    def __init__(
        self,
        name: str,
        azure_endpoint: str,
        api_key: str,
        deployment: str,
        timeout: float,
    ) -> None:
        self.name = name
        self._deployment = deployment
        self._client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=API_VERSION,
            max_retries=0,
            timeout=httpx.Timeout(timeout),
        )

    def warmup(self) -> None:
        with contextlib.suppress(Exception):
            self._client.embeddings.create(model=self._deployment, input="warmup")

    def scan(self, prompt: str, idx: int, rep: int) -> ScanResult:
        http_status: int | None = None
        server_ms: float | None = None
        vector_dims: int | None = None
        status, error = "SUCCESS", ""
        headers = httpx.Headers()
        start = time.perf_counter()
        try:
            raw = self._client.embeddings.with_raw_response.create(
                model=self._deployment, input=prompt
            )
            latency = (time.perf_counter() - start) * 1000
            http_status = raw.status_code
            headers = raw.headers
            body = raw.parse()
            if body.data:
                vector_dims = len(body.data[0].embedding)
        except APIStatusError as e:
            latency = (time.perf_counter() - start) * 1000
            http_status = e.status_code
            headers = e.response.headers
            body_text = e.response.text or ""
            status = (
                "BLOCKED" if "content_filter" in body_text else f"HTTP_{e.status_code}"
            )
            error = " ".join(body_text.split())[:500]
        except APITimeoutError:
            latency = (time.perf_counter() - start) * 1000
            status, error = "TIMEOUT", "no response within client timeout"
        except APIConnectionError as e:
            latency = (time.perf_counter() - start) * 1000
            status, error = "CONN_ERROR", str(e).replace("\n", " ")

        proc = headers.get("openai-processing-ms")
        if proc:
            with contextlib.suppress(ValueError):
                server_ms = float(proc)

        return ScanResult(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            prompt_index=idx,
            repeat=rep,
            endpoint=self.name,
            latency_ms=round(latency, 2),
            server_ms=server_ms,
            status=status,
            http_status=http_status,
            request_id=headers.get("apim-request-id")
            or headers.get("x-request-id", ""),
            region=headers.get("x-ms-region", ""),
            vector_dims=vector_dims,
            prompt_chars=len(prompt),
            prompt=" ".join(prompt.split()),
            error=error,
        )


class AirsScanner:
    """Hits the Prisma AIRS synchronous scan API directly (no Azure embedding).

    HTTP 200 with action=block means the guardrail fired (BLOCKED).
    vector_dims is always None — AIRS returns a verdict, not a vector.
    """

    def __init__(
        self, name: str, base_url: str, api_key: str, profile: str, timeout: float
    ) -> None:
        self.name = name
        self._profile = profile
        self._url = base_url.rstrip("/") + "/v1/scan/sync/request"
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "x-pan-token": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def warmup(self) -> None:
        with contextlib.suppress(Exception):
            self._http.post(self._url, json=self._payload("warmup", 0, 0))

    def _payload(self, prompt: str, idx: int, rep: int) -> dict[str, Any]:
        return {
            "tr_id": f"bench-{idx}-{rep}",
            "ai_profile": {"profile_name": self._profile},
            "contents": [{"prompt": prompt}],
        }

    def scan(self, prompt: str, idx: int, rep: int) -> ScanResult:
        http_status: int | None = None
        status, error, request_id = "SUCCESS", "", ""
        start = time.perf_counter()
        try:
            resp = self._http.post(self._url, json=self._payload(prompt, idx, rep))
            latency = (time.perf_counter() - start) * 1000
            http_status = resp.status_code
            if resp.status_code == 200:
                body = resp.json()
                request_id = str(body.get("scan_id", ""))
                if body.get("action") == "block":
                    status = "BLOCKED"
                    detected = sorted(
                        k for k, v in body.get("prompt_detected", {}).items() if v
                    )
                    error = f"category={body.get('category')} detected={detected}"
            else:
                status = f"HTTP_{resp.status_code}"
                error = " ".join(resp.text.split())[:500]
        except httpx.TimeoutException:
            latency = (time.perf_counter() - start) * 1000
            status, error = "TIMEOUT", "no response within client timeout"
        except httpx.HTTPError as e:
            latency = (time.perf_counter() - start) * 1000
            status, error = "CONN_ERROR", str(e).replace("\n", " ")

        return ScanResult(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            prompt_index=idx,
            repeat=rep,
            endpoint=self.name,
            latency_ms=round(latency, 2),
            server_ms=None,
            status=status,
            http_status=http_status,
            request_id=request_id,
            region="",
            vector_dims=None,
            prompt_chars=len(prompt),
            prompt=" ".join(prompt.split()),
            error=error,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Guardrail latency benchmark: Azure embedding postures + optional Prisma AIRS direct API."
    )
    p.add_argument("-o", "--output", default=None,
                   help="CSV path (default: embedding_bench_<timestamp>.csv)")
    p.add_argument("-n", "--num-prompts", type=int, default=10)
    p.add_argument("-p", "--prompts-file", default="prompts.txt")
    p.add_argument("-r", "--repeat", type=int, default=1,
                   help="Rounds per prompt (>1 = stabler percentiles)")
    p.add_argument("--delay", type=float, default=0.2,
                   help="Seconds between parallel rounds")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-request timeout in seconds")
    p.add_argument("--seed", type=int, default=None,
                   help="RNG seed for reproducible prompt sampling")
    p.add_argument("--no-warmup", action="store_true",
                   help="Skip untimed warmup request per endpoint")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="httpx DEBUG logs (counts toward timings — debug only)")
    return p.parse_args()


def load_prompts(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        log.error("Prompts file not found: %s", path)
        sys.exit(1)


def percentile(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(results: list[ScanResult], names: tuple[str, ...]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in names:
        rs = [r for r in results if r.endpoint == name]
        lat = sorted(r.latency_ms for r in rs if r.status in VALID_SCAN_STATUSES)
        srv = [r.server_ms for r in rs if r.server_ms is not None]
        s: dict[str, Any] = {
            "requests": len(rs),
            "success": sum(r.status == "SUCCESS" for r in rs),
            "blocked": sum(r.status == "BLOCKED" for r in rs),
            "errors": sum(r.status not in VALID_SCAN_STATUSES for r in rs),
        }
        if lat:
            s.update({
                "min_ms": round(lat[0], 2),
                "mean_ms": round(statistics.fmean(lat), 2),
                "p50_ms": round(statistics.median(lat), 2),
                "p90_ms": round(percentile(lat, 90), 2),
                "p95_ms": round(percentile(lat, 95), 2),
                "p99_ms": round(percentile(lat, 99), 2),
                "max_ms": round(lat[-1], 2),
                "stdev_ms": round(statistics.stdev(lat), 2) if len(lat) > 1 else 0.0,
            })
        if srv and lat:
            s["mean_server_ms"] = round(statistics.fmean(srv), 2)
            s["mean_network_overhead_ms"] = round(
                statistics.fmean(lat) - statistics.fmean(srv), 2
            )
        summary[name] = s

    rounds: dict[tuple[int, int], dict[str, float]] = {}
    for r in results:
        if r.status in VALID_SCAN_STATUSES:
            rounds.setdefault((r.prompt_index, r.repeat), {})[r.endpoint] = r.latency_ms

    for a, b in combinations(names, 2):
        deltas = [v[b] - v[a] for v in rounds.values() if a in v and b in v]
        if deltas:
            summary[f"delta_{b}_minus_{a}"] = {
                "pairs": len(deltas),
                "mean_ms": round(statistics.fmean(deltas), 2),
                "median_ms": round(statistics.median(deltas), 2),
                "min_ms": round(min(deltas), 2),
                "max_ms": round(max(deltas), 2),
                f"{b}_faster_pct": round(100 * sum(d < 0 for d in deltas) / len(deltas), 1),
            }

    complete = [v for v in rounds.values() if len(v) == len(names)]
    if complete:
        wins = dict.fromkeys(names, 0)
        for v in complete:
            wins[min(v, key=v.__getitem__)] += 1
        summary["fastest_win_rate"] = {
            "rounds": len(complete),
            **{n: round(100 * wins[n] / len(complete), 1) for n in names},
        }
    return summary


def print_summary(summary: dict[str, Any], names: tuple[str, ...]) -> None:
    log.info("")
    log.info("=" * 88)
    log.info("SUMMARY — client-side round-trip latency (ms)")
    log.info("=" * 88)
    log.info(
        "%-10s%5s%5s%5s%5s%9s%9s%9s%9s%9s%9s",
        "posture", "n", "ok", "blk", "err", "min", "mean", "p50", "p95", "p99", "max",
    )
    for name in names:
        s = summary[name]
        if "mean_ms" in s:
            log.info(
                "%-10s%5d%5d%5d%5d%9.1f%9.1f%9.1f%9.1f%9.1f%9.1f",
                name, s["requests"], s["success"], s["blocked"], s["errors"],
                s["min_ms"], s["mean_ms"], s["p50_ms"], s["p95_ms"], s["p99_ms"], s["max_ms"],
            )
        else:
            log.info("%-10s%5d  -- no successful scans --", name, s["requests"])
    for name in names:
        s = summary[name]
        if s.get("mean_server_ms") is not None:
            log.info(
                "%s: mean server-side %sms, network+client overhead %sms",
                name, s["mean_server_ms"], s["mean_network_overhead_ms"],
            )
    log.info("-" * 88)
    for a, b in combinations(names, 2):
        d = summary.get(f"delta_{b}_minus_{a}")
        if d:
            log.info(
                "%s - %s: mean %+.1fms | median %+.1fms | %s faster in %s%% of %d pairs",
                b, a, d["mean_ms"], d["median_ms"], b, d[f"{b}_faster_pct"], d["pairs"],
            )
    w = summary.get("fastest_win_rate")
    if w:
        log.info(
            "fastest posture win rate over %d complete rounds: %s",
            w["rounds"],
            ", ".join(f"{n} {w[n]}%" for n in names),
        )


def main() -> None:
    args = parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    cfg = {k: os.getenv(k) for k in REQUIRED_ENV}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)

    all_prompts = load_prompts(args.prompts_file)
    if args.seed is not None:
        random.seed(args.seed)
    sample_size = min(args.num_prompts, len(all_prompts))
    prompts = random.sample(all_prompts, sample_size)

    output = args.output or f"embedding_bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    summary_path = os.path.splitext(output)[0] + ".summary.json"

    scanners: list[EmbeddingScanner | AirsScanner] = [
        EmbeddingScanner("default", cfg["AZURE_AI_ENDPOINT"] or "", cfg["AZURE_AI_API_KEY"] or "",
                         cfg["DEPLOYMENT_DEFAULT"] or "", args.timeout),
        EmbeddingScanner("strict",  cfg["AZURE_AI_ENDPOINT"] or "", cfg["AZURE_AI_API_KEY"] or "",
                         cfg["DEPLOYMENT_STRICT"] or "",  args.timeout),
        EmbeddingScanner("prisma",  cfg["AZURE_AI_ENDPOINT"] or "", cfg["AZURE_AI_API_KEY"] or "",
                         cfg["DEPLOYMENT_PRISMA"] or "",  args.timeout),
    ]
    airs_key = os.getenv("PRISMA_AIRS_API_KEY")
    airs_profile = os.getenv("PRISMA_AIRS_PROFILE_NAME")
    if airs_key and airs_profile:
        scanners.append(AirsScanner(
            "airs",
            os.getenv("PRISMA_AIRS_ENDPOINT", AIRS_DEFAULT_ENDPOINT),
            airs_key,
            airs_profile,
            args.timeout,
        ))
        log.info("Prisma AIRS direct API enabled (profile: %s)", airs_profile)
    else:
        log.info("Prisma AIRS leg disabled — set PRISMA_AIRS_API_KEY + PRISMA_AIRS_PROFILE_NAME to enable")

    names = tuple(s.name for s in scanners)

    log.info(
        "Loaded %d prompts, sampled %d (seed=%s). "
        "%d round(s) x %d legs = %d timed requests.",
        len(all_prompts), sample_size, args.seed,
        sample_size * args.repeat, len(scanners),
        sample_size * args.repeat * len(scanners),
    )
    log.info("Detail CSV: %s", output)

    if not args.no_warmup:
        for scanner in scanners:
            label = getattr(scanner, "_deployment", getattr(scanner, "_url", ""))
            log.info("Warmup: %s (%s)", scanner.name, label)
            scanner.warmup()

    run_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[ScanResult] = []

    with (
        ThreadPoolExecutor(max_workers=len(scanners)) as pool,
        open(output, "w", newline="", encoding="utf-8") as csvfile,
    ):
        writer = csv.DictWriter(csvfile, fieldnames=[f.name for f in fields(ScanResult)])
        writer.writeheader()

        for idx, prompt in enumerate(prompts, 1):
            log.info("")
            log.info("[%d/%d] %.70s%s", idx, sample_size, prompt, "..." if len(prompt) > 70 else "")
            for rep in range(1, args.repeat + 1):
                futures = [pool.submit(s.scan, prompt, idx, rep) for s in scanners]
                round_results = [f.result() for f in futures]
                for r in round_results:
                    results.append(r)
                    writer.writerow(asdict(r))
                    log.info(
                        "  %-9s %8.2fms  %-9s dims=%-6s server=%-8s req=%s",
                        r.endpoint, r.latency_ms, r.status,
                        str(r.vector_dims) if r.vector_dims else "-",
                        f"{r.server_ms}ms" if r.server_ms is not None else "-",
                        r.request_id or "-",
                    )
                csvfile.flush()
                valid = [r for r in round_results if r.status in VALID_SCAN_STATUSES]
                if len(valid) >= 2:
                    fastest = min(valid, key=lambda r: r.latency_ms)
                    log.info("  fastest   %s (%.2fms)", fastest.endpoint, fastest.latency_ms)
                time.sleep(args.delay)

    summary = summarize(results, names)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run": {
                    "started_utc": run_started,
                    "model": "text-embedding-3-small",
                    "postures": {
                        "default": cfg["DEPLOYMENT_DEFAULT"],
                        "strict": cfg["DEPLOYMENT_STRICT"],
                        "prisma": cfg["DEPLOYMENT_PRISMA"],
                    },
                    "airs_profile": airs_profile,
                    "legs": list(names),
                    "prompts_sampled": sample_size,
                    "repeat": args.repeat,
                    "seed": args.seed,
                    "timeout_s": args.timeout,
                    "warmup": not args.no_warmup,
                },
                "endpoints": summary,
            },
            f,
            indent=2,
        )

    print_summary(summary, names)
    log.info("")
    log.info("Detail CSV:   %s", output)
    log.info("Summary JSON: %s", summary_path)


if __name__ == "__main__":
    main()
