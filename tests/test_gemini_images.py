from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hoi4 import gemini_images
from hoi4.assets import FLAG_SIZES, PORTRAIT_SIZE
from hoi4.gemini_images import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    GeminiAuthenticationError,
    GeminiBackendUnavailableError,
    GeminiImageGenerationError,
    GeminiImageGenerator,
    GeminiImageResponseError,
)
from hoi4 import import_flag_to_mod, import_portrait_to_mod, write_portrait_gfx

Image = pytest.importorskip("PIL.Image")


def _png_bytes(size: tuple[int, int] = (300, 200)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, (30, 80, 160, 255)).save(output, format="PNG")
    return output.getvalue()


def _jpeg_bytes(size: tuple[int, int] = (300, 200)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (30, 80, 160)).save(output, format="JPEG")
    return output.getvalue()


def _png_from_jpeg(content: bytes) -> bytes:
    output = io.BytesIO()
    with Image.open(io.BytesIO(content)) as opened:
        opened.convert("RGB").save(output, format="PNG")
    return output.getvalue()


class FakeInteractions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response or SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(_jpeg_bytes()).decode("ascii"),
                mime_type="image/jpeg",
            )
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.interactions = FakeInteractions(response=response, error=error)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _client_for_image(size: tuple[int, int]) -> FakeClient:
    return FakeClient(
        response=SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(_jpeg_bytes(size)).decode("ascii"),
                mime_type="image/jpeg",
            )
        )
    )


class TestRequestsAndPrompts:
    def test_flag_uses_default_model_size_prompt_and_aspect_ratio(
        self, tmp_path: Path
    ) -> None:
        client = _client_for_image((768, 512))
        generator = GeminiImageGenerator(client=client)

        result = generator.generate_flag_candidate(
            "A silver eagle over a red and black field",
            tmp_path / "flag.png",
            ideology="neutrality",
        )

        call = client.interactions.calls[0]
        assert call["model"] == DEFAULT_GEMINI_IMAGE_MODEL
        assert call["response_format"] == {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": "3:2",
            "image_size": "512",
        }
        assert "raw SVG-style, HOI4-compatible flag graphic" in result.prompt
        assert "Ideological context: neutrality" in result.prompt
        assert "10 by 7 pixels" in result.prompt
        assert result.dimensions == (768, 512)

    def test_portrait_uses_historical_default_and_3_by_4_ratio(
        self, tmp_path: Path
    ) -> None:
        client = _client_for_image((384, 512))
        generator = GeminiImageGenerator(client=client)

        result = generator.generate_portrait_candidate(
            "A fictional Polish general in his early fifties",
            tmp_path / "portrait.png",
        )

        call = client.interactions.calls[0]
        assert call["response_format"]["aspect_ratio"] == "3:4"
        assert "1930s-1940s" in result.prompt
        assert "period-correct clothing" in result.prompt
        assert "full head and both shoulders safely inside the crop" in result.prompt
        assert result.dimensions == (384, 512)

    @pytest.mark.parametrize("asset_type", ["flag", "portrait"])
    def test_custom_style_replaces_default_aesthetic_but_keeps_constraints(
        self, tmp_path: Path, asset_type: str
    ) -> None:
        client = FakeClient()
        generator = GeminiImageGenerator(client=client)
        destination = tmp_path / f"{asset_type}.png"
        if asset_type == "flag":
            result = generator.generate_flag_candidate(
                "A mountain republic", destination, style="woodcut propaganda"
            )
            assert "raw SVG-style" not in result.prompt
            assert "10 by 7 pixels" in result.prompt
        else:
            client.interactions.response = SimpleNamespace(
                output_image=SimpleNamespace(
                    data=base64.b64encode(_jpeg_bytes((300, 400))).decode("ascii"),
                    mime_type="image/jpeg",
                )
            )
            result = generator.generate_portrait_candidate(
                "A fictional admiral", destination, style="ink wash illustration"
            )
            assert "painterly realism" not in result.prompt
            assert "full head and both shoulders" in result.prompt
        assert "Use this requested visual style: " in result.prompt

    def test_explicit_reference_is_the_only_uploaded_image(self, tmp_path: Path) -> None:
        reference = tmp_path / "reference.png"
        reference.write_bytes(_png_bytes((20, 30)))
        client = _client_for_image((300, 400))

        GeminiImageGenerator(client=client).generate_portrait_candidate(
            "Use the same fictional person",
            tmp_path / "candidate.png",
            reference_images=[reference],
        )

        request_input = client.interactions.calls[0]["input"]
        assert len(request_input) == 2
        assert request_input[0]["type"] == "text"
        assert request_input[1]["type"] == "image"
        assert request_input[1]["mime_type"] == "image/png"
        assert base64.b64decode(request_input[1]["data"]) == reference.read_bytes()

    def test_per_call_resolution_override(self, tmp_path: Path) -> None:
        client = FakeClient()
        GeminiImageGenerator(client=client).generate_flag_candidate(
            "A tricolor",
            tmp_path / "flag.png",
            image_size="2K",
        )
        assert client.interactions.calls[0]["response_format"]["image_size"] == "2K"

    def test_small_provider_ratio_variance_is_center_cropped_to_exact_ratio(
        self, tmp_path: Path
    ) -> None:
        result = GeminiImageGenerator(
            client=_client_for_image((448, 592))
        ).generate_portrait_candidate("A leader", tmp_path / "portrait.png")

        assert result.dimensions == (444, 592)
        with Image.open(result.path) as opened:
            assert opened.size == (444, 592)
            assert opened.format == "PNG"


class TestValidation:
    @pytest.mark.parametrize(
        ("model", "size"),
        [
            ("gemini-3.1-flash-lite-image", "512"),
            ("gemini-3-pro-image", "512"),
            ("gemini-3.1-flash-image", "8K"),
            ("gemini-3.1-flash-image", "1k"),
        ],
    )
    def test_rejects_invalid_known_model_resolutions(self, model: str, size: str) -> None:
        with pytest.raises(ValueError, match="image_size|supports"):
            GeminiImageGenerator(model=model, image_size=size, client=FakeClient())

    @pytest.mark.parametrize(
        ("model", "size"),
        [
            ("gemini-3.1-flash-image", "512"),
            ("gemini-3.1-flash-image", "4K"),
            ("gemini-3.1-flash-lite-image", "1K"),
            ("gemini-3-pro-image", "4K"),
            ("future-image-model", "2K"),
        ],
    )
    def test_accepts_supported_and_future_model_resolutions(
        self, model: str, size: str
    ) -> None:
        generator = GeminiImageGenerator(model=model, image_size=size, client=FakeClient())
        assert generator.model == model
        assert generator.image_size == size

    def test_rejects_bad_destination_and_empty_prompt(self, tmp_path: Path) -> None:
        generator = GeminiImageGenerator(client=FakeClient())
        with pytest.raises(ValueError, match="description"):
            generator.generate_flag_candidate(" ", tmp_path / "flag.png")
        with pytest.raises(ValueError, match=r"\.png"):
            generator.generate_flag_candidate("A flag", tmp_path / "flag.jpg")

    def test_reference_limits_apply_before_the_api_call(self, tmp_path: Path) -> None:
        client = FakeClient()
        generator = GeminiImageGenerator(client=client)
        with pytest.raises(ValueError, match="At most 4"):
            generator.generate_portrait_candidate(
                "A leader",
                tmp_path / "portrait.png",
                reference_images=[tmp_path / f"{index}.png" for index in range(5)],
            )
        with pytest.raises(ValueError, match="At most 10"):
            generator.generate_flag_candidate(
                "A flag",
                tmp_path / "flag.png",
                reference_images=[tmp_path / f"{index}.png" for index in range(11)],
            )
        assert client.interactions.calls == []

    def test_inline_request_limit_applies_before_the_api_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reference = tmp_path / "reference.png"
        reference.write_bytes(_png_bytes())
        client = FakeClient()
        monkeypatch.setattr(gemini_images, "GEMINI_INLINE_REQUEST_LIMIT", 100)
        with pytest.raises(ValueError, match="20 MB"):
            GeminiImageGenerator(client=client).generate_flag_candidate(
                "A flag",
                tmp_path / "flag.png",
                reference_images=[reference],
            )
        assert client.interactions.calls == []


class TestFailuresAndAtomicity:
    def test_missing_key_does_not_create_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        destination = tmp_path / "new" / "candidate.png"
        with pytest.raises(GeminiAuthenticationError, match="GEMINI_API_KEY"):
            GeminiImageGenerator().generate_flag_candidate("A flag", destination)
        assert not destination.exists()
        assert not destination.parent.exists()

    def test_missing_google_dependency_does_not_create_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "not-printed")
        real_import = gemini_images.import_module

        def missing_google(name: str) -> Any:
            if name == "google.genai":
                raise ImportError("missing")
            return real_import(name)

        monkeypatch.setattr(gemini_images, "import_module", missing_google)
        destination = tmp_path / "candidate.png"
        with pytest.raises(GeminiBackendUnavailableError, match="gemini"):
            GeminiImageGenerator().generate_flag_candidate("A flag", destination)
        assert not destination.exists()

    def test_missing_pillow_does_not_call_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeClient()
        real_import = gemini_images.import_module

        def missing_pillow(name: str) -> Any:
            if name.startswith("PIL"):
                raise ImportError("missing")
            return real_import(name)

        monkeypatch.setattr(gemini_images, "import_module", missing_pillow)
        with pytest.raises(GeminiBackendUnavailableError, match="gemini"):
            GeminiImageGenerator(client=client).generate_flag_candidate(
                "A flag", tmp_path / "candidate.png"
            )
        assert client.interactions.calls == []

    def test_refusal_rate_limit_and_provider_error_leave_destination_untouched(
        self, tmp_path: Path
    ) -> None:
        destination = tmp_path / "candidate.png"
        cases: list[tuple[FakeClient, type[Exception], str]] = [
            (
                FakeClient(response=SimpleNamespace(output_image=None)),
                GeminiImageResponseError,
                "no image output",
            ),
            (
                FakeClient(error=SimpleRateLimitError()),
                GeminiImageGenerationError,
                "rate-limited",
            ),
            (
                FakeClient(error=RuntimeError("image filtered out for safety")),
                GeminiImageGenerationError,
                "safety reasons",
            ),
            (
                FakeClient(error=RuntimeError("provider included a secret")),
                GeminiImageGenerationError,
                "could not complete",
            ),
        ]
        for client, exception_type, match in cases:
            with pytest.raises(exception_type, match=match) as caught:
                GeminiImageGenerator(client=client).generate_flag_candidate(
                    "A flag", destination
                )
            assert "secret" not in str(caught.value)
            assert not destination.exists()
            assert len(client.interactions.calls) == 1

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (
                SimpleNamespace(
                    output_image=SimpleNamespace(data="not base64!", mime_type="image/jpeg")
                ),
                "malformed base64",
            ),
            (
                SimpleNamespace(
                    output_image=SimpleNamespace(
                        data=base64.b64encode(b"not a raster").decode("ascii"),
                        mime_type="image/jpeg",
                    )
                ),
                "invalid raster",
            ),
            (
                SimpleNamespace(
                    output_image=SimpleNamespace(
                        data=base64.b64encode(_jpeg_bytes()).decode("ascii"),
                        mime_type="image/png",
                    )
                ),
                "image/jpeg",
            ),
            (
                SimpleNamespace(
                    output_image=SimpleNamespace(
                        data=base64.b64encode(_jpeg_bytes((400, 400))).decode("ascii"),
                        mime_type="image/jpeg",
                    )
                ),
                "3:2 image",
            ),
        ],
    )
    def test_invalid_responses_do_not_write(
        self, tmp_path: Path, response: Any, message: str
    ) -> None:
        destination = tmp_path / "candidate.png"
        with pytest.raises(GeminiImageResponseError, match=message):
            GeminiImageGenerator(client=FakeClient(response=response)).generate_flag_candidate(
                "A flag", destination
            )
        assert not destination.exists()

    def test_overwrite_protection_avoids_billable_call(self, tmp_path: Path) -> None:
        destination = tmp_path / "candidate.png"
        destination.write_bytes(b"original")
        client = FakeClient()
        with pytest.raises(FileExistsError, match="Refusing"):
            GeminiImageGenerator(client=client).generate_flag_candidate(
                "A flag", destination
            )
        assert destination.read_bytes() == b"original"
        assert client.interactions.calls == []

    def test_invalid_response_cannot_replace_existing_candidate(self, tmp_path: Path) -> None:
        destination = tmp_path / "candidate.png"
        destination.write_bytes(b"original candidate")
        response = SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(b"not a raster").decode("ascii"),
                mime_type="image/jpeg",
            )
        )
        with pytest.raises(GeminiImageResponseError, match="invalid raster"):
            GeminiImageGenerator(client=FakeClient(response=response)).generate_flag_candidate(
                "A flag",
                destination,
                overwrite=True,
            )
        assert destination.read_bytes() == b"original candidate"


class SimpleRateLimitError(RuntimeError):
    status_code = 429


class TestResultAndLifecycle:
    def test_result_metadata_is_complete_and_immutable(self, tmp_path: Path) -> None:
        response_content = _jpeg_bytes((600, 400))
        content = _png_from_jpeg(response_content)
        client = FakeClient(
            response={
                "output_image": {
                    "data": base64.b64encode(response_content).decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            }
        )
        result = GeminiImageGenerator(client=client).generate_flag_candidate(
            "A flag", tmp_path / "candidate.png"
        )
        assert result.path.read_bytes() == content
        assert result.model == DEFAULT_GEMINI_IMAGE_MODEL
        assert result.mime_type == "image/png"
        assert result.width == 600
        assert result.height == 400
        assert result.sha256 == hashlib.sha256(content).hexdigest()
        with pytest.raises(FrozenInstanceError):
            result.sha256 = "changed"  # type: ignore[misc]

    def test_context_manager_closes_only_owned_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        injected = FakeClient()
        with GeminiImageGenerator(client=injected) as generator:
            generator.generate_flag_candidate("A flag", tmp_path / "injected.png")
        assert not injected.closed

        owned = FakeClient()
        real_import = gemini_images.import_module

        def fake_import(name: str) -> Any:
            if name == "google.genai":
                return SimpleNamespace(Client=lambda: owned)
            return real_import(name)

        monkeypatch.setattr(gemini_images, "import_module", fake_import)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        with GeminiImageGenerator() as generator:
            generator.generate_flag_candidate("A flag", tmp_path / "owned.png")
        assert owned.closed


class TestImporterIntegration:
    def test_generated_flag_imports_to_all_exact_hoi4_tga_sizes(
        self, tmp_path: Path
    ) -> None:
        candidate = GeminiImageGenerator(
            client=_client_for_image((768, 512))
        ).generate_flag_candidate("A flag", tmp_path / "candidate.png")
        assets = import_flag_to_mod(tmp_path / "mod", "TST", candidate.path)
        for size_name, path in zip(("large", "medium", "small"), assets[0].paths):
            with Image.open(path) as opened:
                assert opened.size == FLAG_SIZES[size_name]
                assert opened.format == "TGA"

    def test_generated_portrait_imports_as_dxt5_and_writes_sprite(
        self, tmp_path: Path
    ) -> None:
        candidate = GeminiImageGenerator(
            client=_client_for_image((384, 512))
        ).generate_portrait_candidate("A leader", tmp_path / "candidate.png")
        portrait = import_portrait_to_mod(
            tmp_path / "mod",
            "TST",
            "leader",
            candidate.path,
        )
        gfx = write_portrait_gfx(tmp_path / "mod", "TST", "leader")

        assert portrait.read_bytes()[:4] == b"DDS "
        assert portrait.read_bytes()[84:88] == b"DXT5"
        with Image.open(portrait) as opened:
            assert opened.size == PORTRAIT_SIZE
            assert opened.format == "DDS"
        declaration = gfx.read_text(encoding="utf-8")
        assert 'name = "GFX_portrait_TST_leader"' in declaration
        assert 'texturefile = "gfx/leaders/TST/leader.dds"' in declaration
