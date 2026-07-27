"""Optional Gemini image generation for reviewed HOI4 asset candidates.

The Google client and Pillow are imported lazily so importing :mod:`hoi4`
keeps the core SDK dependency-free. Each public generation method performs one
Interactions API call and writes only a validated, locally converted PNG.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from .assets import _write_bytes_atomically


DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
DEFAULT_GEMINI_IMAGE_SIZE = "512"
GEMINI_INLINE_REQUEST_LIMIT = 20_000_000
FLAG_REFERENCE_LIMIT = 10
PORTRAIT_REFERENCE_LIMIT = 4
_MAX_ASPECT_RATIO_DEVIATION = 0.03

_KNOWN_MODEL_IMAGE_SIZES: dict[str, frozenset[str]] = {
    "gemini-3.1-flash-image": frozenset({"512", "1K", "2K", "4K"}),
    "gemini-3.1-flash-lite-image": frozenset({"1K"}),
    "gemini-3-pro-image": frozenset({"1K", "2K", "4K"}),
}
_GENERIC_IMAGE_SIZES = frozenset({"512", "1K", "2K", "4K"})
_REFERENCE_MIME_TYPES: dict[str, str] = {
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "HEIC": "image/heic",
    "HEIF": "image/heif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WEBP": "image/webp",
}

_FLAG_DEFAULT_STYLE = (
    "Use a raw SVG-style, HOI4-compatible flag graphic with bold geometric forms, "
    "high contrast, two to four strong colors, and perfectly uniform solid fills."
)
_FLAG_TECHNICAL_CONSTRAINTS = (
    "Compose a clean 3:2 flag that remains unmistakable at 10 by 7 pixels. "
    "Treat every band, symbol, and color expressly requested in the design brief as the "
    "complete inventory; do not invent additional motifs or colors. Make the defining symbol "
    "large enough to survive the tiny preview. "
    "Use no lettering, words, numbers, gradients, texture, lighting effects, shadows, "
    "frames, borders, or tiny details. Keep every symbol centered, simple, and safely "
    "inside the crop."
)
_PORTRAIT_DEFAULT_STYLE = (
    "Use historically grounded grand-strategy portraiture: a chest-up 1930s-1940s "
    "leader in period-correct clothing, painterly realism, a muted palette, subdued "
    "studio lighting, and a plain dark background."
)
_PORTRAIT_TECHNICAL_CONSTRAINTS = (
    "Compose a single chest-up subject in a 3:4 portrait. Keep the full head and both "
    "shoulders safely inside the crop with comfortable margins. Use no text, lettering, "
    "watermark, insignia label, decorative frame, or border."
)


class GeminiImageError(RuntimeError):
    """Base exception for optional Gemini image generation failures."""


class GeminiBackendUnavailableError(GeminiImageError):
    """Raised when the Gemini extra is not installed."""


class GeminiAuthenticationError(GeminiImageError):
    """Raised when credentials are missing or rejected."""


class GeminiImageGenerationError(GeminiImageError):
    """Raised when Gemini cannot complete the requested image generation."""


class GeminiImageResponseError(GeminiImageError):
    """Raised when Gemini returns no usable PNG output."""


@dataclass(frozen=True)
class GeminiImageResult:
    """Metadata for one validated Gemini-generated PNG candidate."""

    path: Path
    prompt: str
    model: str
    mime_type: str
    dimensions: tuple[int, int]
    sha256: str

    @property
    def width(self) -> int:
        """Return the generated image width."""

        return self.dimensions[0]

    @property
    def height(self) -> int:
        """Return the generated image height."""

        return self.dimensions[1]


class GeminiImageGenerator:
    """Generate reviewable flag and portrait PNG candidates with Gemini.

    The generator never retries or changes models. A caller-provided client is
    useful for testing and remains owned by the caller; otherwise a Google
    client is created lazily from ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_GEMINI_IMAGE_MODEL,
        image_size: str = DEFAULT_GEMINI_IMAGE_SIZE,
        client: Any | None = None,
    ) -> None:
        self.model = _require_text(model, label="model")
        self.image_size = _validate_image_size(self.model, image_size)
        self._client = client
        self._owns_client = False
        self._closed = False

    def __enter__(self) -> GeminiImageGenerator:
        if self._closed:
            raise RuntimeError("GeminiImageGenerator is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close a lazily created Google client."""

        if self._closed:
            return
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
        self._client = None
        self._closed = True

    def generate_flag_candidate(
        self,
        description: str,
        destination: str | Path,
        *,
        style: str | None = None,
        ideology: str | None = None,
        reference_images: Iterable[str | Path] = (),
        image_size: str | None = None,
        overwrite: bool = False,
    ) -> GeminiImageResult:
        """Generate one 3:2 PNG flag candidate without importing it into a mod."""

        prompt = _flag_prompt(description, style=style, ideology=ideology)
        return self._generate_candidate(
            prompt,
            destination,
            aspect_ratio="3:2",
            reference_images=reference_images,
            reference_limit=FLAG_REFERENCE_LIMIT,
            image_size=image_size,
            overwrite=overwrite,
        )

    def generate_portrait_candidate(
        self,
        description: str,
        destination: str | Path,
        *,
        style: str | None = None,
        reference_images: Iterable[str | Path] = (),
        image_size: str | None = None,
        overwrite: bool = False,
    ) -> GeminiImageResult:
        """Generate one 3:4 PNG portrait candidate without importing it into a mod."""

        prompt = _portrait_prompt(description, style=style)
        return self._generate_candidate(
            prompt,
            destination,
            aspect_ratio="3:4",
            reference_images=reference_images,
            reference_limit=PORTRAIT_REFERENCE_LIMIT,
            image_size=image_size,
            overwrite=overwrite,
        )

    def _generate_candidate(
        self,
        prompt: str,
        destination: str | Path,
        *,
        aspect_ratio: str,
        reference_images: Iterable[str | Path],
        reference_limit: int,
        image_size: str | None,
        overwrite: bool,
    ) -> GeminiImageResult:
        if self._closed:
            raise RuntimeError("GeminiImageGenerator is closed")
        target = _require_png_destination(destination)
        if not overwrite and (target.exists() or target.is_symlink()):
            raise FileExistsError(f"Refusing to overwrite image candidate: {target}")

        resolved_size = _validate_image_size(
            self.model,
            self.image_size if image_size is None else image_size,
        )
        Image, unidentified_error = _require_pillow()
        references = tuple(reference_images)
        if len(references) > reference_limit:
            raise ValueError(
                f"At most {reference_limit} reference images are allowed for this asset type"
            )
        request_input: list[dict[str, str]] = [{"type": "text", "text": prompt}]
        request_input.extend(
            _encode_reference_image(path, Image=Image, unidentified_error=unidentified_error)
            for path in references
        )
        response_format = {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": aspect_ratio,
            "image_size": resolved_size,
        }
        _validate_request_size(self.model, request_input, response_format)

        client = self._get_client()
        try:
            interaction = client.interactions.create(
                model=self.model,
                input=request_input,
                response_format=response_format,
            )
        except Exception as exc:
            raise _translate_provider_error(exc) from exc

        response_bytes = _decode_output_image(interaction)
        png_bytes, dimensions = _convert_response_to_png(
            response_bytes,
            Image=Image,
            unidentified_error=unidentified_error,
            aspect_ratio=aspect_ratio,
        )
        _write_bytes_atomically(target, png_bytes, overwrite=overwrite)
        return GeminiImageResult(
            path=target,
            prompt=prompt,
            model=self.model,
            mime_type="image/png",
            dimensions=dimensions,
            sha256=hashlib.sha256(png_bytes).hexdigest(),
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            raise GeminiAuthenticationError(
                "Set GEMINI_API_KEY or GOOGLE_API_KEY before generating an image."
            )
        try:
            genai = import_module("google.genai")
            client = genai.Client()
        except ImportError as exc:
            raise GeminiBackendUnavailableError(
                "Gemini image generation requires the 'gemini' extra. "
                "Install 'hoi4-agent-sdk[gemini]' and retry."
            ) from exc
        except Exception as exc:
            raise GeminiAuthenticationError(
                "Gemini credentials could not initialize the API client."
            ) from exc
        self._client = client
        self._owns_client = True
        return client


def _flag_prompt(description: str, *, style: str | None, ideology: str | None) -> str:
    subject = _require_text(description, label="description")
    aesthetic = (
        _FLAG_DEFAULT_STYLE
        if style is None
        else f"Use this requested visual style: {_require_text(style, label='style')}"
    )
    parts = [
        "Create one original national flag candidate for a Hearts of Iron IV mod.",
        f"Design brief: {subject}",
    ]
    if ideology is not None:
        parts.append(f"Ideological context: {_require_text(ideology, label='ideology')}")
    parts.extend((aesthetic, _FLAG_TECHNICAL_CONSTRAINTS))
    return "\n".join(parts)


def _portrait_prompt(description: str, *, style: str | None) -> str:
    subject = _require_text(description, label="description")
    aesthetic = (
        _PORTRAIT_DEFAULT_STYLE
        if style is None
        else f"Use this requested visual style: {_require_text(style, label='style')}"
    )
    return "\n".join(
        (
            "Create one original leader portrait candidate for a Hearts of Iron IV mod.",
            f"Subject brief: {subject}",
            aesthetic,
            _PORTRAIT_TECHNICAL_CONSTRAINTS,
        )
    )


def _require_text(value: str, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    return text


def _validate_image_size(model: str, image_size: str) -> str:
    if not isinstance(image_size, str) or image_size not in _GENERIC_IMAGE_SIZES:
        raise ValueError(
            "image_size must be one of '512', '1K', '2K', or '4K' with uppercase K"
        )
    supported = _KNOWN_MODEL_IMAGE_SIZES.get(model)
    if supported is not None and image_size not in supported:
        options = ", ".join(sorted(supported, key=("512", "1K", "2K", "4K").index))
        raise ValueError(f"{model} supports image_size values: {options}")
    return image_size


def _require_png_destination(destination: str | Path) -> Path:
    target = Path(destination).expanduser().resolve(strict=False)
    if target.suffix.lower() != ".png":
        raise ValueError("Gemini image candidates must use a .png destination")
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"Image candidate destination is a directory: {target}")
    return target


def _require_pillow() -> tuple[Any, type[Exception]]:
    try:
        Image = import_module("PIL.Image")
        unidentified_error = getattr(import_module("PIL"), "UnidentifiedImageError")
    except (ImportError, AttributeError) as exc:
        raise GeminiBackendUnavailableError(
            "Gemini image generation requires the 'gemini' extra. "
            "Install 'hoi4-agent-sdk[gemini]' and retry."
        ) from exc
    return Image, unidentified_error


def _encode_reference_image(
    source: str | Path,
    *,
    Image: Any,
    unidentified_error: type[Exception],
) -> dict[str, str]:
    path = Path(source).expanduser().resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"Reference image does not exist: {path}")
    try:
        content = path.read_bytes()
        with Image.open(io.BytesIO(content)) as opened:
            opened.load()
            image_format = str(opened.format or "").upper()
    except (unidentified_error, OSError) as exc:
        raise ValueError(f"Reference image is not a supported raster: {path}") from exc
    mime_type = _REFERENCE_MIME_TYPES.get(image_format)
    if mime_type is None:
        raise ValueError(f"Reference image format is not supported by Gemini: {image_format}")
    return {
        "type": "image",
        "data": base64.b64encode(content).decode("ascii"),
        "mime_type": mime_type,
    }


def _validate_request_size(
    model: str,
    request_input: list[dict[str, str]],
    response_format: dict[str, str],
) -> None:
    payload = json.dumps(
        {
            "model": model,
            "input": [{"type": "user_input", "content": request_input}],
            "response_format": response_format,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) >= GEMINI_INLINE_REQUEST_LIMIT:
        raise ValueError("Gemini inline request must remain below the 20 MB API limit")


def _get_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _decode_output_image(interaction: Any) -> bytes:
    output_image = _get_field(interaction, "output_image")
    if output_image is None:
        raise GeminiImageResponseError(
            "Gemini returned no image output; the request may have been refused."
        )
    encoded = _get_field(output_image, "data")
    if not isinstance(encoded, (str, bytes)) or not encoded:
        raise GeminiImageResponseError("Gemini returned image output without base64 data.")
    mime_type = _get_field(output_image, "mime_type") or "image/jpeg"
    if mime_type != "image/jpeg":
        raise GeminiImageResponseError(
            f"Gemini returned {mime_type!r}; an image/jpeg response was required."
        )
    try:
        encoded_bytes = encoded.encode("ascii") if isinstance(encoded, str) else encoded
        return base64.b64decode(encoded_bytes, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise GeminiImageResponseError("Gemini returned malformed base64 image data.") from exc


def _convert_response_to_png(
    content: bytes,
    *,
    Image: Any,
    unidentified_error: type[Exception],
    aspect_ratio: str,
) -> tuple[bytes, tuple[int, int]]:
    try:
        with Image.open(io.BytesIO(content)) as opened:
            opened.load()
            if opened.format != "JPEG":
                raise GeminiImageResponseError("Gemini image output is not a JPEG raster.")
            source_dimensions = cast(tuple[int, int], opened.size)
            dimensions = _normalized_aspect_dimensions(source_dimensions, aspect_ratio)
            left = (source_dimensions[0] - dimensions[0]) // 2
            top = (source_dimensions[1] - dimensions[1]) // 2
            normalized = opened.crop(
                (left, top, left + dimensions[0], top + dimensions[1])
            )
            output = io.BytesIO()
            normalized.convert("RGB").save(output, format="PNG")
    except GeminiImageResponseError:
        raise
    except (unidentified_error, OSError, ValueError) as exc:
        raise GeminiImageResponseError("Gemini returned invalid raster image data.") from exc
    if dimensions[0] <= 0 or dimensions[1] <= 0:
        raise GeminiImageResponseError("Gemini returned an empty image raster.")
    return output.getvalue(), dimensions


def _normalized_aspect_dimensions(
    dimensions: tuple[int, int], aspect_ratio: str
) -> tuple[int, int]:
    expected_width, expected_height = (int(value) for value in aspect_ratio.split(":"))
    if dimensions[0] <= 0 or dimensions[1] <= 0:
        raise GeminiImageResponseError("Gemini returned an empty image raster.")
    actual_ratio = dimensions[0] / dimensions[1]
    expected_ratio = expected_width / expected_height
    deviation = abs(actual_ratio - expected_ratio) / expected_ratio
    if deviation > _MAX_ASPECT_RATIO_DEVIATION:
        raise GeminiImageResponseError(
            f"Gemini returned {dimensions[0]}x{dimensions[1]}; "
            f"a {aspect_ratio} image was required."
        )
    scale = min(dimensions[0] // expected_width, dimensions[1] // expected_height)
    if scale <= 0:
        raise GeminiImageResponseError("Gemini returned an empty image raster.")
    return expected_width * scale, expected_height * scale


def _translate_provider_error(error: Exception) -> GeminiImageError:
    status_code = getattr(error, "status_code", None)
    name = type(error).__name__.lower()
    detail = str(error).lower()
    if status_code in {401, 403} or "authentication" in name or "permission" in name:
        return GeminiAuthenticationError("Gemini rejected the configured API credentials.")
    if status_code == 429 or "ratelimit" in name or "resourceexhausted" in name:
        return GeminiImageGenerationError(
            "Gemini rate-limited the image request; no candidate was written."
        )
    if "safety" in detail or "filtered out" in detail or "prohibited use" in detail:
        return GeminiImageGenerationError(
            "Gemini refused the image request for safety reasons; no candidate was written."
        )
    return GeminiImageGenerationError(
        "Gemini could not complete the image request; no candidate was written."
    )
