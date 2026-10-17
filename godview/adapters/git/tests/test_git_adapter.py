"""Unit tests for the Git & GitHub Sense Adapter (`godview/adapters/git/`).

Covers TDD §1 (I-02 randomised fixtures, I-05 official MCP tools only, I-06 TrustDecision
before git.create_rollback_pr / git.propose_files / git.merge_pr), §3.6 (GitHub webhook
CloudEvents), §3.7 (`git.create_rollback_pr` exact 5-call recipe), §6.2 #3, and Agent Spec §5.1 & §6:
- Generic Git discovery (local bare/worktree repos + `GODVIEW_HINT_REPO_URLS` widening scope)
- GitHub webhook HMAC-SHA256 verification (`X-Hub-Signature-256`, both valid and invalid directions)
- Contract reading: OpenAPI (`x-godview-probe-safe`, `x-godview-admin-action`, `x-godview-reversal`),
  Protobuf `.proto` services/messages, and JSON Schema `$schema` entities
- `git.create_rollback_pr` exact 5-call recipe (`get_commit` -> `get_file_contents` at parent ->
  `create_branch` -> `push_files` -> `create_pull_request`, plus optional `create_issue`) through
  the `github-mcp` client interface agreed with `@adapters-gcp` (#72/#73)
- Availability states: `available`, `partial`, `unavailable`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from godview_shared.adapter_contract import SenseCategory
from godview_shared.capabilities import validate_mcp_tool_binding
from godview_shared.contracts import TrustDecision, TrustViolation

from godview.adapters.git import (
    GitSenseAdapter,
    WebhookSignatureError,
    parse_contract_document,
    verify_github_webhook_hmac,
)


def _rand_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_trust_decision(
    capability_id: str,
    *,
    authorized: bool = True,
    effective_level: int = 3,
    required_level: int = 3,
    approval_id: str | None = None,
    blast_radius_resources: int = 1,
    sa_to_impersonate: str = "godview-act-sa",
) -> TrustDecision:
    return TrustDecision(
        authorized=authorized,
        agent_id=f"change__{_rand_name('repo')}",
        action_type=capability_id,
        capability_id=capability_id,
        effective_level=effective_level,
        required_level=required_level,
        approval_id=approval_id,
        sa_to_impersonate=sa_to_impersonate,
        blast_radius_resources=blast_radius_resources,
        reason="unit test git trust decision",
        decided_at=datetime.now(tz=UTC).isoformat(),
    )


class _RecordingGitHubMcpClient:
    """Fake github-mcp client implementing McpToolClientProtocol (#72/#73) and recording call order."""

    server_name = "github-mcp"

    def __init__(
        self, parent_sha: str, bad_sha: str, target_file: str, parent_content: str
    ) -> None:
        self.parent_sha = parent_sha
        self.bad_sha = bad_sha
        self.target_file = target_file
        self.parent_content = parent_content
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        validate_mcp_tool_binding(self.server_name, tool_name)
        self.calls.append((tool_name, dict(arguments)))

        if tool_name == "get_commit":
            return {
                "sha": arguments["sha"],
                "parents": [{"sha": self.parent_sha}],
                "files": [{"filename": self.target_file, "status": "modified"}],
                "commit": {"message": "feat: update threshold"},
            }
        if tool_name == "get_file_contents":
            assert arguments["ref"] == self.parent_sha
            return {
                "path": arguments["path"],
                "ref": arguments["ref"],
                "content": self.parent_content,
                "sha": "blob-parent-123",
            }
        if tool_name == "create_branch":
            return {
                "ref": f"refs/heads/{arguments['branch']}",
                "sha": self.bad_sha,
            }
        if tool_name == "push_files":
            return {
                "commit_sha": f"revert-{uuid.uuid4().hex[:8]}",
                "branch": arguments["branch"],
            }
        if tool_name == "create_pull_request":
            return {
                "number": 43,
                "html_url": f"https://github.com/{arguments['owner']}/{arguments['repo']}/pull/43",
                "head": arguments["head"],
                "base": arguments["base"],
            }
        if tool_name == "create_issue":
            return {
                "number": 44,
                "html_url": f"https://github.com/{arguments['owner']}/{arguments['repo']}/issues/44",
            }
        if tool_name == "list_commits":
            return [{"sha": self.bad_sha}, {"sha": self.parent_sha}]
        raise KeyError(tool_name)


@pytest.mark.unit
def test_github_webhook_hmac_verification_both_directions() -> None:
    """Verify HMAC-SHA256 signature validation passes on authentic payloads and fails on tampering."""
    secret = f"whsec-{uuid.uuid4().hex}"
    repo_slug = f"org-{uuid.uuid4().hex[:4]}/{_rand_name('repo')}"
    payload = json.dumps(
        {"ref": "refs/heads/main", "after": "a1b2c3d4", "repository": {"full_name": repo_slug}}
    ).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    valid_sig = f"sha256={digest}"

    # Direction 1: Valid signature passes
    assert verify_github_webhook_hmac(payload, valid_sig, secret) is True

    # Direction 2: Tampered payload, wrong secret, missing prefix, or empty header fails
    assert verify_github_webhook_hmac(payload + b" ", valid_sig, secret) is False
    assert verify_github_webhook_hmac(payload, valid_sig, "wrong-secret") is False
    assert verify_github_webhook_hmac(payload, digest, secret) is False
    assert verify_github_webhook_hmac(payload, None, secret) is False

    adapter = GitSenseAdapter(webhook_secret=secret)
    event = adapter.parse_github_webhook_event(
        event_type="push",
        payload_bytes=payload,
        signature_header=valid_sig,
        headers={"X-GitHub-Delivery": "delivery-guid-001"},
        env_id="env-test",
    )
    assert event.type == "godview.webhook.github.push"
    assert event.id == "delivery-guid-001"
    assert event.subject == "refs/heads/main"
    assert event.data["repo"] == repo_slug
    assert event.data["sha"] == "a1b2c3d4"

    # PR opened vs PR merged on the same head SHA produce distinct CloudEvent IDs even without X-GitHub-Delivery (Finding 8)
    pr_open_bytes = json.dumps(
        {
            "action": "opened",
            "number": 7,
            "pull_request": {"number": 7, "head": {"sha": "fff"}},
            "repository": {"full_name": repo_slug},
        }
    ).encode("utf-8")
    pr_merged_bytes = json.dumps(
        {
            "action": "closed",
            "number": 7,
            "pull_request": {"number": 7, "merged": True, "head": {"sha": "fff"}},
            "repository": {"full_name": repo_slug},
        }
    ).encode("utf-8")
    sig_open = (
        "sha256=" + hmac.new(secret.encode("utf-8"), pr_open_bytes, hashlib.sha256).hexdigest()
    )
    sig_merged = (
        "sha256=" + hmac.new(secret.encode("utf-8"), pr_merged_bytes, hashlib.sha256).hexdigest()
    )
    ev_open = adapter.parse_github_webhook_event(
        event_type="pull_request", payload_bytes=pr_open_bytes, signature_header=sig_open
    )
    ev_merged = adapter.parse_github_webhook_event(
        event_type="pull_request", payload_bytes=pr_merged_bytes, signature_header=sig_merged
    )
    assert ev_open.id != ev_merged.id
    assert ev_open.subject == "pr/7"

    with pytest.raises(WebhookSignatureError):
        adapter.parse_github_webhook_event(
            event_type="push",
            payload_bytes=payload,
            signature_header="sha256=deadbeef",
            env_id="env-test",
        )


@pytest.mark.unit
def test_contract_reading_openapi_proto_and_json_schema() -> None:
    """Extract routes, x-godview-probe-safe, x-godview-admin-action, and entities from OpenAPI, proto, JSON-Schema."""
    openapi_doc = {
        "openapi": "3.1.0",
        "info": {"title": _rand_name("svc-api"), "version": "1.0.0"},
        "paths": {
            "/v1/items": {
                "get": {
                    "operationId": "listItems",
                    "x-godview-probe-safe": True,
                },
                "post": {
                    "operationId": "createItem",
                    "x-godview-probe-safe": False,
                },
            },
            "/admin/v1/settlements/{item_id}:replay": {
                "post": {
                    "operationId": "replaySettlement",
                    "x-godview-admin-action": "biz.replay_settlement",
                    "x-godview-reversal": {"capability": "biz.compensate_journey"},
                }
            },
        },
        "components": {
            "schemas": {
                "OrderRecord": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "journey_id": {"type": "string"},
                        "customer_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["PENDING", "APPROVED", "SETTLED"]},
                    },
                }
            }
        },
    }
    parsed_oa = parse_contract_document(json.dumps(openapi_doc), filename="openapi.json")
    assert parsed_oa["format"] == "openapi"
    routes_by_path = {(r["method"], r["path"]): r for r in parsed_oa["routes"]}
    assert routes_by_path[("GET", "/v1/items")]["probe_safe"] is True
    assert routes_by_path[("POST", "/v1/items")]["probe_safe"] is False
    admin_route = routes_by_path[("POST", "/admin/v1/settlements/{item_id}:replay")]
    assert admin_route["admin"] is True
    assert admin_route["admin_capability"] == "biz.replay_settlement"
    assert admin_route["reversal"]["capability"] == "biz.compensate_journey"
    assert any(
        e["name"] == "OrderRecord" and "journey_id" in e["correlation_keys"]
        for e in parsed_oa["entities"]
    )

    proto_text = """
    syntax = "proto3";
    package finance.v1;
    service DecisionService {
      rpc EvaluateDecision (EvaluateRequest) returns (EvaluateResponse);
    }
    message EvaluateRequest {
      string journey_id = 1;
      string customer_id = 2;
      double amount = 3;
    }
    """
    parsed_proto = parse_contract_document(proto_text, filename="decision.proto")
    assert parsed_proto["format"] == "proto"
    assert any(
        r["path"] == "/finance.v1.DecisionService/EvaluateDecision" for r in parsed_proto["routes"]
    )
    assert any(
        e["name"] == "EvaluateRequest" and "journey_id" in e["correlation_keys"]
        for e in parsed_proto["entities"]
    )

    json_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AccountEntity",
        "type": "object",
        "properties": {
            "account_id": {"type": "string"},
            "journey_id": {"type": "string"},
            "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
        },
    }
    parsed_schema = parse_contract_document(json.dumps(json_schema), filename="account.schema.json")
    assert parsed_schema["format"] == "json_schema"
    assert parsed_schema["entities"][0]["name"] == "AccountEntity"
    assert parsed_schema["entities"][0]["states"] == ["OPEN", "CLOSED"]


@pytest.mark.unit
async def test_git_create_rollback_pr_exact_five_call_recipe() -> None:
    """Verify git.create_rollback_pr executes the exact 5-call sequence via github-mcp and enforces L3 trust."""
    owner = _rand_name("org")
    repo = _rand_name("repo")
    bad_sha = "9f1c2ab0001"
    parent_sha = "8e0b19a0000"
    target_file = "services/evaluator/app/rules_engine.py"
    parent_content = "MAX_RATIO_PCT = 60.0\nMIN_SCORE = 680\n"

    mcp_client = _RecordingGitHubMcpClient(
        parent_sha=parent_sha,
        bad_sha=bad_sha,
        target_file=target_file,
        parent_content=parent_content,
    )
    adapter = GitSenseAdapter(github_mcp_client=mcp_client)

    assert SenseCategory.CODE in adapter.senses()
    assert SenseCategory.CONTRACTS in adapter.senses()
    assert SenseCategory.CHANGE_PLANE in adapter.senses()

    # Missing TrustDecision raises TrustViolation (I-06)
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "git.create_rollback_pr",
            {"owner": owner, "repo": repo, "sha": bad_sha},
            trust_decision=None,
        )

    # L2 TrustDecision (< L3 required) raises TrustViolation
    low_td = _make_trust_decision("git.create_rollback_pr", effective_level=2)
    with pytest.raises(TrustViolation):
        await adapter.execute_capability(
            "git.create_rollback_pr",
            {"owner": owner, "repo": repo, "sha": bad_sha},
            trust_decision=low_td,
        )

    # Valid L3 TrustDecision executes the exact 5-call recipe
    valid_td = _make_trust_decision("git.create_rollback_pr", effective_level=3)
    result = await adapter.execute_capability(
        "git.create_rollback_pr",
        {
            "owner": owner,
            "repo": repo,
            "sha": bad_sha,
            "base_branch": "main",
            "reason": "Revert ratio threshold regression",
        },
        trust_decision=valid_td,
    )

    tool_sequence = [call[0] for call in mcp_client.calls]
    assert tool_sequence == [
        "get_commit",
        "get_file_contents",
        "create_branch",
        "push_files",
        "create_pull_request",
    ]
    assert result["pr_number"] == 43
    assert result["reverted_sha"] == bad_sha
    assert result["parent_sha"] == parent_sha
    assert result["recipe_calls"] == [
        "get_commit",
        "get_file_contents",
        "create_branch",
        "push_files",
        "create_pull_request",
    ]
    assert result["reversal_recipe"]["capability"] == "git.create_rollback_pr"

    # Finding 3: Added file or missing parent content raises ValueError; missing PR number raises RuntimeError; base64 content is decoded
    import base64

    class _EdgeCaseGitHubMcpClient:
        server_name = "github-mcp"

        def __init__(self, mode: str) -> None:
            self.mode = mode
            self.pushed_files: list[dict[str, str]] = []

        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            validate_mcp_tool_binding(self.server_name, tool_name)
            if tool_name == "get_commit":
                status = "added" if self.mode == "added_status" else "modified"
                return {
                    "sha": bad_sha,
                    "parents": [{"sha": parent_sha}],
                    "files": [{"filename": target_file, "status": status}],
                }
            if tool_name == "get_file_contents":
                if self.mode == "missing_parent_file":
                    return {"type": "error", "message": "Not Found"}
                return {
                    "path": target_file,
                    "content": base64.b64encode(parent_content.encode("utf-8")).decode("ascii"),
                    "encoding": "base64",
                }
            if tool_name == "push_files":
                self.pushed_files = list(arguments["files"])
                return {"commit_sha": "rev-b64"}
            if tool_name == "create_pull_request":
                if self.mode == "missing_pr_number":
                    return {}
                return {"number": 99, "html_url": f"https://github.com/{owner}/{repo}/pull/99"}
            return {}

    for err_mode in ("added_status", "missing_parent_file"):
        edge_adapter = GitSenseAdapter(github_mcp_client=_EdgeCaseGitHubMcpClient(err_mode))
        with pytest.raises(ValueError, match="Cannot"):
            await edge_adapter.execute_capability(
                "git.create_rollback_pr",
                {"owner": owner, "repo": repo, "sha": bad_sha},
                trust_decision=valid_td,
            )

    no_num_adapter = GitSenseAdapter(
        github_mcp_client=_EdgeCaseGitHubMcpClient("missing_pr_number")
    )
    with pytest.raises(RuntimeError, match="number"):
        await no_num_adapter.execute_capability(
            "git.create_rollback_pr",
            {"owner": owner, "repo": repo, "sha": bad_sha},
            trust_decision=valid_td,
        )

    b64_client = _EdgeCaseGitHubMcpClient("base64_ok")
    b64_adapter = GitSenseAdapter(github_mcp_client=b64_client)
    b64_res = await b64_adapter.execute_capability(
        "git.create_rollback_pr",
        {"owner": owner, "repo": repo, "sha": bad_sha},
        trust_decision=valid_td,
    )
    assert b64_res["pr_number"] == 99
    assert b64_client.pushed_files[0]["content"] == parent_content


@pytest.mark.unit
async def test_generic_local_git_discovery_and_health_states(tmp_path: Path) -> None:
    """Verify generic local Git repository discovery, commit reading, and health states."""
    repo_dir = tmp_path / _rand_name("local-repo")
    repo_dir.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo_dir)], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "tester"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "tester@example.local"], check=True
    )

    contract_file = repo_dir / "openapi.json"
    contract_file.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "local-svc", "version": "1.0"},
                "paths": {"/v1/ping": {"get": {"x-godview-probe-safe": True}}},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "openapi.json"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )

    hint_url = f"https://github.com/example/{_rand_name('hint-repo')}.git"
    adapter = GitSenseAdapter(
        local_repo_paths=[repo_dir],
        hint_repo_urls=[hint_url],
        webhook_secret="secret-123",
    )
    h_avail = await adapter.health()
    assert h_avail.state in ("available", "partial")
    assert h_avail.evidence["local_repos_found"] == 1

    disc = await adapter.discover()
    assert disc.evidence["repositories_count"] == 2

    commits = await adapter.execute_capability("git.list_commits", {"repo_path": str(repo_dir)})
    assert len(commits) == 1
    assert "initial commit" in commits[0]["message"]

    file_res = await adapter.execute_capability(
        "git.get_file_contents",
        {"repo_path": str(repo_dir), "path": "openapi.json", "ref": "HEAD"},
    )
    assert "x-godview-probe-safe" in file_res["content"]

    unavail_adapter = GitSenseAdapter(
        local_repo_paths=[], github_mcp_client=None, hint_repo_urls=[]
    )
    h_unavail = await unavail_adapter.health()
    assert h_unavail.state == "unavailable"

    # Finding 13: git read capabilities refuse when no repo is configured (never fall back to ".")
    for read_cap, cap_args in (
        ("git.list_commits", {}),
        ("git.get_commit", {"sha": "HEAD"}),
        ("git.get_file_contents", {"path": "openapi.json"}),
    ):
        with pytest.raises(ValueError, match="requires 'repo_path'"):
            await unavail_adapter.execute_capability(read_cap, cap_args)

    # Malformed contract files are recorded in evidence["contract_parse_errors"]
    bad_contract = repo_dir / "broken.schema.json"
    bad_contract.write_text("{not valid json", encoding="utf-8")
    disc_with_err = await adapter.discover()
    assert len(disc_with_err.evidence["contract_parse_errors"]) == 1


@pytest.mark.unit
async def test_git_propose_files_pr_comment_merge_and_tf_apply_dispatch_trust() -> None:
    """Test git.propose_files, git.create_issue, git.pr_comment, git.merge_pr, and tf.apply_dispatch (L2 approval_id)."""
    import httpx

    owner = _rand_name("org")
    repo = _rand_name("repo")
    mcp_client = _RecordingGitHubMcpClient(
        parent_sha="111",
        bad_sha="222",
        target_file="a.py",
        parent_content="x = 1\n",
    )

    rest_calls: list[tuple[str, str]] = []

    def rest_handler(request: httpx.Request) -> httpx.Response:
        rest_calls.append((request.method, request.url.path))
        if request.url.path.endswith("/dispatches"):
            return httpx.Response(204)
        return httpx.Response(200, json={"status": "ok", "path": request.url.path})

    adapter = GitSenseAdapter(
        github_mcp_client=mcp_client,
        github_token="ghp_test_token",
        http_transport=httpx.MockTransport(rest_handler),
    )

    # L1 git.propose_files (3-call MCP recipe: create_branch -> push_files -> create_pull_request)
    td_propose = _make_trust_decision("git.propose_files", effective_level=1, required_level=1)
    pr_res = await adapter.execute_capability(
        "git.propose_files",
        {
            "owner": owner,
            "repo": repo,
            "branch": "godview/invariant-1",
            "files": [{"path": "godview/invariants/inv.md", "content": "---\nname: inv\n---\n"}],
            "message": "chore: add invariant",
        },
        trust_decision=td_propose,
    )
    assert pr_res["number"] == 43
    assert "target_resource" in pr_res
    assert "reversal_recipe" in pr_res

    # L1 git.create_issue includes target_resource and reversal_recipe (Finding 11)
    td_issue = _make_trust_decision("git.create_issue", effective_level=1, required_level=1)
    issue_res = await adapter.execute_capability(
        "git.create_issue",
        {"owner": owner, "repo": repo, "title": "Incident postmortem"},
        trust_decision=td_issue,
    )
    assert issue_res["number"] == 44
    assert "target_resource" in issue_res
    assert "reversal_recipe" in issue_res

    # L2 tf.apply_dispatch without approval_id at effective_level=2 must raise TrustViolation
    td_dispatch_no_apr = _make_trust_decision(
        "tf.apply_dispatch",
        effective_level=2,
        required_level=2,
        approval_id=None,
    )
    with pytest.raises(TrustViolation, match="approval_id"):
        await adapter.execute_capability(
            "tf.apply_dispatch",
            {"owner": owner, "repo": repo, "inputs": {"target": "module.workloads"}},
            trust_decision=td_dispatch_no_apr,
        )

    # L2 tf.apply_dispatch with approval_id succeeds and returns target_resource + reversal_recipe
    td_dispatch_ok = _make_trust_decision(
        "tf.apply_dispatch",
        effective_level=2,
        required_level=2,
        approval_id="apr-inc-1",
    )
    disp_res = await adapter.execute_capability(
        "tf.apply_dispatch",
        {"owner": owner, "repo": repo, "inputs": {"target": "module.workloads"}},
        trust_decision=td_dispatch_ok,
    )
    assert disp_res["status"] == "dispatched"
    assert "target_resource" in disp_res
    assert "reversal_recipe" in disp_res

    # L1 git.pr_comment and L3 git.merge_pr include target_resource + reversal_recipe
    td_comment = _make_trust_decision("git.pr_comment", effective_level=1, required_level=1)
    comment_res = await adapter.execute_capability(
        "git.pr_comment",
        {"owner": owner, "repo": repo, "pr_number": 42, "body": "Risk review: low risk"},
        trust_decision=td_comment,
    )
    assert "target_resource" in comment_res
    assert "reversal_recipe" in comment_res

    td_merge = _make_trust_decision("git.merge_pr", effective_level=3, required_level=3)
    merge_res = await adapter.execute_capability(
        "git.merge_pr",
        {"owner": owner, "repo": repo, "pr_number": 43},
        trust_decision=td_merge,
    )
    assert "target_resource" in merge_res
    assert "reversal_recipe" in merge_res
    assert len(rest_calls) == 3

    # Empty github_token raises PermissionError instead of sending empty Authorization header (Finding 11)
    no_token_adapter = GitSenseAdapter(
        github_mcp_client=mcp_client,
        github_token="",
        http_transport=httpx.MockTransport(rest_handler),
    )
    with pytest.raises(PermissionError, match="github_token"):
        await no_token_adapter.execute_capability(
            "git.pr_comment",
            {"owner": owner, "repo": repo, "pr_number": 42, "body": "comment"},
            trust_decision=td_comment,
        )
