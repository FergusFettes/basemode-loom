from __future__ import annotations

from types import SimpleNamespace

import pytest

from basemode_loom import images


def test_generate_branch_image_raises_without_key(monkeypatch):
    monkeypatch.setattr(images, "_resolve_openai_key", lambda: None)

    with pytest.raises(images.ImageGenerationError, match="no OpenAI API key"):
        images.generate_branch_image("a cat")


def test_generate_branch_image_returns_b64_and_mime(monkeypatch):
    monkeypatch.setattr(images, "_resolve_openai_key", lambda: "sk-test")

    captured = {}

    class FakeImages:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json="ZmFrZQ==")])

    class FakeClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.images = FakeImages()

    monkeypatch.setattr(images, "OpenAI", FakeClient)

    b64, mime = images.generate_branch_image("a cat")

    assert b64 == "ZmFrZQ=="
    assert mime == "image/png"
    assert captured["api_key"] == "sk-test"
    assert captured["prompt"] == "a cat"
    assert captured["model"] == "gpt-image-2"


def test_generate_branch_image_wraps_api_errors(monkeypatch):
    monkeypatch.setattr(images, "_resolve_openai_key", lambda: "sk-test")

    class FailingImages:
        def generate(self, **kwargs):
            raise RuntimeError("upstream exploded")

    class FakeClient:
        def __init__(self, api_key):
            self.images = FailingImages()

    monkeypatch.setattr(images, "OpenAI", FakeClient)

    with pytest.raises(images.ImageGenerationError, match="upstream exploded"):
        images.generate_branch_image("a cat")


def test_generate_branch_image_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(images, "_resolve_openai_key", lambda: "sk-test")

    class EmptyImages:
        def generate(self, **kwargs):
            return SimpleNamespace(data=[])

    class FakeClient:
        def __init__(self, api_key):
            self.images = EmptyImages()

    monkeypatch.setattr(images, "OpenAI", FakeClient)

    with pytest.raises(images.ImageGenerationError, match="no image data"):
        images.generate_branch_image("a cat")
