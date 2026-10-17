# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CloudSyncedLibrary: Harness² Library backed by local disk + GCS + Firestore."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from godview_shared.fakes import InMemoryFirestore

from godview.harness.core.ledger import Ledger
from godview.harness.core.library import Library, _slug
from godview.harness.core.proposer import Proposer
from godview.harness.core.spec import Candidate, SurfaceSpec
from godview.harness.substrate import GODVIEW_SURFACE
from godview.invariants.skills import (
    GcsSkillsStorageClient,
    InMemorySkillsStorageClient,
    SkillsStorageClientProtocol,
)


class CloudSyncedLibrary(Library):
    """Domain-partitioned Harness Library that persists locally and syncs to GCS and Firestore.

    Storage layout:
      - Local disk: `<root>/<surface>/<domain>/INDEX.md`, `<domain>/EXPERIENCE.md`,
        and `<domain>/harnesses/<task_id>/{EXPERIENCE.md,harness/...}`
      - GCS bucket (`gs://<bucket>/harness2/<domain>/...`):
        mirrors `INDEX.md`, `EXPERIENCE.md`, and every file under `harnesses/<task_id>/`
      - Firestore (`god-view-memory-db`):
        * `skills/{domain}__{task_id}` or `procedural_skills/{domain}__{task_id}`
        * `harness_trees/{domain}__{task_id}`
        * `court_agents/{domain}/living_model/{task_id}`
    """

    def __init__(
        self,
        root: Path,
        surface: SurfaceSpec = GODVIEW_SURFACE,
        *,
        storage_client: SkillsStorageClientProtocol | None = None,
        firestore_client: Any | None = None,
        auth: Any | None = None,
        token_provider: Callable[[], str] | None = None,
        bucket_name: str | None = None,
        gcs_prefix: str = "harness2",
    ) -> None:
        super().__init__(root=root, surface=surface)
        self.gcs_prefix = gcs_prefix.strip("/")
        resolved_bucket = (
            bucket_name
            or os.environ.get("AGENT_SKILLS_BUCKET")
            or os.environ.get("GODVIEW_SKILLS_BUCKET", "")
        )
        if not resolved_bucket:
            project_id = (
                os.environ.get("GCP_PROJECT_ID", "").strip()
                or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
            )
            resolved_bucket = (
                f"{project_id}-god-view-agent-skills" if project_id else "god-view-agent-skills"
            )
        self.bucket_name = resolved_bucket

        resolved_token_provider = token_provider
        if resolved_token_provider is None and auth is not None:
            if hasattr(auth, "access_token"):
                resolved_token_provider = auth.access_token

        env_mode = os.environ.get("GODVIEW_ENV", "hermetic").lower()
        if storage_client is not None:
            self.storage_client: SkillsStorageClientProtocol = storage_client
        elif env_mode != "hermetic":
            try:
                self.storage_client = GcsSkillsStorageClient(
                    token_provider=resolved_token_provider,
                )
            except Exception:
                self.storage_client = InMemorySkillsStorageClient()
        else:
            self.storage_client = InMemorySkillsStorageClient()

        self.firestore_client = firestore_client or InMemoryFirestore()
        self.synced_objects: list[str] = []

    def adopt(
        self,
        *,
        domain: str,
        task_id: str,
        task_md: str,
        winner: Candidate,
        all_candidates: list[Candidate],
        proposer: Proposer | None,
        ledger: Ledger,
        work_dir: Path,
    ) -> Path:
        dest = super().adopt(
            domain=domain,
            task_id=task_id,
            task_md=task_md,
            winner=winner,
            all_candidates=all_candidates,
            proposer=proposer,
            ledger=ledger,
            work_dir=work_dir,
        )
        self._sync_domain_to_cloud(
            domain=domain,
            task_id=task_id,
            winner=winner,
            dest=dest,
        )
        return dest

    def _sync_domain_to_cloud(
        self,
        *,
        domain: str,
        task_id: str,
        winner: Candidate,
        dest: Path,
    ) -> None:
        ddir = self.domain_dir(domain)
        dom_slug = _slug(domain)
        task_slug = _slug(task_id)

        # 1. Upload domain INDEX.md and EXPERIENCE.md to GCS
        for top_name in ("INDEX.md", "EXPERIENCE.md"):
            top_file = ddir / top_name
            if top_file.is_file():
                obj_key = f"gs://{self.bucket_name}/{self.gcs_prefix}/{dom_slug}/{top_name}"
                self.storage_client.upload_text(obj_key, top_file.read_text(encoding="utf-8"))
                self.synced_objects.append(obj_key)

        # 2. Upload all files in `dest` (`EXPERIENCE.md` + `harness/**/*`) to GCS
        for file_path in sorted(dest.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(ddir).as_posix()
            obj_key = f"gs://{self.bucket_name}/{self.gcs_prefix}/{dom_slug}/{rel}"
            self.storage_client.upload_text(obj_key, file_path.read_text(encoding="utf-8"))
            self.synced_objects.append(obj_key)

        # 3. Record skills, harness_trees & living_model metadata in Firestore
        exp_text = (
            (dest / "EXPERIENCE.md").read_text(encoding="utf-8")
            if (dest / "EXPERIENCE.md").is_file()
            else ""
        )
        idx_text = self.read_index(domain)
        skill_paths = [
            p.relative_to(dest / "harness").as_posix()
            for p in sorted((dest / "harness" / "skills").glob("*/SKILL.md"))
        ]
        script_paths = [
            p.relative_to(dest / "harness").as_posix()
            for p in sorted((dest / "harness" / "scripts").glob("*.py"))
        ]
        record = {
            "domain": dom_slug,
            "task_id": task_id,
            "winner_tag": winner.tag,
            "score": winner.score,
            "passed": winner.passed,
            "notes": winner.notes,
            "skills": skill_paths,
            "scripts": script_paths,
            "experience_markdown": exp_text,
            "index_markdown": idx_text,
            "gcs_uri": f"gs://{self.bucket_name}/{self.gcs_prefix}/{dom_slug}/harnesses/{task_slug}/",
            "updated_at_epoch": time.time(),
        }
        if hasattr(self.firestore_client, "set"):
            self.firestore_client.set("skills", f"{dom_slug}__{task_slug}", record)
            self.firestore_client.set("harness_trees", f"{dom_slug}__{task_slug}", record)
            if hasattr(self.firestore_client, "set_subdoc"):
                self.firestore_client.set_subdoc(
                    "court_agents",
                    dom_slug,
                    "living_model",
                    task_slug,
                    record,
                )
        elif hasattr(self.firestore_client, "collection"):
            self.firestore_client.collection("skills").document(
                f"{dom_slug}__{task_slug}"
            ).set(record)
            self.firestore_client.collection("harness_trees").document(
                f"{dom_slug}__{task_slug}"
            ).set(record)
            self.firestore_client.collection("court_agents").document(dom_slug).collection(
                "living_model"
            ).document(task_slug).set(record)
