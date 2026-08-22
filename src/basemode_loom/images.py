"""One-off OpenAI image generation for a loom branch.

Everything else in this codebase generates text through litellm so one model
plan can span any provider. Image generation isn't part of that plan — it's a
single explicit action tied to OpenAI's Images API — so it goes straight
through the ``openai`` SDK instead of widening litellm's role for one call
site.
"""

from __future__ import annotations

import os

from basemode.keys import get_key
from openai import OpenAI

MAX_PROMPT_CHARS = 4000


class ImageGenerationError(Exception):
    """Raised when no OpenAI key is configured, or the Images API call fails."""


def _resolve_openai_key() -> str | None:
    """Mirrors credentials.py's stored-then-environment key resolution."""
    return get_key("openai") or os.environ.get("OPENAI_API_KEY")


def generate_branch_image(prompt: str) -> tuple[str, str]:
    """Generate an image for `prompt`, returning (base64 image data, mime type)."""
    api_key = _resolve_openai_key()
    if not api_key:
        raise ImageGenerationError("no OpenAI API key configured")

    try:
        response = OpenAI(api_key=api_key).images.generate(
            model="gpt-image-2",
            prompt=prompt,
            size="1024x1024",
        )
    except Exception as exc:
        raise ImageGenerationError(str(exc)) from exc

    data = response.data or []
    if not data or not data[0].b64_json:
        raise ImageGenerationError("image API returned no image data")

    return data[0].b64_json, "image/png"
