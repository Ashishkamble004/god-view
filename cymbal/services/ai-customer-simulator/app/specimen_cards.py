"""Specimen PAN and Aadhaar-style card generation, PNG watermarking, and GCS upload.

Enforces BR-01, TDD I-09, and TDD §6.2 #9:
- Model chain: gemini-nano-banana-2.1 (global) -> gemini-3-pro-image (global) -> gemini-2.5-flash-image (us-central1).
- Watermark: 'SPECIMEN · NOT A GOVERNMENT DOCUMENT' embedded in visual prompt and PNG tEXt metadata chunks.
- Zero official emblems or agency names (UIDAI, Income Tax Department, Ashoka, Satyameva Jayate, Government of India).
- Uploaded to gs://general-ak-god-view-kyc-vault/<customer_id>/{pan,aadhaar}.png via SpecimenStorage interface.
"""

from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from godview_shared.config import MODEL_TABLE, validate_model_location

from app.config import AdcTokenProvider
from app.personas import VertexModelTransport

PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
SPECIMEN_WATERMARK: str = "SPECIMEN · NOT A GOVERNMENT DOCUMENT"

FORBIDDEN_EMBLEM_TERMS: tuple[str, ...] = (
    "uidai",
    "income tax department",
    "government of india",
    "satyameva jayate",
    "ashoka",
    "national emblem",
    "lion capital",
)


class SpecimenCardError(RuntimeError):
    """Raised when specimen card generation or validation fails across all fallback models."""


@dataclass(frozen=True)
class GeneratedSpecimenPair:
    """Result of generating and uploading specimen PAN + Aadhaar PNGs for a customer."""

    pan_gcs_uri: str
    aadhaar_gcs_uri: str
    pan_image_model: str
    aadhaar_image_model: str
    pan_bytes: bytes
    aadhaar_bytes: bytes


class SpecimenStorage(Protocol):
    """Storage interface for uploading specimen KYC PNGs to GCS (or hermetic memory)."""

    async def upload_png(
        self,
        *,
        customer_id: str,
        doc_type: Literal["pan", "aadhaar"],
        data: bytes,
        metadata: dict[str, str],
    ) -> str: ...


class InMemorySpecimenStorage:
    """Hermetic in-memory storage fake for tests."""

    def __init__(self, bucket_name: str = "general-ak-god-view-kyc-vault") -> None:
        self.bucket_name = bucket_name
        self.objects: dict[str, dict[str, Any]] = {}

    async def upload_png(
        self,
        *,
        customer_id: str,
        doc_type: Literal["pan", "aadhaar"],
        data: bytes,
        metadata: dict[str, str],
    ) -> str:
        uri = f"gs://{self.bucket_name}/{customer_id}/{doc_type}.png"
        self.objects[uri] = {
            "data": data,
            "content_type": "image/png",
            "metadata": dict(metadata),
        }
        return uri


class GcsHttpSpecimenStorage:
    """Uploads specimen PNGs to Google Cloud Storage via JSON upload API with google-auth ADC."""

    def __init__(
        self,
        bucket_name: str = "general-ak-god-view-kyc-vault",
        timeout_s: float = 30.0,
        token_provider: AdcTokenProvider | None = None,
    ) -> None:
        self.bucket_name = bucket_name
        self.timeout_s = timeout_s
        self.token_provider = token_provider or AdcTokenProvider()

    def _get_access_token(self) -> str:
        return self.token_provider.get_access_token()

    async def upload_png(
        self,
        *,
        customer_id: str,
        doc_type: Literal["pan", "aadhaar"],
        data: bytes,
        metadata: dict[str, str],
    ) -> str:
        object_name = f"{customer_id}/{doc_type}.png"
        encoded_name = quote(object_name, safe="")
        url = (
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket_name}/o"
            f"?uploadType=media&name={encoded_name}"
        )
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/png",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, headers=headers, content=data)
            if resp.status_code not in (200, 201):
                raise SpecimenCardError(
                    f"GCS upload to gs://{self.bucket_name}/{object_name} failed "
                    f"(HTTP {resp.status_code}): {resp.text[:300]}"
                )
        return f"gs://{self.bucket_name}/{object_name}"


def _png_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    return struct.pack(">I", len(chunk_data)) + chunk_type + chunk_data + struct.pack(">I", crc)


def _text_chunk(keyword: str, text_value: str) -> bytes:
    payload = keyword.encode("latin-1") + b"\x00" + text_value.encode("utf-8")
    return _png_chunk(b"tEXt", payload)


def render_deterministic_specimen_png(
    *,
    doc_type: Literal["pan", "aadhaar"],
    customer_id: str,
    full_name: str,
    dob: str,
    id_number: str,
    model_id: str,
) -> bytes:
    """Render a valid watermarked PNG with embedded tEXt specimen metadata."""
    width, height = 64, 40
    # 8-bit RGB, non-interlaced
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    meta_chunks = b"".join(
        [
            _text_chunk("Watermark", SPECIMEN_WATERMARK),
            _text_chunk("Specimen-Disclaimer", SPECIMEN_WATERMARK),
            _text_chunk("Card-Type", f"{doc_type.upper()}_SPECIMEN"),
            _text_chunk("Customer-ID", customer_id),
            _text_chunk("Holder-Name", full_name),
            _text_chunk("DOB", dob),
            _text_chunk("Synthetic-ID", id_number),
            _text_chunk("Emblems", "NONE"),
            _text_chunk("Generator-Model", model_id),
            _text_chunk("Synthetic", "true"),
        ]
    )
    # Build RGB scanlines with a diagonal watermark stripe across the card
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            if abs((x * height) - (y * width)) < (width * 3):
                # Diagonal watermark band (slate ink)
                row.extend((15, 23, 42))
            else:
                # Luminous white / slate-50 card background
                row.extend((248, 250, 252))
        rows.append(bytes(row))
    compressed = zlib.compress(b"".join(rows))
    idat = _png_chunk(b"IDAT", compressed)
    iend = _png_chunk(b"IEND", b"")
    return PNG_SIGNATURE + ihdr + meta_chunks + idat + iend


def inject_png_specimen_metadata(
    png_bytes: bytes,
    *,
    doc_type: Literal["pan", "aadhaar"],
    customer_id: str,
    full_name: str,
    dob: str,
    id_number: str,
    model_id: str,
) -> bytes:
    """Inject PNG tEXt watermark and specimen metadata chunks before IEND."""
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise SpecimenCardError("Invalid PNG magic bytes in generated specimen image")

    meta_chunks = b"".join(
        [
            _text_chunk("Watermark", SPECIMEN_WATERMARK),
            _text_chunk("Specimen-Disclaimer", SPECIMEN_WATERMARK),
            _text_chunk("Card-Type", f"{doc_type.upper()}_SPECIMEN"),
            _text_chunk("Customer-ID", customer_id),
            _text_chunk("Holder-Name", full_name),
            _text_chunk("DOB", dob),
            _text_chunk("Synthetic-ID", id_number),
            _text_chunk("Emblems", "NONE"),
            _text_chunk("Generator-Model", model_id),
            _text_chunk("Synthetic", "true"),
        ]
    )
    iend_marker = _png_chunk(b"IEND", b"")
    if png_bytes.endswith(iend_marker):
        return png_bytes[: -len(iend_marker)] + meta_chunks + iend_marker
    return png_bytes + meta_chunks + iend_marker


def verify_specimen_png(png_bytes: bytes) -> dict[str, Any]:
    """Metadata check: verify PNG magic header, SPECIMEN_WATERMARK tEXt chunk, and absence of forbidden emblem strings in metadata."""
    valid_png = png_bytes.startswith(PNG_SIGNATURE) and b"IEND" in png_bytes
    watermark_bytes = SPECIMEN_WATERMARK.encode("utf-8")
    has_watermark = watermark_bytes in png_bytes
    lower_text = png_bytes.decode("latin-1", errors="ignore").lower()
    has_forbidden_emblem = any(term in lower_text for term in FORBIDDEN_EMBLEM_TERMS)
    return {
        "valid_png": valid_png,
        "has_watermark": has_watermark,
        "watermark": SPECIMEN_WATERMARK if has_watermark else None,
        "has_forbidden_emblem": has_forbidden_emblem,
    }


def build_specimen_prompt(
    *,
    doc_type: Literal["pan", "aadhaar"],
    full_name: str,
    dob: str,
    id_number: str,
) -> str:
    """Construct a safe specimen test card prompt with mandatory watermark and zero emblems."""
    card_label = (
        "SYNTHETIC TAX ID TEST CARD (PAN-STYLE)"
        if doc_type == "pan"
        else "SYNTHETIC CITIZEN ID TEST CARD (12-DIGIT)"
    )
    return (
        f"Render a flat graphic software test fixture card titled '{card_label}'. "
        f"Stamp a bold diagonal watermark across the entire card reading exactly: "
        f"'{SPECIMEN_WATERMARK}'. "
        "Do NOT include any government emblem, national symbol, coat of arms, seal, or real agency name. "
        f"Display fictional test fields: Holder: {full_name.upper()}, DOB: {dob}, "
        f"Synthetic Test ID: {id_number} (checksum-invalid synthetic identifier)."
    )


class VertexSpecimenCardGenerator:
    """Generates watermarked PAN + Aadhaar specimen cards using MODEL_TABLE['specimen_image']."""

    def __init__(
        self,
        transport: VertexModelTransport,
        storage: SpecimenStorage,
    ) -> None:
        self.transport = transport
        self.storage = storage
        self.model_config = MODEL_TABLE["specimen_image"]

    def _candidate_chain(self) -> list[tuple[str, str]]:
        chain: list[tuple[str, str]] = [(self.model_config.model_id, self.model_config.location)]
        for fb in self.model_config.fallbacks:
            chain.append((fb.model_id, fb.location))
        return chain

    async def _render_single_card(
        self,
        *,
        doc_type: Literal["pan", "aadhaar"],
        customer_id: str,
        full_name: str,
        dob: str,
        id_number: str,
    ) -> tuple[bytes, str]:
        prompt = build_specimen_prompt(
            doc_type=doc_type,
            full_name=full_name,
            dob=dob,
            id_number=id_number,
        )
        errors: list[str] = []
        for model_id, location in self._candidate_chain():
            validate_model_location(model_id, location)
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            }
            try:
                resp = await self.transport.generate_content(
                    model_id=model_id,
                    location=location,
                    payload=payload,
                )
                candidates = resp.get("candidates", [])
                if not candidates:
                    raise ValueError(f"Empty candidates from {model_id}@{location}")
                parts = candidates[0].get("content", {}).get("parts", [])
                # Require a final non-thought inlineData image part; never fall back to a thought draft
                non_thought_images = [
                    p["inlineData"]
                    for p in parts
                    if isinstance(p, dict) and "inlineData" in p and not p.get("thought")
                ]
                if not non_thought_images or not non_thought_images[-1].get("data"):
                    raise ValueError(
                        f"No non-thought inlineData image returned by {model_id}@{location}"
                    )
                chosen = non_thought_images[-1]
                raw_bytes = base64.b64decode(chosen["data"])
                watermarked = inject_png_specimen_metadata(
                    raw_bytes,
                    doc_type=doc_type,
                    customer_id=customer_id,
                    full_name=full_name,
                    dob=dob,
                    id_number=id_number,
                    model_id=model_id,
                )
                check = verify_specimen_png(watermarked)
                if (
                    not check["valid_png"]
                    or not check["has_watermark"]
                    or check["has_forbidden_emblem"]
                ):
                    raise SpecimenCardError(
                        f"Generated card failed specimen metadata verification: {check}"
                    )
                return watermarked, model_id
            except Exception as exc:
                errors.append(f"{model_id}@{location}: {exc}")

        raise SpecimenCardError(
            f"All specimen image models failed for {doc_type} ({'; '.join(errors)})"
        )

    async def generate_and_upload_pair(
        self,
        *,
        customer_id: str,
        full_name: str,
        dob: str,
        pan_number: str,
        aadhaar_number: str,
    ) -> GeneratedSpecimenPair:
        pan_bytes, pan_model = await self._render_single_card(
            doc_type="pan",
            customer_id=customer_id,
            full_name=full_name,
            dob=dob,
            id_number=pan_number,
        )
        aadhaar_bytes, aadhaar_model = await self._render_single_card(
            doc_type="aadhaar",
            customer_id=customer_id,
            full_name=full_name,
            dob=dob,
            id_number=aadhaar_number,
        )

        pan_uri = await self.storage.upload_png(
            customer_id=customer_id,
            doc_type="pan",
            data=pan_bytes,
            metadata={
                "watermark": SPECIMEN_WATERMARK,
                "model": pan_model,
                "synthetic": "true",
            },
        )
        aadhaar_uri = await self.storage.upload_png(
            customer_id=customer_id,
            doc_type="aadhaar",
            data=aadhaar_bytes,
            metadata={
                "watermark": SPECIMEN_WATERMARK,
                "model": aadhaar_model,
                "synthetic": "true",
            },
        )
        return GeneratedSpecimenPair(
            pan_gcs_uri=pan_uri,
            aadhaar_gcs_uri=aadhaar_uri,
            pan_image_model=pan_model,
            aadhaar_image_model=aadhaar_model,
            pan_bytes=pan_bytes,
            aadhaar_bytes=aadhaar_bytes,
        )
