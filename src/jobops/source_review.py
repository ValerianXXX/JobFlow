"""Bounded, in-memory original-page rendering for an authenticated local review."""

from __future__ import annotations

import base64
import io

from PIL import Image

from .errors import JobOpsError
from .util import sha256_bytes


def source_page(raw: bytes, extension: str, *, expected_hash: str, page: int) -> dict:
    if sha256_bytes(raw) != expected_hash:
        raise JobOpsError(
            "SOURCE_REVIEW_HASH_MISMATCH", "The source changed; reload its review."
        )
    if (
        type(page) is not int
        or not 1 <= page <= 60
        or not 0 < len(raw) <= 16 * 1024 * 1024
    ):
        raise JobOpsError(
            "SOURCE_REVIEW_LIMIT",
            "Preview supports files up to 16 MiB and at most 60 pages.",
        )
    try:
        if extension == ".pdf":
            import pdfplumber

            with pdfplumber.open(io.BytesIO(raw)) as document:
                count = len(document.pages)
                if not 1 <= count <= 60 or page > count:
                    raise ValueError()
                original = document.pages[page - 1]
                if original.width * original.height * (110 / 72) ** 2 > 25_000_000:
                    raise ValueError()
                image = original.to_image(resolution=110).original.convert("RGB")
        elif extension in {".png", ".jpg", ".jpeg"}:
            with Image.open(io.BytesIO(raw)) as original:
                if (
                    page != 1
                    or original.width * original.height > 25_000_000
                    or getattr(original, "n_frames", 1) != 1
                ):
                    raise ValueError()
                count, image = 1, original.convert("RGB")
        else:
            raise JobOpsError(
                "SOURCE_REVIEW_FORMAT",
                "Open the original DOCX/text file alongside this review; inline page preview supports PDF and images.",
            )
        output = io.BytesIO()
        image.save(output, format="PNG")
        if output.tell() > 6 * 1024 * 1024:
            raise ValueError()
        return {
            "page": page,
            "page_count": count,
            "source_sha256": expected_hash,
            "image_data_url": "data:image/png;base64,"
            + base64.b64encode(output.getvalue()).decode("ascii"),
            "region_accuracy": "UNVERIFIED",
            "real_external_actions": 0,
        }
    except JobOpsError:
        raise
    except Exception as exc:
        raise JobOpsError(
            "SOURCE_REVIEW_UNAVAILABLE",
            "This source page could not be rendered within the preview limits.",
        ) from exc
