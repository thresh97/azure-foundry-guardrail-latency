#!/usr/bin/env python3
"""Guardrail latency benchmark: Azure AI Foundry guardrail postures + optional extra legs.

All scanners run IN PARALLEL per prompt for a true head-to-head. Captures full
client-side round-trip, RAI verdicts, and request IDs into a long-format CSV,
then emits a console summary + JSON with percentiles, pairwise deltas, and
fastest-endpoint win rate.

Azure Foundry postures (always active) — Responses API via /openai/v1:
  default   azure-default  (Microsoft.Default RAI — system-managed)
  strict    azure-strict   (custom low-severity content filters, prompt + completion)
  prisma    prisma-airs    (Azure RAI pass-through + Prisma AIRS via Foundry integration)

Optional standalone scanner legs (scan-only, no model generation):
  content   Azure AI Content Safety — shieldPrompt (jailbreak / prompt injection)
  security  Azure AI Content Safety — text:analyze  (hate, violence, self-harm, sexual)
  airs      Prisma AIRS             — POST /v1/scan/sync/request

Required env / .env:
  AZURE_AI_ENDPOINT        https://<subdomain>.services.ai.azure.com/openai/v1
  DEPLOYMENT_DEFAULT       azure-default
  DEPLOYMENT_STRICT        azure-strict
  DEPLOYMENT_PRISMA        prisma-airs

Optional env:
  AZURE_AI_API_KEY                  <api key> — omit to use DefaultAzureCredential (MSI / az login)
  AZURE_CONTENT_SAFETY_ENDPOINT     <base url e.g. https://<name>.cognitiveservices.azure.com>
                                    enables both content (shieldPrompt) and security (text:analyze) legs
  PRISMA_AIRS_DIRECT_API_KEY        <key>     — enables the airs leg
  PRISMA_AIRS_DIRECT_PROFILE_NAME   <name>
  PRISMA_AIRS_ENDPOINT              https://service.api.aisecurity.paloaltonetworks.com (default)
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
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

log = logging.getLogger("bench")

REQUIRED_ENV = (
    "AZURE_AI_ENDPOINT",
    "DEPLOYMENT_DEFAULT",
    "DEPLOYMENT_STRICT",
    "DEPLOYMENT_PRISMA",
)
AIRS_DEFAULT_ENDPOINT = "https://service.api.aisecurity.paloaltonetworks.com"
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
    """Sends a request to one Azure AI Foundry deployment via the Responses API.

    A content_filter error means the RAI policy or external guardrail fired (BLOCKED).
    Any other non-2xx is recorded as HTTP_<code>.
    """

    def __init__(
        self,
        name: str,
        endpoint: str,
        api_key: str,
        deployment: str,
        timeout: float,
    ) -> None:
        self.name = name
        self._deployment = deployment
        self._client = OpenAI(
            base_url=endpoint,
            api_key=api_key,
            max_retries=0,
            timeout=httpx.Timeout(timeout),
        )

    def probe(self) -> tuple[bool, str]:
        try:
            self._client.responses.create(model=self._deployment, input="probe")
            return True, "ok"
        except APIStatusError as e:
            # 4xx from the model/guardrail means the endpoint is reachable
            if "content_filter" in (e.response.text or ""):
                return True, "ok (content_filter on probe prompt)"
            return True, f"ok (HTTP {e.status_code})"
        except (APITimeoutError, APIConnectionError) as e:
            return False, str(e).replace("\n", " ")[:200]

    def warmup(self) -> None:
        with contextlib.suppress(Exception):
            self._client.responses.create(model=self._deployment, input="warmup")

    def scan(self, prompt: str, idx: int, rep: int) -> ScanResult:
        http_status: int | None = None
        server_ms: float | None = None
        status, error = "SUCCESS", ""
        headers = httpx.Headers()
        start = time.perf_counter()
        try:
            raw = self._client.responses.with_raw_response.create(
                model=self._deployment, input=prompt
            )
            latency = (time.perf_counter() - start) * 1000
            http_status = raw.status_code
            headers = raw.headers
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
            vector_dims=None,
            prompt_chars=len(prompt),
            prompt=" ".join(prompt.split()),
            error=error,
        )


class ContentShieldScanner:
    """Azure AI Content Safety — shieldPrompt API (jailbreak / prompt-injection detection).

    POST /contentsafety/text:shieldPrompt?api-version=2024-09-01
    BLOCKED when userPromptAnalysis.attackDetected is true. Scan-only, no model generation.
    """

    _API_PATH = "/contentsafety/text:shieldPrompt?api-version=2024-09-01"

    def __init__(self, name: str, base_url: str, api_key: str | None,
                 token_provider: Any | None, timeout: float) -> None:
        self.name = name
        self._url = base_url.rstrip("/") + self._API_PATH
        self._api_key = api_key
        self._token_provider = token_provider
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def _auth_headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Ocp-Apim-Subscription-Key": self._api_key, "Content-Type": "application/json"}
        token = self._token_provider()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def probe(self) -> tuple[bool, str]:
        try:
            resp = self._http.post(self._url, headers=self._auth_headers(), json={"userPrompt": "probe"})
            if resp.status_code in (200, 400):
                return True, f"ok (HTTP {resp.status_code})"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.TimeoutException:
            return False, "timeout"
        except httpx.HTTPError as e:
            return False, str(e).replace("\n", " ")[:200]

    def warmup(self) -> None:
        with contextlib.suppress(Exception):
            self._http.post(self._url, headers=self._auth_headers(), json={"userPrompt": "warmup"})

    def scan(self, prompt: str, idx: int, rep: int) -> ScanResult:
        http_status: int | None = None
        status, error, request_id = "SUCCESS", "", ""
        start = time.perf_counter()
        try:
            resp = self._http.post(self._url, headers=self._auth_headers(), json={"userPrompt": prompt})
            latency = (time.perf_counter() - start) * 1000
            http_status = resp.status_code
            request_id = resp.headers.get("x-request-id", "")
            if resp.status_code == 200:
                if resp.json().get("userPromptAnalysis", {}).get("attackDetected"):
                    status = "BLOCKED"
                    error = "attackDetected=true"
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
            prompt_index=idx, repeat=rep, endpoint=self.name,
            latency_ms=round(latency, 2), server_ms=None, status=status,
            http_status=http_status, request_id=request_id, region="",
            vector_dims=None, prompt_chars=len(prompt),
            prompt=" ".join(prompt.split()), error=error,
        )


class TextAnalyzeScanner:
    """Azure AI Content Safety — text:analyze API (harmful content detection).

    POST /contentsafety/text:analyze?api-version=2024-09-01
    BLOCKED when any category (Hate, SelfHarm, Sexual, Violence) has severity > 0.
    Scan-only, no model generation.
    """

    _API_PATH = "/contentsafety/text:analyze?api-version=2024-09-01"
    _CATEGORIES = ["Hate", "SelfHarm", "Sexual", "Violence"]

    def __init__(self, name: str, base_url: str, api_key: str | None,
                 token_provider: Any | None, timeout: float) -> None:
        self.name = name
        self._url = base_url.rstrip("/") + self._API_PATH
        self._api_key = api_key
        self._token_provider = token_provider
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def _auth_headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Ocp-Apim-Subscription-Key": self._api_key, "Content-Type": "application/json"}
        token = self._token_provider()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _body(self, prompt: str) -> dict[str, Any]:
        return {"text": prompt, "categories": self._CATEGORIES, "outputType": "FourSeverityLevels"}

    def probe(self) -> tuple[bool, str]:
        try:
            resp = self._http.post(self._url, headers=self._auth_headers(), json=self._body("probe"))
            if resp.status_code in (200, 400):
                return True, f"ok (HTTP {resp.status_code})"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.TimeoutException:
            return False, "timeout"
        except httpx.HTTPError as e:
            return False, str(e).replace("\n", " ")[:200]

    def warmup(self) -> None:
        with contextlib.suppress(Exception):
            self._http.post(self._url, headers=self._auth_headers(), json=self._body("warmup"))

    def scan(self, prompt: str, idx: int, rep: int) -> ScanResult:
        http_status: int | None = None
        status, error, request_id = "SUCCESS", "", ""
        start = time.perf_counter()
        try:
            resp = self._http.post(self._url, headers=self._auth_headers(), json=self._body(prompt))
            latency = (time.perf_counter() - start) * 1000
            http_status = resp.status_code
            request_id = resp.headers.get("x-request-id", "")
            if resp.status_code == 200:
                flagged = [c for c in resp.json().get("categoriesAnalysis", []) if c.get("severity", 0) > 0]
                if flagged:
                    status = "BLOCKED"
                    error = "flagged=" + ",".join(f"{c['category']}:{c['severity']}" for c in flagged)
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
            prompt_index=idx, repeat=rep, endpoint=self.name,
            latency_ms=round(latency, 2), server_ms=None, status=status,
            http_status=http_status, request_id=request_id, region="",
            vector_dims=None, prompt_chars=len(prompt),
            prompt=" ".join(prompt.split()), error=error,
        )


class AirsScanner:
    """Hits the Prisma AIRS synchronous scan API directly (no Azure model call).

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

    def probe(self) -> tuple[bool, str]:
        try:
            resp = self._http.post(self._url, json=self._payload("probe", 0, 0))
            if resp.status_code == 200:
                return True, "ok"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.TimeoutException:
            return False, "timeout"
        except httpx.HTTPError as e:
            return False, str(e).replace("\n", " ")[:200]

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
        description="Guardrail latency benchmark: three Azure AI Foundry postures + optional Prisma AIRS direct API."
    )
    p.add_argument("-o", "--output", default=None,
                   help="CSV path (default: guardrail_bench_<timestamp>.csv)")
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


def resolve_azure_auth() -> str | Any:
    """Return an api_key value for OpenAI(base_url=..., api_key=...).

    Priority:
      1. AZURE_AI_API_KEY in env  → returned as a plain string.
      2. Managed Identity         → token provider callable (Azure VM with SAI/UAI).
      3. Azure CLI (az login)     → token provider callable (developer laptop).

    The chosen method is logged at startup so it's always visible.
    """
    api_key = os.getenv("AZURE_AI_API_KEY")
    if api_key:
        log.info("Azure auth: API key (AZURE_AI_API_KEY)")
        return api_key

    try:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
    except ImportError:
        log.error("No AZURE_AI_API_KEY set and azure-identity is not installed. "
                  "pip install azure-identity  or set AZURE_AI_API_KEY.")
        sys.exit(1)

    # Detect likely sub-credential for a clear startup message.
    if os.getenv("IDENTITY_ENDPOINT"):
        method = "ManagedIdentityCredential (VM instance identity)"
    elif any(os.getenv(v) for v in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID")):
        method = "EnvironmentCredential (service principal env vars)"
    else:
        method = "AzureCliCredential (az login)"

    log.info("Azure auth: DefaultAzureCredential → %s", method)
    return get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")


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

    azure_auth = resolve_azure_auth()

    all_prompts = load_prompts(args.prompts_file)
    if args.seed is not None:
        random.seed(args.seed)
    sample_size = min(args.num_prompts, len(all_prompts))
    prompts = random.sample(all_prompts, sample_size)

    output = args.output or f"guardrail_bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    summary_path = os.path.splitext(output)[0] + ".summary.json"

    scanners: list[EmbeddingScanner | ContentShieldScanner | TextAnalyzeScanner | AirsScanner] = [
        EmbeddingScanner("default", cfg["AZURE_AI_ENDPOINT"] or "", azure_auth,
                         cfg["DEPLOYMENT_DEFAULT"] or "", args.timeout),
        EmbeddingScanner("strict",  cfg["AZURE_AI_ENDPOINT"] or "", azure_auth,
                         cfg["DEPLOYMENT_STRICT"] or "",  args.timeout),
        EmbeddingScanner("prisma",  cfg["AZURE_AI_ENDPOINT"] or "", azure_auth,
                         cfg["DEPLOYMENT_PRISMA"] or "",  args.timeout),
    ]

    cs_endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
    if cs_endpoint:
        cs_api_key = os.getenv("AZURE_AI_API_KEY")
        cs_token_provider: Any | None = None
        if not cs_api_key:
            try:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                # Separate credential instances so each scanner has its own token cache.
                cs_token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )
                cs_token_provider2 = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )
            except ImportError:
                log.warning("azure-identity not installed; content safety legs require AZURE_AI_API_KEY — skipping")
                cs_endpoint = None
        else:
            cs_token_provider2 = None
        if cs_endpoint:
            scanners.append(ContentShieldScanner("content", cs_endpoint, cs_api_key, cs_token_provider, args.timeout))
            scanners.append(TextAnalyzeScanner("security", cs_endpoint, cs_api_key, cs_token_provider2, args.timeout))
            log.info("Content Safety legs enabled (%s)", cs_endpoint)
    else:
        log.info("Content Safety legs disabled — set AZURE_CONTENT_SAFETY_ENDPOINT to enable")

    airs_key = os.getenv("PRISMA_AIRS_DIRECT_API_KEY") or os.getenv("PRISMA_AIRS_API_KEY")
    airs_profile = os.getenv("PRISMA_AIRS_DIRECT_PROFILE_NAME") or os.getenv("PRISMA_AIRS_PROFILE_NAME")
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
        log.info("Prisma AIRS leg disabled — set PRISMA_AIRS_DIRECT_API_KEY + PRISMA_AIRS_DIRECT_PROFILE_NAME to enable")

    # Pre-flight: probe optional legs (content, security) and drop any that are unreachable.
    # Required legs (default, strict, prisma) abort on failure.
    REQUIRED_LEGS = {"default", "strict", "prisma"}
    live_scanners: list[EmbeddingScanner | ContentShieldScanner | TextAnalyzeScanner | AirsScanner] = []
    log.info("Pre-flight probe...")
    for scanner in scanners:
        ok, msg = scanner.probe()
        log.info("  %-9s %s", scanner.name, msg)
        if ok:
            live_scanners.append(scanner)
        elif scanner.name in REQUIRED_LEGS:
            log.error("Required leg %r failed probe: %s — aborting.", scanner.name, msg)
            sys.exit(1)
        else:
            log.warning("Optional leg %r failed probe — skipping.", scanner.name)
    scanners = live_scanners

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
                    "model": cfg["DEPLOYMENT_DEFAULT"],
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
