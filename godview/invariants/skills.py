"""SKILL.md loader, frontmatter validator against CAPABILITY_REGISTRY, and GCS vault sync (Agent Spec §8)."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import yaml
from godview_shared.contracts import ProceduralSkillDoc, SkillFrontmatter
from pydantic import ValidationError

_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?\Z", re.DOTALL)


class SkillValidationError(ValueError):
    """Raised when a SKILL.md file fails YAML parsing, schema validation, or capability allowlist checks."""


class SkillsStorageClientProtocol(Protocol):
    """Storage interface abstracting GCS object reads and writes for SKILL.md files."""

    def upload_text(self, gcs_uri: str, content: str) -> None:
        """Upload raw markdown text to the specified gs:// URI."""
        ...

    def download_text(self, gcs_uri: str) -> str:
        """Download raw markdown text from the specified gs:// URI."""
        ...


class InMemorySkillsStorageClient:
    """Hermetic in-memory fake for GCS skills bucket storage."""

    def __init__(self) -> None:
        self._objects: dict[str, str] = {}

    def upload_text(self, gcs_uri: str, content: str) -> None:
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Expected gs:// URI, got '{gcs_uri}'")
        self._objects[gcs_uri] = content

    def download_text(self, gcs_uri: str) -> str:
        if gcs_uri not in self._objects:
            raise FileNotFoundError(f"GCS object not found in fake storage: {gcs_uri}")
        return self._objects[gcs_uri]

    def list_uris(self) -> list[str]:
        return sorted(self._objects.keys())


def _parse_gs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got '{gcs_uri}'")
    rest = gcs_uri[len("gs://") :]
    bucket, _, obj_name = rest.partition("/")
    if not bucket or not obj_name:
        raise ValueError(f"gs:// URI must include bucket and object path, got '{gcs_uri}'")
    return bucket, obj_name


def _fetch_gcs_access_token() -> str:
    env_tok = os.environ.get("GODVIEW_GCP_ACCESS_TOKEN")
    if env_tok:
        return env_tok.strip()
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError("Empty access_token from GCE metadata server")
        return token


class GcsSkillsStorageClient:
    """Live Google Cloud Storage JSON API client for syncing SKILL.md files without vendor SDK imports."""

    def __init__(self, *, token_provider: Callable[[], str] | None = None) -> None:
        self._token_provider = token_provider or _fetch_gcs_access_token

    def upload_text(self, gcs_uri: str, content: str) -> None:
        bucket, obj_name = _parse_gs_uri(gcs_uri)
        token = self._token_provider()
        encoded_name = urllib.parse.quote(obj_name, safe="")
        url = (
            f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
            f"?uploadType=media&name={encoded_name}"
        )
        req = urllib.request.Request(
            url,
            data=content.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/markdown; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310
            resp.read()

    def download_text(self, gcs_uri: str) -> str:
        bucket, obj_name = _parse_gs_uri(gcs_uri)
        token = self._token_provider()
        encoded_name = urllib.parse.quote(obj_name, safe="")
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encoded_name}?alt=media"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310
            return resp.read().decode("utf-8")


def default_skills_bucket_uri(project_id: str | None = None) -> str:
    """Resolve the GCS bucket URI (`gs://<project>-god-view-agent-skills`) without hardcoded tenant literals."""
    explicit = os.environ.get("GODVIEW_SKILLS_BUCKET")
    if explicit:
        if not explicit.startswith("gs://"):
            raise ValueError(f"GODVIEW_SKILLS_BUCKET must start with 'gs://', got '{explicit}'")
        return explicit.rstrip("/")
    proj = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if proj:
        return f"gs://{proj}-god-view-agent-skills"
    env_mode = os.environ.get("GODVIEW_ENV", "hermetic")
    if env_mode == "hermetic":
        return "gs://hermetic-god-view-agent-skills"
    raise ValueError(
        "GODVIEW_SKILLS_BUCKET or GOOGLE_CLOUD_PROJECT must be set when GODVIEW_ENV != 'hermetic'"
    )


def format_skill_markdown(doc: ProceduralSkillDoc | SkillFrontmatter, body_md: str = "") -> str:
    """Serialize a ProceduralSkillDoc or SkillFrontmatter + markdown body back to SKILL.md format."""
    fm_fields = SkillFrontmatter.model_fields.keys()
    raw_dump = doc.model_dump()
    fm_dict = {k: raw_dump[k] for k in fm_fields if k in raw_dump}
    yaml_block = yaml.safe_dump(fm_dict, sort_keys=False).strip()
    body = body_md if body_md else (doc.body_md if isinstance(doc, ProceduralSkillDoc) else "")
    return f"---\n{yaml_block}\n---\n\n{body.strip()}\n"


class SkillLoader:
    """Loads, validates, and syncs SKILL.md procedural skills with GCS (`gs://<project>-god-view-agent-skills`)."""

    def __init__(
        self,
        storage_client: SkillsStorageClientProtocol | None = None,
        *,
        bucket_uri: str | None = None,
    ) -> None:
        if storage_client is not None:
            self._storage: SkillsStorageClientProtocol = storage_client
        elif os.environ.get("GODVIEW_ENV", "hermetic") == "hermetic":
            self._storage = InMemorySkillsStorageClient()
        else:
            self._storage = GcsSkillsStorageClient()
        self._bucket_uri = (bucket_uri or default_skills_bucket_uri()).rstrip("/")

    @property
    def bucket_uri(self) -> str:
        return self._bucket_uri

    @property
    def storage_client(self) -> SkillsStorageClientProtocol:
        return self._storage

    def parse_markdown(
        self,
        content: str,
        *,
        git_path: str | None = None,
        gcs_uri: str | None = None,
    ) -> ProceduralSkillDoc:
        """Parse and validate SKILL.md content against SkillFrontmatter and CAPABILITY_REGISTRY."""
        match = _FRONTMATTER_RE.match(content.strip())
        if not match:
            raise SkillValidationError(
                "SKILL.md must begin with YAML frontmatter delimited by '---'"
            )
        raw_yaml, body_md = match.group(1), (match.group(2) or "").strip()

        try:
            parsed_yaml: dict[str, Any] = yaml.safe_load(raw_yaml)
        except yaml.YAMLError as exc:
            raise SkillValidationError(f"Invalid YAML in SKILL.md frontmatter: {exc}") from exc

        if not isinstance(parsed_yaml, dict):
            raise SkillValidationError("SKILL.md frontmatter must deserialize to a mapping")

        try:
            frontmatter = SkillFrontmatter.model_validate(parsed_yaml)
        except ValidationError as exc:
            raise SkillValidationError(f"SKILL.md frontmatter failed validation: {exc}") from exc

        return ProceduralSkillDoc(
            doc_id=frontmatter.name,
            body_md=body_md,
            git_path=git_path or f"godview/agent-skills/{frontmatter.name}/SKILL.md",
            gcs_uri=gcs_uri,
            **frontmatter.model_dump(),
        )

    def load_file(self, path: Path | str) -> ProceduralSkillDoc:
        """Load and validate a SKILL.md file from disk."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"SKILL.md file not found: {file_path}")
        content = file_path.read_text(encoding="utf-8")
        return self.parse_markdown(content, git_path=str(file_path))

    def sync_to_gcs(self, doc: ProceduralSkillDoc) -> ProceduralSkillDoc:
        """Upload a validated ProceduralSkillDoc to `gs://<bucket>/skills/<slug>/SKILL.md`."""
        target_uri = f"{self._bucket_uri}/skills/{doc.name}/SKILL.md"
        markdown_payload = format_skill_markdown(doc, doc.body_md)
        self._storage.upload_text(target_uri, markdown_payload)
        return doc.model_copy(update={"gcs_uri": target_uri})

    def load_from_gcs(self, skill_slug: str) -> ProceduralSkillDoc:
        """Download and validate a SKILL.md from `gs://<bucket>/skills/<slug>/SKILL.md`."""
        target_uri = f"{self._bucket_uri}/skills/{skill_slug}/SKILL.md"
        raw_md = self._storage.download_text(target_uri)
        return self.parse_markdown(raw_md, gcs_uri=target_uri)
