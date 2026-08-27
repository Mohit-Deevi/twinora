"""Growstack LLM router image-generation backend.

Routes `image_generate` through the company's internal router instead of a personal FAL key, so
images are billed to the Growstack Azure account. Default model is **azure-gpt-image-2**.

The router is NOT OpenAI-shaped for images. It exposes:

    POST /v1/image
      {"model": "...", "use_azure": true, "customer_id": "...",
       "input":   {"prompt": "..."},                    # required; other keys are passed through
       "options": {"n": 1, "size": "1024x1024", "response_format": "url"}}
    -> {"request_id", "model", "provider", "images": [{"url", "b64_json", "revised_prompt"}], "cost_usd"}

Verified working 2026-08-25: azure-gpt-image-2 returned a 1024x1024 PNG in ~15s at $0.00 to the caller.

Config:
    image_gen:
      provider: growstack-router
      growstack-router:
        model: azure-gpt-image-2       # optional override
        customer_id: jarvis            # optional; tags spend in the router's usage reports

Cloudflare fronts the router and rejects the default Python-urllib User-Agent with "Error 1010",
so a browser UA is sent on every call.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://dev-llm-router.growstack.ai"
DEFAULT_MODEL = "azure-gpt-image-2"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# gpt-image-2 accepts a fixed set of sizes; map Hermes' aspect ratios onto the nearest one.
_SIZE_BY_ASPECT = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "4:3": "1536x1024",
    "3:2": "1536x1024",
    "9:16": "1024x1536",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
    "21:9": "1536x1024",
}

_MODELS = [
    {"id": "azure-gpt-image-2", "name": "GPT Image 2 (Azure)", "default": True,
     "notes": "Best text rendering and prompt adherence; billed to the Growstack Azure account."},
    {"id": "gpt-image-2", "name": "GPT Image 2 (OpenAI)"},
    {"id": "ByteDance/Seedream-4.5", "name": "Seedream 4.5"},
    {"id": "black-forest-labs/FLUX.1.1-pro", "name": "FLUX 1.1 Pro"},
    {"id": "gemini-2.5-flash-image", "name": "Gemini 2.5 Flash Image"},
    {"id": "ideogram-v-2", "name": "Ideogram v2"},
    {"id": "dall-e-3", "name": "DALL-E 3"},
]


def _hermes_home() -> pathlib.Path:
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"
    return pathlib.Path.home() / ".hermes"


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    p = _hermes_home() / ".env"
    if p.exists():
        m = re.search(rf"(?m)^{name}=(.*)$", p.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return default


def _cfg() -> Dict[str, Any]:
    """Read image_gen.growstack-router.* from config.yaml without importing Hermes internals."""
    try:
        import yaml
        cfg = yaml.safe_load((_hermes_home() / "config.yaml").read_text(encoding="utf-8")) or {}
        section = cfg.get("image_gen") or {}
        return section.get("growstack-router") or section.get("growstack_router") or {}
    except Exception:  # noqa: BLE001
        return {}


class GrowstackRouterImageProvider(ImageGenProvider):
    """Image generation through the Growstack internal LLM router."""

    @property
    def name(self) -> str:
        return "growstack-router"

    @property
    def display_name(self) -> str:
        return "Growstack Router (GPT Image 2)"

    def is_available(self) -> bool:
        return bool(_env("GROWSTACK_ROUTER_KEY"))

    def list_models(self) -> List[Dict[str, Any]]:
        return list(_MODELS)

    def default_model(self) -> Optional[str]:
        return _cfg().get("model") or DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        # /v1/image is text-to-image; /v1/image/edit exists but takes a different body, so
        # reference images are not claimed here rather than silently ignored.
        return {"modalities": ["text"], "max_reference_images": 0}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "fields": [
                {"key": "GROWSTACK_ROUTER_KEY", "label": "Growstack router API key",
                 "type": "secret", "required": True},
            ],
            "help": "Key for the internal LLM router. Images are billed to the Growstack Azure account.",
        }

    # ------------------------------------------------------------------ generate
    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        key = _env("GROWSTACK_ROUTER_KEY")
        if not key:
            return error_response(
                error="GROWSTACK_ROUTER_KEY is not set. Add it to <HERMES_HOME>/.env.",
                error_type="missing_credentials", provider=self.name)

        conf = _cfg()
        base = (conf.get("base_url") or _env("GROWSTACK_ROUTER_URL", DEFAULT_BASE)).rstrip("/")
        model = kwargs.get("model") or conf.get("model") or DEFAULT_MODEL
        aspect = resolve_aspect_ratio(aspect_ratio)
        size = _SIZE_BY_ASPECT.get(aspect, "1024x1024")

        if image_url or reference_image_urls:
            logger.info("growstack-router: reference images are not supported by /v1/image; "
                        "generating from the prompt only")

        body = {
            "model": model,
            "use_azure": str(model).startswith("azure-"),
            "customer_id": conf.get("customer_id") or "jarvis",
            "input": {"prompt": prompt},
            "options": {"n": 1, "size": size, "response_format": "url"},
        }
        req = urllib.request.Request(
            f"{base}/v1/image", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": UA},
        )

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            return error_response(
                error=f"router HTTP {e.code}: {detail}",
                error_type="provider_error", provider=self.name)
        except Exception as e:  # noqa: BLE001
            return error_response(
                error=f"router unreachable ({type(e).__name__}): {e}",
                error_type="network_error", provider=self.name)

        images = data.get("images") or []
        if not images:
            return error_response(
                error=f"router returned no images: {json.dumps(data)[:300]}",
                error_type="provider_contract", provider=self.name)

        first = images[0]
        url = first.get("url")
        if not url:
            return error_response(
                error="router returned an image entry without a URL",
                error_type="provider_contract", provider=self.name)

        # The router's URLs are time-limited SAS links, so materialise the bytes locally —
        # otherwise WhatsApp/Telegram sends can fail after expiry.
        image_ref: str = url
        try:
            image_ref = str(save_url_image(url, prefix="growstack"))
        except Exception as e:  # noqa: BLE001
            logger.warning("growstack-router: could not cache image locally (%s); returning URL", e)

        return success_response(
            image=image_ref,
            model=data.get("model") or model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            modality="text",
            extra={
                "size": size,
                "upstream_provider": data.get("provider"),
                "request_id": data.get("request_id"),
                "cost_usd": data.get("cost_usd"),
                "revised_prompt": first.get("revised_prompt"),
                "elapsed_s": round(time.time() - t0, 1),
            },
        )


def register(ctx) -> None:
    """Plugin entry point — wire the router image backend into the registry."""
    ctx.register_image_gen_provider(GrowstackRouterImageProvider())
