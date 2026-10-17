"""Local Environment Sense Adapter (`godview/adapters/local/adapter.py`).

Implements SDD §2.2, §2.3, §10, Coordinator Decision A11 (observer exclusion) and A19
(honest reporting when Docker/kind are absent on Cloudtop), and TDD §1 (I-02, I-03, I-07), §6.2 #3:
- Discovers services, labels, ports, `/metrics` endpoints, and dependency edges from
  `docker-compose.yml` (and optional override files).
- Enriches `/metrics` endpoints from `prometheus.yml` scrape targets.
- Discovers `kind` cluster topology from `kind/cluster.yaml`.
- Extracts and redacts local PostgreSQL DSNs found in discovered service configurations,
  and parses entity tables from seed SQL files.
- Discovers local bare/worktree Git remotes.
- Provides `build_hermetic_local_adapters()` with randomised names for zero-config hermetic boot.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx
import yaml
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.config import (
    MANAGED_LABEL_KEY,
    MANAGED_LABEL_VALUE,
    is_excluded_resource,
)
from godview_shared.contracts import (
    FORBIDDEN_TELEMETRY_TABLES,
    DiscoveredEdgeDoc,
    DiscoveredEntityDoc,
    DiscoveredServiceDoc,
    DiscoveredServiceKind,
    TrustDecision,
    TrustViolation,
)
from godview_shared.otel import inject_baggage_headers

_PROM_SAMPLE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|NaN|[+-]Inf)$"
)
_PROM_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\"|[^"])*)"')


def redact_dsn_password(dsn: str) -> str:
    """Return a PostgreSQL DSN with any embedded password removed for safe locators/evidence."""
    if not dsn:
        return ""
    try:
        parsed = urlparse(dsn)
        if not parsed.scheme:
            return dsn
        netloc = parsed.hostname or "localhost"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"{parsed.username}@{netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except ValueError:
        return "postgresql://redacted"


def parse_prometheus_text(raw_text: str) -> dict[str, Any]:
    """Parse Prometheus text exposition format into metric families and sample counts."""
    metrics: dict[str, list[dict[str, Any]]] = {}
    types: dict[str, str] = {}
    sample_count = 0

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 4:
                types[parts[2]] = parts[3]
            continue
        if line.startswith("#"):
            continue
        match = _PROM_SAMPLE_RE.match(line)
        if not match:
            continue
        metric_name, labels_blob, value_str = match.groups()
        labels: dict[str, str] = {}
        if labels_blob:
            for l_match in _PROM_LABEL_RE.finditer(labels_blob):
                labels[l_match.group(1)] = l_match.group(2).replace('\\"', '"')
        try:
            value = float(value_str)
        except ValueError:
            continue
        metrics.setdefault(metric_name, []).append(
            {
                "labels": labels,
                "value": value,
                "type": types.get(metric_name, "untyped"),
            }
        )
        sample_count += 1

    return {
        "metrics": metrics,
        "types": types,
        "sample_count": sample_count,
    }


def _route_path_matches(template_path: str, actual_path: str) -> bool:
    """Match an OpenAPI path template (e.g. '/v1/orders/{id}') against an actual URL path."""
    t_parts = [p for p in template_path.strip("/").split("/") if p]
    a_parts = [p for p in actual_path.strip("/").split("/") if p]
    if len(t_parts) != len(a_parts):
        return False
    for t_seg, a_seg in zip(t_parts, a_parts, strict=True):
        if t_seg.startswith("{") and t_seg.endswith("}"):
            continue
        if t_seg != a_seg:
            return False
    return True


_ENV_VAR_DEFAULT_RE = re.compile(r"\$\{[A-Za-z0-9_]+:-([^}]+)\}")
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_DEF_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+([A-Za-z0-9_()]+)", re.MULTILINE)

_VALID_KINDS: frozenset[str] = frozenset(
    {
        "cloud_run",
        "gke_workload",
        "gce_vm",
        "cloud_sql",
        "api_gateway",
        "bucket",
        "reasoning_engine",
        "other",
    }
)


def _expand_compose_Placeholders(val: str) -> str:
    return _ENV_VAR_DEFAULT_RE.sub(r"\1", val)


def _default_docker_daemon_probe() -> bool:
    sock = Path("/var/run/docker.sock")
    return bool(shutil.which("docker")) and sock.exists()


def _default_kind_binary_probe() -> bool:
    return bool(shutil.which("kind"))


class LocalEnvironmentSenseAdapter(SenseAdapter):
    """Sense adapter for the local docker-compose + kind + Postgres + Git environment."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "local"
    name: str = "local-compose-kind-adapter"

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        compose_path: str | Path | None = None,
        kind_config_path: str | Path | None = None,
        prometheus_config_path: str | Path | None = None,
        seed_sql_dir: str | Path | None = None,
        git_repos_dir: str | Path | None = None,
        env_id: str = "local-compose",
        discovered_routes: Sequence[Mapping[str, Any]] | None = None,
        docker_daemon_probe: Callable[[], bool] | None = None,
        kind_binary_probe: Callable[[], bool] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base = Path(repo_root) if repo_root is not None else Path.cwd()
        self.compose_path = (
            Path(compose_path) if compose_path is not None else base / "docker-compose.yml"
        )
        self.kind_config_path = (
            Path(kind_config_path)
            if kind_config_path is not None
            else base / "local-env" / "kind" / "cluster.yaml"
        )
        self.prometheus_config_path = (
            Path(prometheus_config_path)
            if prometheus_config_path is not None
            else base / "local-env" / "prometheus" / "prometheus.yml"
        )
        self.seed_sql_dir = (
            Path(seed_sql_dir) if seed_sql_dir is not None else base / "local-env" / "seed"
        )
        self.git_repos_dir = (
            Path(git_repos_dir)
            if git_repos_dir is not None
            else base / "local-env" / "git" / "repos"
        )
        self.env_id = env_id
        self._discovered_routes: list[dict[str, Any]] = [dict(r) for r in (discovered_routes or ())]
        self._docker_probe = docker_daemon_probe or _default_docker_daemon_probe
        self._kind_probe = kind_binary_probe or _default_kind_binary_probe
        self._transport = http_transport

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.SCOPED_OBSERVABILITY,
            SenseCategory.DATA,
            SenseCategory.CODE,
            SenseCategory.CONTRACTS,
            SenseCategory.SYNTHETIC_INTERROGATION,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "metrics.scrape": get_capability("metrics.scrape"),
            "probe.execute": get_capability("probe.execute"),
        }

    async def health(self) -> AdapterHealth:
        now = datetime.now(tz=UTC)
        compose_exists = self.compose_path.is_file()
        kind_exists = self.kind_config_path.is_file()
        prom_exists = self.prometheus_config_path.is_file()
        git_exists = self.git_repos_dir.is_dir()
        docker_ok = bool(self._docker_probe())
        kind_ok = bool(self._kind_probe())

        any_static = compose_exists or kind_exists or git_exists
        if docker_ok and compose_exists:
            state: Literal["available", "partial", "unavailable"] = "available"
        elif any_static or docker_ok:
            state = "partial"
        else:
            state = "unavailable"

        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=state,
            evidence={
                "compose_file_present": compose_exists,
                "kind_config_present": kind_exists,
                "prometheus_config_present": prom_exists,
                "git_repos_dir_present": git_exists,
                "docker_daemon_available": docker_ok,
                "kind_binary_available": kind_ok,
            },
            checked_at=now,
        )

    async def discover(self) -> DiscoveryResult:
        """Discover local docker-compose services, kind cluster, Postgres DSNs, Prometheus targets, and Git remotes."""
        now_iso = datetime.now(tz=UTC).isoformat()
        prom_targets = self._read_prometheus_targets()
        kind_cluster = self._read_kind_cluster()
        git_remotes = self._discover_local_git_remotes()
        entities = self._discover_seed_entities()

        if not self.compose_path.is_file():
            return DiscoveryResult(
                services=[],
                edges=[],
                entities=entities,
                evidence={
                    "compose_file_present": False,
                    "kind_cluster": kind_cluster,
                    "local_git_remotes": git_remotes,
                    "postgres_dsns": [],
                },
            )

        raw_compose = yaml.safe_load(self.compose_path.read_text(encoding="utf-8")) or {}
        compose_services = raw_compose.get("services") or {}

        services: list[DiscoveredServiceDoc] = []
        observer_excluded: list[str] = []
        postgres_dsns: list[str] = []
        raw_links: list[tuple[str, str, str]] = []

        default_repo_ref = git_remotes[0] if git_remotes else None

        for svc_name, svc_cfg in compose_services.items():
            if not isinstance(svc_cfg, dict):
                continue
            labels = self._normalize_kv(svc_cfg.get("labels"))
            env_map = self._normalize_kv(svc_cfg.get("environment"))

            for env_key, env_val in env_map.items():
                expanded = _expand_compose_Placeholders(env_val)
                if env_key == "DATABASE_URL" or expanded.startswith(
                    ("postgres://", "postgresql://")
                ):
                    redacted = redact_dsn_password(expanded)
                    if redacted and redacted not in postgres_dsns:
                        postgres_dsns.append(redacted)

            if is_excluded_resource(svc_name, labels):
                observer_excluded.append(svc_name)
                continue

            kind = self._infer_kind(svc_name, svc_cfg, labels)
            internal_port = self._infer_internal_port(svc_cfg, env_map, labels)
            locator = self._infer_locator(svc_name, kind, internal_port, env_map, postgres_dsns)
            metrics_endpoint = self._infer_metrics_endpoint(
                svc_name,
                kind,
                internal_port,
                labels,
                prom_targets,
            )

            doc_id = f"{self.env_id}__{kind}.{svc_name}"
            services.append(
                DiscoveredServiceDoc(
                    doc_id=doc_id,
                    env_id=self.env_id,
                    name=svc_name,
                    kind=kind,
                    adapter_family="local",
                    locator=locator,
                    labels=labels,
                    metrics_endpoint=metrics_endpoint,
                    repo_ref=default_repo_ref,
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

            depends_on = svc_cfg.get("depends_on")
            dep_names: list[str] = []
            if isinstance(depends_on, list):
                dep_names = [str(d) for d in depends_on]
            elif isinstance(depends_on, dict):
                dep_names = [str(d) for d in depends_on.keys()]
            for dep in dep_names:
                raw_links.append(
                    (svc_name, dep, "tcp" if "postgres" in dep or "pg" in dep else "http")
                )

            for env_key, env_val in env_map.items():
                expanded = _expand_compose_Placeholders(env_val)
                if env_key.endswith("_URL") and expanded.startswith(("http://", "https://")):
                    parsed = urlparse(expanded)
                    if parsed.hostname:
                        raw_links.append((svc_name, parsed.hostname, parsed.scheme or "http"))

        by_name = {s.name: s for s in services}
        edges: list[DiscoveredEdgeDoc] = []
        seen_edges: set[tuple[str, str]] = set()
        for src_name, dst_name, proto in raw_links:
            if src_name not in by_name or dst_name not in by_name:
                continue
            src_id = (
                by_name[src_name].doc_id or f"{self.env_id}__{by_name[src_name].kind}.{src_name}"
            )
            dst_id = (
                by_name[dst_name].doc_id or f"{self.env_id}__{by_name[dst_name].kind}.{dst_name}"
            )
            if (src_id, dst_id) in seen_edges:
                continue
            seen_edges.add((src_id, dst_id))
            edges.append(
                DiscoveredEdgeDoc(
                    doc_id=f"{src_id}__to__{dst_id}",
                    src=src_id,
                    dst=dst_id,
                    protocol=proto,
                    evidence="config",
                    call_rate_baseline=0.0,
                    first_seen=now_iso,
                    last_seen=now_iso,
                )
            )

        return DiscoveryResult(
            services=services,
            edges=edges,
            entities=entities,
            evidence={
                "compose_services_discovered": len(services),
                "observer_services_excluded": observer_excluded,
                "postgres_dsns": postgres_dsns,
                "kind_cluster": kind_cluster,
                "local_git_remotes": git_remotes,
                "prometheus_targets": prom_targets,
            },
        )

    @staticmethod
    def _normalize_kv(raw: Any) -> dict[str, str]:
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        if isinstance(raw, list):
            out: dict[str, str] = {}
            for item in raw:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    out[k.strip()] = v.strip()
            return out
        return {}

    @staticmethod
    def _infer_kind(
        svc_name: str,
        svc_cfg: dict[str, Any],
        labels: dict[str, str],
    ) -> DiscoveredServiceKind:
        explicit = labels.get("godview_kind", "").strip()
        if explicit in _VALID_KINDS:
            return explicit  # type: ignore[return-value]
        image = str(svc_cfg.get("image", "")).lower()
        if "postgres" in image or labels.get("godview_datastore") == "postgres":
            return "cloud_sql"
        if "vm" in svc_name:
            return "gce_vm"
        return "cloud_run"

    @staticmethod
    def _infer_internal_port(
        svc_cfg: dict[str, Any],
        env_map: dict[str, str],
        labels: dict[str, str],
    ) -> int:
        if "metrics_port" in labels and str(labels["metrics_port"]).strip().isdigit():
            return int(str(labels["metrics_port"]).strip())
        if "PORT" in env_map:
            expanded_port = _expand_compose_Placeholders(env_map["PORT"]).strip()
            if expanded_port.isdigit():
                return int(expanded_port)
        ports = svc_cfg.get("ports") or []
        if ports:
            first = str(ports[0])
            parts = first.split(":")
            candidate_port = parts[-1].split("/")[0].strip()
            if candidate_port.isdigit():
                return int(candidate_port)
        return 8080

    @staticmethod
    def _infer_locator(
        svc_name: str,
        kind: DiscoveredServiceKind,
        port: int,
        env_map: dict[str, str],
        postgres_dsns: list[str],
    ) -> str:
        if kind == "cloud_sql":
            db_user = _expand_compose_Placeholders(env_map.get("POSTGRES_USER", "app_user"))
            db_name = _expand_compose_Placeholders(env_map.get("POSTGRES_DB", "db"))
            dsn = f"postgresql://{db_user}@{svc_name}:{port}/{db_name}"
            if dsn not in postgres_dsns:
                postgres_dsns.append(dsn)
            return dsn
        return f"http://{svc_name}:{port}"

    @staticmethod
    def _infer_metrics_endpoint(
        svc_name: str,
        kind: DiscoveredServiceKind,
        port: int,
        labels: dict[str, str],
        prom_targets: dict[str, str],
    ) -> str | None:
        if kind == "cloud_sql":
            return None
        if svc_name in prom_targets:
            return prom_targets[svc_name]
        if "metrics_port" in labels or "metrics_path" in labels:
            m_port = int(labels.get("metrics_port", port))
            m_path = labels.get("metrics_path", "/metrics")
            if not m_path.startswith("/"):
                m_path = "/" + m_path
            return f"http://{svc_name}:{m_port}{m_path}"
        return None

    def _read_prometheus_targets(self) -> dict[str, str]:
        if not self.prometheus_config_path.is_file():
            return {}
        try:
            doc = yaml.safe_load(self.prometheus_config_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return {}
        targets: dict[str, str] = {}
        for job in doc.get("scrape_configs") or []:
            if not isinstance(job, dict):
                continue
            job_name = str(job.get("job_name", ""))
            m_path = str(job.get("metrics_path", "/metrics"))
            for sc in job.get("static_configs") or []:
                t_list = sc.get("targets") or []
                if t_list:
                    host_port = str(t_list[0])
                    svc_key = host_port.split(":")[0] or job_name
                    targets[svc_key] = f"http://{host_port}{m_path}"
        return targets

    def _read_kind_cluster(self) -> dict[str, Any] | None:
        if not self.kind_config_path.is_file():
            return None
        try:
            doc = yaml.safe_load(self.kind_config_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return None
        if not isinstance(doc, dict):
            return None
        name = str(doc.get("name", "godview"))
        nodes = doc.get("nodes") or []
        port_mappings: list[dict[str, Any]] = []
        for node in nodes:
            if isinstance(node, dict):
                for pm in node.get("extraPortMappings") or []:
                    if isinstance(pm, dict):
                        port_mappings.append(dict(pm))
        return {
            "name": name,
            "kube_context": f"kind-{name}",
            "api_version": doc.get("apiVersion", "kind.x-k8s.io/v1alpha4"),
            "node_count": len(nodes),
            "extra_port_mappings": port_mappings,
        }

    def _discover_local_git_remotes(self) -> list[str]:
        if not self.git_repos_dir.is_dir():
            return []
        remotes: list[str] = []
        for child in sorted(self.git_repos_dir.iterdir()):
            if child.is_dir() and (
                child.name.endswith(".git")
                or (child / ".git").exists()
                or (child / "HEAD").is_file()
            ):
                remotes.append(f"file://{child.resolve()}")
        return remotes

    def _discover_seed_entities(self) -> list[DiscoveredEntityDoc]:
        if not self.seed_sql_dir.is_dir():
            return []
        entities: list[DiscoveredEntityDoc] = []
        seen_tables: set[str] = set()
        for sql_file in sorted(self.seed_sql_dir.glob("*.sql")):
            try:
                text = sql_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _CREATE_TABLE_RE.finditer(text):
                tbl_name, body = match.groups()
                t_clean = tbl_name.strip()
                if t_clean in seen_tables or t_clean in FORBIDDEN_TELEMETRY_TABLES:
                    continue
                seen_tables.add(t_clean)
                cols: list[str] = []
                for c_match in _COLUMN_DEF_RE.finditer(body):
                    col_token = c_match.group(1)
                    if col_token.upper() not in (
                        "PRIMARY",
                        "FOREIGN",
                        "CONSTRAINT",
                        "CHECK",
                        "UNIQUE",
                    ):
                        cols.append(col_token)
                corr_keys = [
                    c
                    for c in cols
                    if c in ("journey_id", "customer_id", "trace_id") or c.endswith("_id")
                ]
                entities.append(
                    DiscoveredEntityDoc(
                        doc_id=f"{self.env_id}__{t_clean}",
                        name=t_clean,
                        correlation_keys=corr_keys,
                        tables=[t_clean],
                        states=[],
                        provenance=sql_file.name,
                    )
                )
        return entities

    def _is_discovered_route_probe_safe(self, method: str, url: str) -> bool:
        """Check whether (method, url_path) is marked x-godview-probe-safe: true in discovered contracts."""
        discovered = getattr(self, "_discovered_routes", None) or ()
        url_path = urlparse(url).path or "/"
        for route in discovered:
            if not isinstance(route, Mapping):
                continue
            r_method = str(route.get("method", "")).upper()
            r_path = str(route.get("path", ""))
            if r_method != method.upper():
                continue
            if not _route_path_matches(r_path, url_path):
                continue
            if bool(route.get("admin_action") or route.get("x-godview-admin-action")):
                return False
            if bool(route.get("probe_safe") or route.get("x-godview-probe-safe")) is True:
                return True
        return False

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"timeout": 10.0}
        if getattr(self, "_transport", None) is not None:
            kwargs["transport"] = self._transport

        if capability_id == "metrics.scrape":
            endpoint = str(args["endpoint"])
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(endpoint)
                resp.raise_for_status()
                parsed = parse_prometheus_text(resp.text)
                parsed["endpoint"] = endpoint
                return parsed

        if capability_id == "probe.execute":
            if args.get("probe_safe") is False:
                raise TrustViolation(
                    "Refusing to probe endpoint with explicit probe_safe=False (TDD I-07)"
                )
            url = str(args["url"])
            method = str(args.get("method", "GET")).upper()
            if not self._is_discovered_route_probe_safe(method, url):
                raise TrustViolation(
                    f"Refusing to probe {method} {url}: route is not marked "
                    "x-godview-probe-safe: true in discovered contracts (TDD I-07)"
                )
            if method not in ("GET", "HEAD"):
                if (
                    trust_decision is None
                    or not trust_decision.authorized
                    or trust_decision.effective_level < 1
                ):
                    raise TrustViolation(
                        f"Non-GET probe.execute ({method} {url}) requires an authorized "
                        "TrustDecision with effective_level >= 1 (Agent Spec §6)"
                    )

            baggage_hdrs = inject_baggage_headers(
                journey_id=args.get("journey_id"),
                customer_id=args.get("customer_id"),
                synthetic=True,
            )
            headers = {**(args.get("headers") or {}), **baggage_hdrs}
            body = args.get("json")
            if isinstance(body, dict):
                body = {**body, "is_ai_generated": True}
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.request(method, url, headers=headers, json=body)
                return {
                    "status_code": resp.status_code,
                    "synthetic_marker_sent": True,
                    "baggage_sent": headers.get("baggage"),
                    "body": resp.json()
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else resp.text,
                }

        raise NotImplementedError(
            f"Adapter '{self.name}' does not implement capability '{capability_id}'"
        )


class _HermeticFixtureLocalAdapter(SenseAdapter):
    """Self-contained hermetic local adapter with randomised names for zero-config perception tests (TDD I-02)."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "local"
    name: str = "hermetic-local-fixture-adapter"

    def __init__(self, *, env_id: str = "local-compose") -> None:
        self.env_id = env_id
        suffix = uuid.uuid4().hex[:6]
        self._gw_name = f"svc-gateway-{suffix}"
        self._eval_name = f"svc-evaluator-{suffix}"
        self._db_name = f"db-store-{suffix}"
        self._entity_name = f"tx_application_{suffix}"

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.SCOPED_OBSERVABILITY,
            SenseCategory.DATA,
            SenseCategory.CODE,
            SenseCategory.CONTRACTS,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "metrics.scrape": get_capability("metrics.scrape"),
            "probe.execute": get_capability("probe.execute"),
        }

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state="available",
            evidence={"mode": "hermetic_fixture"},
            checked_at=datetime.now(tz=UTC),
        )

    async def discover(self) -> DiscoveryResult:
        now_iso = datetime.now(tz=UTC).isoformat()
        gw_id = f"{self.env_id}__cloud_run.{self._gw_name}"
        eval_id = f"{self.env_id}__gke_workload.{self._eval_name}"
        db_id = f"{self.env_id}__cloud_sql.{self._db_name}"

        services = [
            DiscoveredServiceDoc(
                doc_id=gw_id,
                env_id=self.env_id,
                name=self._gw_name,
                kind="cloud_run",
                adapter_family="local",
                locator=f"http://{self._gw_name}:8080",
                labels={MANAGED_LABEL_KEY: MANAGED_LABEL_VALUE},
                classification="journey_linked",
                routes=[{"method": "POST", "path": "/v1/apply", "x-godview-probe-safe": True}],
                metrics_endpoint=f"http://{self._gw_name}:8080/metrics",
                repo_ref="git://local/hermetic-repo",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=eval_id,
                env_id=self.env_id,
                name=self._eval_name,
                kind="gke_workload",
                adapter_family="kubernetes",
                locator=f"k8s://ns-main/{self._eval_name}",
                labels={MANAGED_LABEL_KEY: MANAGED_LABEL_VALUE},
                classification="journey_linked",
                metrics_endpoint=f"http://{self._eval_name}:8080/metrics",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
            DiscoveredServiceDoc(
                doc_id=db_id,
                env_id=self.env_id,
                name=self._db_name,
                kind="cloud_sql",
                adapter_family="sql",
                locator=f"postgresql://godview_readonly@{self._db_name}:5432/appdb",
                labels={MANAGED_LABEL_KEY: MANAGED_LABEL_VALUE},
                classification="journey_linked",
                first_seen=now_iso,
                last_seen=now_iso,
                status="active",
            ),
        ]
        edges = [
            DiscoveredEdgeDoc(
                doc_id=f"{gw_id}__to__{eval_id}",
                src=gw_id,
                dst=eval_id,
                protocol="http",
                evidence="config",
                call_rate_baseline=10.0,
                first_seen=now_iso,
                last_seen=now_iso,
            )
        ]
        entities = [
            DiscoveredEntityDoc(
                doc_id=f"{self.env_id}__{self._entity_name}",
                name=self._entity_name,
                correlation_keys=["journey_id", "customer_id"],
                tables=[self._entity_name],
                owning_service_id=gw_id,
                states=["PENDING", "VERIFIED", "APPROVED", "SETTLED"],
                provenance="hermetic_fixture",
            )
        ]
        return DiscoveryResult(
            services=services,
            edges=edges,
            entities=entities,
            evidence={"mode": "hermetic_fixture"},
        )


def build_hermetic_local_adapters(*, env_id: str = "local-compose") -> list[SenseAdapter]:
    """Return environment-neutral hermetic local adapters with randomised names (TDD I-02, Rule B6)."""
    env_mode = os.environ.get("GODVIEW_ENV", "").strip().lower()
    if env_mode != "hermetic":
        raise RuntimeError(
            f"build_hermetic_local_adapters() is only permitted when GODVIEW_ENV='hermetic' "
            f"(got {env_mode!r}, Rule B6)"
        )
    return [_HermeticFixtureLocalAdapter(env_id=env_id)]
