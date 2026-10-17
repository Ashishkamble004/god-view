"""Helper to repackage cached uv archive-v0 entries and fresh local packages into vendor/ .whl files."""

from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

ALLOWED_PREFIXES = (
    "fastapi-",
    "starlette-",
    "uvicorn-",
    "pydantic-",
    "pydantic_core-",
    "annotated_types-",
    "typing_extensions-",
    "typing_inspection-",
    "anyio-",
    "idna-",
    "sniffio-",
    "click-",
    "h11-",
    "psycopg-",
    "psycopg_binary-",
    "opentelemetry_",
    "prometheus_client-",
    "deprecated-",
    "wrapt-",
    "importlib_metadata-",
    "zipp-",
)


def _record_entry(arcname: str, data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{arcname},sha256={b64},{len(data)}"


def build_fresh_wheel(
    dist_name: str,
    version: str,
    source_root: Path,
    package_dirs: list[str],
    vendor_dir: Path,
) -> Path:
    """Build a fresh pure-Python wheel from current source directories with valid RECORD."""
    vendor_dir.mkdir(parents=True, exist_ok=True)
    wheel_name = f"{dist_name}-{version}-py3-none-any.whl"
    wheel_path = vendor_dir / wheel_name
    if wheel_path.exists():
        wheel_path.unlink()

    dist_info = f"{dist_name}-{version}.dist-info"
    records: list[str] = []

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for pkg in package_dirs:
            pkg_path = source_root / pkg
            if not pkg_path.is_dir():
                continue
            for file_path in sorted(pkg_path.rglob("*")):
                if not file_path.is_file() or "__pycache__" in file_path.parts:
                    continue
                arcname = file_path.relative_to(source_root).as_posix()
                data = file_path.read_bytes()
                zf.writestr(arcname, data)
                records.append(_record_entry(arcname, data))

        metadata_bytes = (
            f"Metadata-Version: 2.1\nName: {dist_name.replace('_', '-')}\nVersion: {version}\n"
        ).encode()
        wheel_bytes = (
            b"Wheel-Version: 1.0\nGenerator: godview-pack-wheels\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )

        meta_arc = f"{dist_info}/METADATA"
        wheel_arc = f"{dist_info}/WHEEL"
        rec_arc = f"{dist_info}/RECORD"

        zf.writestr(meta_arc, metadata_bytes)
        records.append(_record_entry(meta_arc, metadata_bytes))
        zf.writestr(wheel_arc, wheel_bytes)
        records.append(_record_entry(wheel_arc, wheel_bytes))

        records.append(f"{rec_arc},,")
        zf.writestr(rec_arc, ("\n".join(records) + "\n").encode())

    return wheel_path


def pack_archive_wheels(archive_root: Path, vendor_dir: Path) -> int:
    if not archive_root.is_dir():
        return 0
    vendor_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in archive_root.iterdir():
        if not entry.is_dir():
            continue
        dist_infos = list(entry.glob("*.dist-info"))
        if len(dist_infos) != 1:
            continue
        dist_info = dist_infos[0]
        dist_prefix = dist_info.name[: -len(".dist-info")]
        if not dist_prefix.startswith(ALLOWED_PREFIXES):
            continue
        wheel_meta = dist_info / "WHEEL"
        tag = "py3-none-any"
        if wheel_meta.is_file():
            for line in wheel_meta.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Tag:"):
                    tag = line.split(":", 1)[1].strip()
                    break
        wheel_path = vendor_dir / f"{dist_prefix}-{tag}.whl"
        if wheel_path.exists():
            continue
        with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in entry.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(entry))
        count += 1
    return count


if __name__ == "__main__":
    archive = Path.home() / ".cache" / "uv" / "archive-v0"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vendor")
    stage_root = target.parent
    packed = pack_archive_wheels(archive, target)
    if (stage_root / "shared" / "godview_shared").is_dir():
        build_fresh_wheel(
            "godview_shared",
            "4.0.0",
            stage_root / "shared",
            ["godview_shared"],
            target,
        )
        packed += 1
    if (stage_root / "cibil_cbs").is_dir():
        build_fresh_wheel(
            "core_banking_cibil_vm",
            "4.0.0",
            stage_root,
            ["cibil_cbs", "app"],
            target,
        )
        packed += 1
    print(f"Packed {packed} wheels into {target}")
