"""Git & GitHub Sense Adapter (`godview/adapters/git/adapter.py`).

Implements SDD §2.2, §2.3, §10 and TDD §1 (I-02, I-03, I-05, I-06), §3.6, §3.7, §6.2 #3,
and Agent Spec §4.4, §5.1, §6:
- Generic Git discovery (local clones/bare remotes + `GODVIEW_HINT_REPO_URLS` widening scope).
- GitHub webhook HMAC-SHA256 verification (`X-Hub-Signature-256`) and CloudEvent translation
  (`godview.webhook.github.push`, `pull_request`, `workflow_run`).
- Contract reading for OpenAPI (`x-godview-probe-safe`, `x-godview-admin-action`,
  `x-godview-reversal`), Protobuf (`.proto`), and JSON Schema (`$schema`).
- `git.create_rollback_pr` as the exact 5-call recipe (`get_commit` -> `get_file_contents` at
  parent -> `create_branch` -> `push_files` -> `create_pull_request`, plus optional `create_issue`)
  through the `github-mcp` client interface agreed with `@adapters-gcp` (#72/#73).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
import yaml
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import (
    CapabilitySpec,
    get_capability,
    validate_mcp_tool_binding,
)
from godview_shared.config import parse_csv_env
from godview_shared.contracts import (
    DiscoveredEntityDoc,
    NervousSystemEvent,
    TrustDecision,
    TrustViolation,
)


class WebhookSignatureError(PermissionError):
    """Raised when a GitHub webhook HMAC-SHA256 signature is missing or invalid."""


@runtime_checkable
class McpToolClientProtocol(Protocol):
    """Protocol agreed with @adapters-gcp (#72/#73) for official MCP server clients."""

    server_name: str

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


def verify_github_webhook_hmac(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify GitHub X-Hub-Signature-256 header (`sha256=<hex>`) using constant-time comparison."""
    if not signature_header or not secret:
        return False
    header = signature_header.strip()
    if not header.startswith("sha256="):
        return False
    provided_hex = header.removeprefix("sha256=").strip()
    if not provided_hex:
        return False
    expected_hex = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_hex, provided_hex)


_PROTO_PACKAGE_RE = re.compile(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", re.MULTILINE)
_PROTO_SERVICE_RE = re.compile(r"service\s+([A-Za-z0-9_]+)\s*\{([^}]*)\}", re.DOTALL)
_PROTO_RPC_RE = re.compile(
    r"rpc\s+([A-Za-z0-9_]+)\s*\(\s*(?:stream\s+)?([A-Za-z0-9_.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?([A-Za-z0-9_.]+)\s*\)"
)
_PROTO_MESSAGE_RE = re.compile(r"message\s+([A-Za-z0-9_]+)\s*\{([^}]*)\}", re.DOTALL)
_PROTO_FIELD_RE = re.compile(
    r"^\s*(?:repeated\s+|optional\s+)?([a-zA-Z0-9_.]+)\s+([a-zA-Z0-9_]+)\s*=\s*\d+",
    re.MULTILINE,
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


def parse_contract_document(content: str, *, filename: str = "") -> dict[str, Any]:
    """Parse an OpenAPI, Protobuf (.proto), or JSON Schema contract into routes and entities."""
    fname_lower = filename.lower()
    stripped = content.strip()

    if fname_lower.endswith(".proto") or (
        "syntax" in stripped and "proto" in stripped and not stripped.startswith("{")
    ):
        return _parse_proto_contract(stripped, filename=filename)

    try:
        parsed_obj = json.loads(stripped)
    except json.JSONDecodeError:
        parsed_obj = yaml.safe_load(stripped)

    if not isinstance(parsed_obj, dict):
        raise ValueError(f"Contract document '{filename}' did not parse to a mapping")

    if "openapi" in parsed_obj or "swagger" in parsed_obj or "paths" in parsed_obj:
        return _parse_openapi_contract(parsed_obj, filename=filename)

    if (
        "$schema" in parsed_obj
        or fname_lower.endswith(".schema.json")
        or parsed_obj.get("type") == "object"
    ):
        return _parse_json_schema_contract(parsed_obj, filename=filename)

    raise ValueError(f"Unrecognized contract format in '{filename}'")


def _parse_openapi_contract(doc: dict[str, Any], *, filename: str) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    paths = doc.get("paths") or {}
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method_key, op_spec in path_item.items():
            if method_key.lower() not in _HTTP_METHODS or not isinstance(op_spec, dict):
                continue
            probe_safe = bool(op_spec.get("x-godview-probe-safe", False))
            admin_cap = op_spec.get("x-godview-admin-action")
            reversal = op_spec.get("x-godview-reversal")
            routes.append(
                {
                    "method": method_key.upper(),
                    "path": str(path_str),
                    "operation_id": op_spec.get("operationId", ""),
                    "probe_safe": probe_safe,
                    "x-godview-probe-safe": probe_safe,
                    "admin": bool(admin_cap),
                    "admin_capability": str(admin_cap) if admin_cap else None,
                    "reversal": reversal if isinstance(reversal, dict) else None,
                }
            )

    entities: list[dict[str, Any]] = []
    schemas = (doc.get("components") or {}).get("schemas") or doc.get("definitions") or {}
    if isinstance(schemas, dict):
        for schema_name, schema_spec in schemas.items():
            if not isinstance(schema_spec, dict):
                continue
            props = schema_spec.get("properties") or {}
            prop_names = [str(k) for k in props] if isinstance(props, dict) else []
            corr_keys = [
                k
                for k in prop_names
                if k in ("journey_id", "customer_id", "trace_id") or k.endswith("_id")
            ]
            states: list[str] = []
            if isinstance(props, dict) and isinstance(props.get("status"), dict):
                enum_vals = props["status"].get("enum")
                if isinstance(enum_vals, list):
                    states = [str(v) for v in enum_vals]
            entities.append(
                {
                    "name": str(schema_name),
                    "fields": prop_names,
                    "correlation_keys": corr_keys,
                    "states": states,
                    "provenance": filename or "openapi",
                }
            )

    return {
        "format": "openapi",
        "title": (doc.get("info") or {}).get("title", ""),
        "routes": routes,
        "entities": entities,
    }


def _parse_proto_contract(proto_text: str, *, filename: str) -> dict[str, Any]:
    pkg_match = _PROTO_PACKAGE_RE.search(proto_text)
    pkg = pkg_match.group(1) if pkg_match else ""

    routes: list[dict[str, Any]] = []
    for svc_match in _PROTO_SERVICE_RE.finditer(proto_text):
        svc_name, svc_body = svc_match.groups()
        qualified_svc = f"{pkg}.{svc_name}" if pkg else svc_name
        for rpc_match in _PROTO_RPC_RE.finditer(svc_body):
            rpc_name, req_type, resp_type = rpc_match.groups()
            routes.append(
                {
                    "method": "POST",
                    "path": f"/{qualified_svc}/{rpc_name}",
                    "operation_id": rpc_name,
                    "request_type": req_type,
                    "response_type": resp_type,
                    "probe_safe": False,
                    "admin": False,
                }
            )

    entities: list[dict[str, Any]] = []
    for msg_match in _PROTO_MESSAGE_RE.finditer(proto_text):
        msg_name, msg_body = msg_match.groups()
        fields = [f_match.group(2) for f_match in _PROTO_FIELD_RE.finditer(msg_body)]
        corr_keys = [
            f for f in fields if f in ("journey_id", "customer_id", "trace_id") or f.endswith("_id")
        ]
        entities.append(
            {
                "name": msg_name,
                "fields": fields,
                "correlation_keys": corr_keys,
                "states": [],
                "provenance": filename or "proto",
            }
        )

    return {
        "format": "proto",
        "package": pkg,
        "routes": routes,
        "entities": entities,
    }


def _parse_json_schema_contract(doc: dict[str, Any], *, filename: str) -> dict[str, Any]:
    title = str(doc.get("title") or Path(filename).stem or "SchemaEntity")
    props = doc.get("properties") or {}
    prop_names = [str(k) for k in props] if isinstance(props, dict) else []
    corr_keys = [
        k for k in prop_names if k in ("journey_id", "customer_id", "trace_id") or k.endswith("_id")
    ]
    states: list[str] = []
    if isinstance(props, dict) and isinstance(props.get("status"), dict):
        enum_vals = props["status"].get("enum")
        if isinstance(enum_vals, list):
            states = [str(v) for v in enum_vals]

    return {
        "format": "json_schema",
        "title": title,
        "routes": [],
        "entities": [
            {
                "name": title,
                "fields": prop_names,
                "correlation_keys": corr_keys,
                "states": states,
                "provenance": filename or "json_schema",
            }
        ],
    }


def _validate_git_mutating_trust(
    capability_id: str,
    spec: CapabilitySpec,
    trust_decision: TrustDecision | None,
) -> TrustDecision:
    if trust_decision is None or not trust_decision.authorized:
        raise TrustViolation(
            f"Mutating capability '{capability_id}' requires an authorized TrustDecision (I-06)"
        )
    if trust_decision.capability_id != capability_id or trust_decision.action_type != capability_id:
        raise TrustViolation(f"TrustDecision capability/action_type mismatch for '{capability_id}'")
    if trust_decision.required_level != spec.min_level:
        raise TrustViolation(
            f"TrustDecision required_level ({trust_decision.required_level}) != spec.min_level ({spec.min_level})"
        )
    if trust_decision.blast_radius_resources != 1:
        raise TrustViolation(
            f"Blast radius must be 1 resource (got {trust_decision.blast_radius_resources})"
        )
    if trust_decision.sa_to_impersonate != "godview-act-sa":
        raise TrustViolation(
            f"Mutating capability '{capability_id}' must impersonate 'godview-act-sa'"
        )
    if spec.min_level >= 2 and trust_decision.effective_level < 2:
        raise TrustViolation(
            f"Capability '{capability_id}' requires effective_level >= 2 (got {trust_decision.effective_level})"
        )
    if trust_decision.effective_level < spec.min_level and not trust_decision.approval_id:
        raise TrustViolation(
            f"Capability '{capability_id}' at effective_level={trust_decision.effective_level} "
            f"(< min_level={spec.min_level}) requires an approval_id"
        )
    if (
        spec.min_level == 2
        and trust_decision.effective_level == 2
        and not trust_decision.approval_id
    ):
        raise TrustViolation(f"L2 capability '{capability_id}' requires an approval_id")
    if trust_decision.effective_level < 1:
        raise TrustViolation(
            f"Capability '{capability_id}' requires effective_level >= 1 (got {trust_decision.effective_level})"
        )
    return trust_decision


class GitSenseAdapter(SenseAdapter):
    """Git & GitHub Sense Adapter (`godview/adapters/git`)."""

    family: Literal["gcp", "kubernetes", "sql", "git", "channels", "local"] = "git"
    name: str = "git-sense-adapter"

    def __init__(
        self,
        *,
        github_mcp_client: McpToolClientProtocol | None = None,
        local_repo_paths: Sequence[str | Path] | None = None,
        hint_repo_urls: Sequence[str] | None = None,
        webhook_secret: str | None = None,
        github_token: str | None = None,
        github_api_url: str = "https://api.github.com",
        env_id: str = "git-workspace",
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.github_mcp_client = github_mcp_client
        self.local_repo_paths: list[Path] = [
            Path(p) for p in (local_repo_paths or ()) if Path(p).exists()
        ]
        env_hints = parse_csv_env(os.environ.get("GODVIEW_HINT_REPO_URLS"))
        combined_hints = list(hint_repo_urls) if hint_repo_urls is not None else list(env_hints)
        self.hint_repo_urls: list[str] = [u.strip() for u in combined_hints if u.strip()]
        self.webhook_secret = (
            webhook_secret
            if webhook_secret is not None
            else os.environ.get("GODVIEW_GITHUB_WEBHOOK_SECRET", "")
        )
        self.github_token = (
            github_token if github_token is not None else os.environ.get("GITHUB_TOKEN", "")
        )
        self.github_api_url = github_api_url.rstrip("/")
        self.env_id = env_id
        self._transport = http_transport

    def senses(self) -> list[SenseCategory]:
        return [
            SenseCategory.CODE,
            SenseCategory.CONTRACTS,
            SenseCategory.CHANGE_PLANE,
            SenseCategory.HUMAN_DOCS_CHAT,
        ]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "git.list_commits": get_capability("git.list_commits"),
            "git.get_commit": get_capability("git.get_commit"),
            "git.get_file_contents": get_capability("git.get_file_contents"),
            "git.create_issue": get_capability("git.create_issue"),
            "git.propose_files": get_capability("git.propose_files"),
            "git.create_rollback_pr": get_capability("git.create_rollback_pr"),
            "git.pr_comment": get_capability("git.pr_comment"),
            "git.merge_pr": get_capability("git.merge_pr"),
            "tf.apply_dispatch": get_capability("tf.apply_dispatch"),
        }

    async def health(self) -> AdapterHealth:
        now = datetime.now(tz=UTC)
        has_mcp = self.github_mcp_client is not None
        local_count = len(self.local_repo_paths)
        hint_count = len(self.hint_repo_urls)
        has_webhook = bool(self.webhook_secret)

        if not has_mcp and local_count == 0 and hint_count == 0:
            state: Literal["available", "partial", "unavailable"] = "unavailable"
        elif has_mcp or (local_count > 0 and has_webhook):
            state = "available"
        else:
            state = "partial"

        return AdapterHealth(
            adapter_name=self.name,
            family=self.family,
            state=state,
            evidence={
                "github_mcp_connected": has_mcp,
                "local_repos_found": local_count,
                "hint_repo_urls_count": hint_count,
                "webhook_hmac_configured": has_webhook,
            },
            checked_at=now,
        )

    async def discover(self) -> DiscoveryResult:
        """Discover Git repositories, OpenAPI/proto/JSON-schema contracts, and entities."""
        repositories: list[dict[str, Any]] = []
        entities: list[DiscoveredEntityDoc] = []
        routes_discovered: list[dict[str, Any]] = []
        contract_parse_errors: list[dict[str, str]] = []

        for repo_path in self.local_repo_paths:
            head_sha = self._git_rev_parse(repo_path)
            remotes = self._git_remotes(repo_path)
            contracts, repo_errors = self._scan_local_contracts(repo_path)
            contract_parse_errors.extend(repo_errors)
            for contract in contracts:
                routes_discovered.extend(contract.get("routes", []))
                for ent in contract.get("entities", []):
                    entities.append(
                        DiscoveredEntityDoc(
                            doc_id=f"{self.env_id}__{ent['name']}",
                            name=ent["name"],
                            correlation_keys=ent.get("correlation_keys", []),
                            tables=[],
                            states=ent.get("states", []),
                            provenance=str(ent.get("provenance", repo_path.name)),
                        )
                    )
            repositories.append(
                {
                    "name": repo_path.name,
                    "locator": str(repo_path),
                    "head_sha": head_sha,
                    "remotes": remotes,
                    "source": "local_git",
                }
            )

        for hint_url in self.hint_repo_urls:
            repositories.append(
                {
                    "name": Path(hint_url.rstrip("/")).stem,
                    "locator": hint_url,
                    "head_sha": None,
                    "remotes": [hint_url],
                    "source": "hint_repo_url",
                }
            )

        return DiscoveryResult(
            services=[],
            edges=[],
            entities=entities,
            evidence={
                "repositories_count": len(repositories),
                "repositories": repositories,
                "routes_discovered": routes_discovered,
                "contract_parse_errors": contract_parse_errors,
            },
        )

    def parse_github_webhook_event(
        self,
        *,
        event_type: str,
        payload_bytes: bytes,
        signature_header: str | None,
        delivery_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        env_id: str | None = None,
    ) -> NervousSystemEvent:
        """Verify GitHub webhook HMAC-SHA256 and return a validated NervousSystemEvent (TDD §3.6)."""
        if not verify_github_webhook_hmac(payload_bytes, signature_header, self.webhook_secret):
            raise WebhookSignatureError("Invalid or missing X-Hub-Signature-256 on GitHub webhook")

        payload = json.loads(payload_bytes.decode("utf-8"))
        evt_clean = event_type.strip().lower()
        if evt_clean not in ("push", "pull_request", "workflow_run"):
            raise ValueError(f"Unsupported GitHub webhook event '{event_type}'")

        ce_type = f"godview.webhook.github.{evt_clean}"
        repo_full = (payload.get("repository") or {}).get("full_name", "unknown/repo")
        pr_obj = payload.get("pull_request") or {}
        sha = (
            payload.get("after")
            or pr_obj.get("head", {}).get("sha")
            or (payload.get("workflow_run") or {}).get("head_sha")
        )
        pr_number = payload.get("number") or pr_obj.get("number")
        action = payload.get("action")
        if evt_clean == "pull_request" and action == "closed" and pr_obj.get("merged"):
            effective_action = "merged"
        else:
            effective_action = str(action or "event")

        resolved_delivery = (delivery_id or "").strip()
        if not resolved_delivery and headers:
            for hk, hv in headers.items():
                if hk.lower() == "x-github-delivery" and hv.strip():
                    resolved_delivery = hv.strip()
                    break

        payload_digest = hashlib.sha256(payload_bytes).hexdigest()[:12]
        event_id = (
            resolved_delivery or f"{sha or 'evt'}-{evt_clean}-{effective_action}-{payload_digest}"
        )
        subject = f"pr/{pr_number}" if pr_number else str(payload.get("ref") or repo_full)

        return NervousSystemEvent(
            specversion="1.0",
            type=ce_type,
            source=f"//adapters/git/github/{repo_full}",
            id=event_id,
            time=datetime.now(tz=UTC).isoformat(),
            subject=subject,
            data={
                "repo": repo_full,
                "sha": sha,
                "pr_number": pr_number,
                "action": action,
                "merged": bool(pr_obj.get("merged", False)),
            },
            godviewenvid=env_id or self.env_id,
            godviewsynthetic=False,
        )

    @staticmethod
    def _git_rev_parse(repo_path: Path) -> str | None:
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return out.strip()
        except (subprocess.SubprocessError, OSError):
            return None

    @staticmethod
    def _git_remotes(repo_path: Path) -> list[str]:
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo_path), "remote", "-v"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            urls: set[str] = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    urls.add(parts[1])
            return sorted(urls)
        except (subprocess.SubprocessError, OSError):
            return []

    @staticmethod
    def _scan_local_contracts(
        repo_path: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if not repo_path.is_dir():
            return results, errors
        for candidate in sorted(repo_path.rglob("*")):
            if not candidate.is_file() or ".git" in candidate.parts:
                continue
            name_lower = candidate.name.lower()
            if (
                name_lower in ("openapi.json", "openapi.yaml", "openapi.yml")
                or name_lower.endswith(".proto")
                or name_lower.endswith(".schema.json")
            ):
                try:
                    raw = candidate.read_text(encoding="utf-8")
                    results.append(parse_contract_document(raw, filename=candidate.name))
                except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
                    errors.append({"file": str(candidate), "error": str(exc)})
        return results, errors

    def _resolve_local_repo_path(self, capability_id: str, args: dict[str, Any]) -> Path:
        raw_path = args.get("repo_path")
        if raw_path and str(raw_path).strip():
            return Path(str(raw_path).strip())
        if self.local_repo_paths:
            return self.local_repo_paths[0]
        raise ValueError(
            f"Capability '{capability_id}' requires 'repo_path' in args or configured local_repo_paths"
        )

    async def _call_github_mcp(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        validate_mcp_tool_binding("github-mcp", tool_name)
        if self.github_mcp_client is None:
            raise ConnectionError(f"github-mcp client is required for tool '{tool_name}'")
        return await self.github_mcp_client.call_tool(tool_name, arguments)

    async def _execute_rollback_pr_recipe(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the exact 5-call rollback PR recipe through github-mcp (#66, #72, #73, TDD §3.7)."""
        owner = str(args.get("owner", "")).strip()
        repo = str(args.get("repo", "")).strip()
        bad_sha = str(args.get("sha") or args.get("commit_sha") or "").strip()
        base_branch = str(args.get("base_branch", "main")).strip()
        reason = str(args.get("reason", f"Automated rollback of commit {bad_sha}")).strip()
        open_issue = bool(args.get("open_postmortem_issue", False))

        if not owner or not repo or not bad_sha:
            raise ValueError("git.create_rollback_pr requires 'owner', 'repo', and 'sha'")

        recipe_calls: list[str] = []

        # Call 1: get_commit
        commit_doc = await self._call_github_mcp(
            "get_commit",
            {"owner": owner, "repo": repo, "sha": bad_sha},
        )
        recipe_calls.append("get_commit")

        parents = commit_doc.get("parents") or []
        if not parents or not parents[0].get("sha"):
            raise ValueError(f"Commit '{bad_sha}' has no parent SHA to revert to")
        parent_sha = str(parents[0]["sha"])

        explicit_files = args.get("files")
        if isinstance(explicit_files, list) and explicit_files:
            file_paths = [str(f) for f in explicit_files]
        else:
            commit_files = commit_doc.get("files") or []
            file_paths = []
            for f_entry in commit_files:
                fname = f_entry.get("filename") or f_entry.get("path")
                if not fname:
                    continue
                if str(f_entry.get("status", "")).lower() == "added":
                    raise ValueError(
                        f"Cannot auto-rollback commit '{bad_sha}' via push_files: "
                        f"file '{fname}' was added in the commit and push_files cannot delete files"
                    )
                file_paths.append(str(fname))
        if not file_paths:
            raise ValueError(f"Commit '{bad_sha}' did not list any modified files")

        # Call 2: get_file_contents at parent_sha
        restored_files: list[dict[str, str]] = []
        for fpath in file_paths:
            file_doc = await self._call_github_mcp(
                "get_file_contents",
                {"owner": owner, "repo": repo, "path": fpath, "ref": parent_sha},
            )
            recipe_calls.append("get_file_contents")
            if (
                not isinstance(file_doc, dict)
                or file_doc.get("type") == "error"
                or "error" in file_doc
                or "content" not in file_doc
                or not isinstance(file_doc.get("content"), str)
            ):
                raise ValueError(
                    f"Cannot rollback '{fpath}' at parent '{parent_sha}': file was added in "
                    f"'{bad_sha}' or parent content is unavailable ({file_doc!r})"
                )
            raw_content = file_doc["content"]
            if str(file_doc.get("encoding", "")).lower() == "base64":
                decoded_content = base64.b64decode(raw_content).decode("utf-8")
            else:
                decoded_content = raw_content
            restored_files.append(
                {
                    "path": fpath,
                    "content": decoded_content,
                }
            )

        # Call 3: create_branch
        rollback_branch = str(args.get("branch") or f"godview/rollback-{bad_sha[:7]}")
        await self._call_github_mcp(
            "create_branch",
            {
                "owner": owner,
                "repo": repo,
                "branch": rollback_branch,
                "from_branch": base_branch,
            },
        )
        recipe_calls.append("create_branch")

        # Call 4: push_files
        commit_msg = f"revert: rollback {bad_sha[:7]} to {parent_sha[:7]} ({reason})"
        push_doc = await self._call_github_mcp(
            "push_files",
            {
                "owner": owner,
                "repo": repo,
                "branch": rollback_branch,
                "files": restored_files,
                "message": commit_msg,
            },
        )
        recipe_calls.append("push_files")

        # Call 5: create_pull_request
        pr_title = f"Revert {bad_sha[:7]}: {reason}"
        pr_body = (
            f"Automated rollback PR synthesized by God-View.\n\n"
            f"- **Reverted commit**: `{bad_sha}`\n"
            f"- **Restored parent**: `{parent_sha}`\n"
            f"- **Reason**: {reason}\n"
        )
        pr_doc = await self._call_github_mcp(
            "create_pull_request",
            {
                "owner": owner,
                "repo": repo,
                "title": pr_title,
                "body": pr_body,
                "head": rollback_branch,
                "base": base_branch,
            },
        )
        recipe_calls.append("create_pull_request")

        issue_doc: dict[str, Any] | None = None
        if open_issue:
            issue_doc = await self._call_github_mcp(
                "create_issue",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": f"Postmortem: Rollback of {bad_sha[:7]}",
                    "body": pr_body,
                },
            )
            recipe_calls.append("create_issue")

        if (
            not isinstance(pr_doc, dict)
            or not isinstance(pr_doc.get("number"), int)
            or int(pr_doc["number"]) <= 0
        ):
            raise RuntimeError(
                f"github-mcp create_pull_request returned invalid PR response (missing positive 'number'): {pr_doc!r}"
            )
        pr_number = int(pr_doc["number"])
        html_url = str(
            pr_doc.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_number}"
        )
        return {
            "pr_number": pr_number,
            "html_url": html_url,
            "branch": rollback_branch,
            "base_branch": base_branch,
            "reverted_sha": bad_sha,
            "parent_sha": parent_sha,
            "rollback_commit_sha": push_doc.get("commit_sha"),
            "recipe_calls": recipe_calls,
            "postmortem_issue": issue_doc,
            "target_resource": f"github.com/{owner}/{repo}/pull/{pr_number}",
            "reversal_recipe": {
                "capability": "git.create_rollback_pr",
                "args": {
                    "owner": owner,
                    "repo": repo,
                    "sha": push_doc.get("commit_sha") or bad_sha,
                    "base_branch": base_branch,
                },
            },
        }

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        spec = get_capability(capability_id)

        if capability_id == "git.list_commits":
            if self.github_mcp_client is not None and "owner" in args and "repo" in args:
                return await self._call_github_mcp(
                    "list_commits",
                    {
                        "owner": args["owner"],
                        "repo": args["repo"],
                        "sha": args.get("sha"),
                        "per_page": int(args.get("per_page", 20)),
                    },
                )
            repo_path = self._resolve_local_repo_path(capability_id, args)
            out = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repo_path),
                    "log",
                    f"-n{int(args.get('per_page', 20))}",
                    "--pretty=format:%H|%an|%aI|%s",
                ],
                text=True,
            )
            commits: list[dict[str, str]] = []
            for line in out.splitlines():
                if not line.strip():
                    continue
                sha, author, authored_at, subject = (line.split("|", 3) + ["", "", "", ""])[:4]
                commits.append(
                    {
                        "sha": sha,
                        "author": author,
                        "authored_at": authored_at,
                        "message": subject,
                    }
                )
            return commits

        if capability_id == "git.get_commit":
            if self.github_mcp_client is not None and "owner" in args and "repo" in args:
                return await self._call_github_mcp(
                    "get_commit",
                    {"owner": args["owner"], "repo": args["repo"], "sha": args["sha"]},
                )
            repo_path = self._resolve_local_repo_path(capability_id, args)
            sha = str(args.get("sha", "HEAD"))
            out = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repo_path),
                    "show",
                    "--name-only",
                    "--pretty=format:%H|%P|%s",
                    sha,
                ],
                text=True,
            )
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            header = lines[0] if lines else "||"
            commit_sha, parent_shas, msg = (header.split("|", 2) + ["", "", ""])[:3]
            files = [{"filename": f} for f in lines[1:]]
            return {
                "sha": commit_sha,
                "parents": [{"sha": p} for p in parent_shas.split() if p],
                "message": msg,
                "files": files,
            }

        if capability_id == "git.get_file_contents":
            if self.github_mcp_client is not None and "owner" in args and "repo" in args:
                return await self._call_github_mcp(
                    "get_file_contents",
                    {
                        "owner": args["owner"],
                        "repo": args["repo"],
                        "path": args["path"],
                        "ref": args.get("ref", "HEAD"),
                    },
                )
            repo_path = self._resolve_local_repo_path(capability_id, args)
            ref = str(args.get("ref", "HEAD"))
            rel_path = str(args["path"])
            content = subprocess.check_output(
                ["git", "-C", str(repo_path), "show", f"{ref}:{rel_path}"],
                text=True,
            )
            return {"path": rel_path, "ref": ref, "content": content}

        if capability_id == "git.create_issue":
            _validate_git_mutating_trust(capability_id, spec, trust_decision)
            owner = str(args["owner"])
            repo = str(args["repo"])
            issue_doc = await self._call_github_mcp(
                "create_issue",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": args["title"],
                    "body": args.get("body", ""),
                },
            )
            issue_num = issue_doc.get("number") if isinstance(issue_doc, dict) else None
            base_res = dict(issue_doc) if isinstance(issue_doc, dict) else {"result": issue_doc}
            base_res.setdefault(
                "target_resource",
                f"github.com/{owner}/{repo}/issues/{issue_num or 'new'}",
            )
            base_res.setdefault(
                "reversal_recipe",
                {
                    "description": spec.description,
                    "action": "close_issue",
                    "issue_number": issue_num,
                },
            )
            return base_res

        if capability_id == "git.propose_files":
            _validate_git_mutating_trust(capability_id, spec, trust_decision)
            owner = str(args["owner"])
            repo = str(args["repo"])
            branch = str(args["branch"])
            base_branch = str(args.get("base_branch", "main"))
            files = list(args["files"])
            message = str(args.get("message", "chore: propose vault files"))
            title = str(args.get("title", message))
            body = str(args.get("body", ""))

            await self._call_github_mcp(
                "create_branch",
                {"owner": owner, "repo": repo, "branch": branch, "from_branch": base_branch},
            )
            await self._call_github_mcp(
                "push_files",
                {
                    "owner": owner,
                    "repo": repo,
                    "branch": branch,
                    "files": files,
                    "message": message,
                },
            )
            pr_doc = await self._call_github_mcp(
                "create_pull_request",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "head": branch,
                    "base": base_branch,
                },
            )
            pr_num = pr_doc.get("number") if isinstance(pr_doc, dict) else None
            base_res = dict(pr_doc) if isinstance(pr_doc, dict) else {"result": pr_doc}
            base_res.setdefault(
                "target_resource",
                f"github.com/{owner}/{repo}/pull/{pr_num or branch}",
            )
            base_res.setdefault(
                "reversal_recipe",
                {
                    "description": spec.description,
                    "action": "close_pr_and_delete_branch",
                    "branch": branch,
                    "pr_number": pr_num,
                },
            )
            return base_res

        if capability_id == "git.create_rollback_pr":
            _validate_git_mutating_trust(capability_id, spec, trust_decision)
            return await self._execute_rollback_pr_recipe(args)

        if capability_id in ("git.pr_comment", "git.merge_pr", "tf.apply_dispatch"):
            _validate_git_mutating_trust(capability_id, spec, trust_decision)
            if not self.github_token or not self.github_token.strip():
                raise PermissionError(
                    f"Capability '{capability_id}' requires a non-empty github_token / GITHUB_TOKEN"
                )
            owner = str(args["owner"])
            repo = str(args["repo"])
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.github_token.strip()}",
            }
            kwargs: dict[str, Any] = {"base_url": self.github_api_url, "headers": headers}
            if self._transport is not None:
                kwargs["transport"] = self._transport

            async with httpx.AsyncClient(**kwargs) as client:
                if capability_id == "git.pr_comment":
                    pr_num = int(args["pr_number"])
                    resp = await client.post(
                        f"/repos/{owner}/{repo}/issues/{pr_num}/comments",
                        json={"body": args["body"]},
                    )
                    resp.raise_for_status()
                    comment_doc = resp.json()
                    res = (
                        dict(comment_doc)
                        if isinstance(comment_doc, dict)
                        else {"result": comment_doc}
                    )
                    res.setdefault("target_resource", f"github.com/{owner}/{repo}/pull/{pr_num}")
                    res.setdefault(
                        "reversal_recipe",
                        {
                            "description": spec.description,
                            "action": "delete_comment",
                            "comment_id": res.get("id"),
                        },
                    )
                    return res
                if capability_id == "git.merge_pr":
                    pr_num = int(args["pr_number"])
                    resp = await client.put(
                        f"/repos/{owner}/{repo}/pulls/{pr_num}/merge",
                        json={"merge_method": args.get("merge_method", "squash")},
                    )
                    resp.raise_for_status()
                    merge_doc = resp.json()
                    res = dict(merge_doc) if isinstance(merge_doc, dict) else {"result": merge_doc}
                    res.setdefault("target_resource", f"github.com/{owner}/{repo}/pull/{pr_num}")
                    res.setdefault(
                        "reversal_recipe",
                        {
                            "description": spec.description,
                            "capability": "git.create_rollback_pr",
                            "args": {"owner": owner, "repo": repo, "sha": res.get("sha")},
                        },
                    )
                    return res
                if capability_id == "tf.apply_dispatch":
                    workflow = str(args.get("workflow_id", "terraform-platform.yml"))
                    ref = str(args.get("ref", "main"))
                    resp = await client.post(
                        f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
                        json={
                            "ref": ref,
                            "inputs": args.get("inputs", {}),
                        },
                    )
                    resp.raise_for_status()
                    return {
                        "status": "dispatched",
                        "workflow": workflow,
                        "ref": ref,
                        "target_resource": f"github.com/{owner}/{repo}/actions/workflows/{workflow}",
                        "reversal_recipe": {
                            "description": spec.description,
                            "capability": "git.create_rollback_pr",
                            "args": {"owner": owner, "repo": repo, "base_branch": ref},
                        },
                    }

        raise NotImplementedError(
            f"Adapter '{self.name}' does not implement capability '{capability_id}'"
        )
