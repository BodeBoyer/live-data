"""Optional: let Claude read a messy prebuilt listing and return a clean spec list.

Used only when `[llm] enabled = true` and ANTHROPIC_API_KEY is set. Output is a strict
JSON schema, then every model string is run back through the regex recognizer so the LLM
can't invent a SKU we don't price. Results are cached in SQLite by text hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..db import Database
from ..watchlist import Watchlist, normalize
from .extract import Component, Extraction, extract

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_prebuilt": {"type": "boolean", "description": "true if this sells a whole desktop computer"},
        "is_laptop": {"type": "boolean"},
        "is_wanted_ad": {"type": "boolean", "description": "true if the poster wants to BUY, not sell"},
        "gpu": {"type": ["string", "null"], "description": "exact GPU model as written, e.g. 'RTX 4070 Ti Super', or null"},
        "cpu": {"type": ["string", "null"], "description": "exact CPU model, e.g. 'Ryzen 7 7800X3D' or 'i7-13700K', or null"},
        "ram_gb": {"type": ["integer", "null"], "description": "total system RAM in GB"},
        "ram_type": {"type": ["string", "null"], "enum": ["ddr3", "ddr4", "ddr5", None]},
        "ram_is_sodimm": {"type": "boolean"},
        "drives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "capacity_gb": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["nvme", "sata_ssd", "hdd", "unknown_ssd"]},
                },
                "required": ["capacity_gb", "kind"],
                "additionalProperties": False,
            },
        },
        "condition_problems": {"type": "array", "items": {"type": "string"},
                               "description": "faults the seller admits: not booting, artifacts, for parts, etc."},
        "missing_parts": {"type": "array", "items": {"type": "string"}, "description": "e.g. 'gpu', 'ssd', 'os'"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["is_prebuilt", "is_laptop", "is_wanted_ad", "gpu", "cpu", "ram_gb", "ram_type", "ram_is_sodimm",
                 "drives", "condition_problems", "missing_parts", "confidence"],
    "additionalProperties": False,
}

SYSTEM = (
    "You read used-marketplace listings for PC hardware and extract the components for sale. "
    "Report only what the text states or clearly implies. Do not guess a model number that is not written. "
    "If a capacity could be VRAM rather than system RAM, prefer the system-RAM reading only when the text says RAM/memory/DDR. "
    "Drives: NVMe/M.2 -> nvme; 2.5in SATA -> sata_ssd; spinning disk -> hdd; 'SSD' with no interface -> unknown_ssd."
)


def _cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


class LlmExtractor:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", db: Database | None = None, client: Any = None):
        self.model = model
        self.effort = effort
        self.db = db
        if client is None:
            import anthropic  # imported lazily so the package works without the extra installed
            client = anthropic.Anthropic()
        self.client = client

    def analyze(self, title: str, description: str | None) -> dict[str, Any]:
        text = f"TITLE: {title}\n\nDESCRIPTION:\n{description or '(none)'}"
        key = _cache_key(self.model, text)
        if self.db:
            cached = self.db.cache_get(key)
            if cached:
                return json.loads(cached)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": text}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to process this listing")
        raw = next(b.text for b in response.content if b.type == "text")
        data = json.loads(raw)
        if self.db:
            self.db.cache_put(key, self.model, raw)
        return data


def merge_llm(extraction: Extraction, data: dict[str, Any], watchlist: Watchlist) -> Extraction:
    """Fill gaps in the regex extraction with the LLM's reading. Regex findings are kept; the LLM adds."""
    ex = extraction
    ex.method = "regex+llm"
    found = ex.categories()

    def recognize(model_text: str | None, category: str) -> Component | None:
        if not model_text:
            return None
        t = normalize(model_text)
        for sku in watchlist.patterned():
            if sku.category == category and sku.matches(t):
                return Component(category, sku.key, sku.name, 1, {"platform": sku.platform, "vram_gb": sku.vram_gb, "llm": True}, False)
        ex.notes.append(f"LLM read {category} '{model_text}' but it is not in the watchlist")
        return None

    if "gpu" not in found and "no gpu" not in ex.soft_flags and "gpu" not in [m.lower() for m in data.get("missing_parts", [])]:
        c = recognize(data.get("gpu"), "gpu")
        if c:
            ex.components.insert(0, c)
    if "cpu" not in found:
        c = recognize(data.get("cpu"), "cpu")
        if c:
            ex.components.append(c)
    if "ram" not in found and data.get("ram_gb"):
        cap = int(data["ram_gb"])
        rt = data.get("ram_type") or ("ddr5" if any(c.detail.get("platform") in ("am5", "lga1851") for c in ex.components) else "ddr4")
        key = f"{rt}-sodimm-{cap}gb" if data.get("ram_is_sodimm") else f"{rt}-{cap}gb"
        ex.components.append(Component("ram", key, f"{cap}GB {rt.upper()} (LLM)", 1, {"capacity_gb": cap, "type": rt, "llm": True}))
    if "ssd" not in found:
        for d in data.get("drives", [])[:3]:
            cap, kind = int(d.get("capacity_gb", 0)), d.get("kind")
            if cap <= 0 or kind == "hdd":
                continue
            fam = "nvme" if kind == "nvme" else "sata-ssd"
            if cap >= 1000:
                tb = max(1, round(cap / 1000))
                key, label = f"{fam}-{tb}tb", f"{tb}TB"
            else:
                key, label = f"{fam}-{512 if cap in (480, 500, 512) else cap}gb", f"{cap}GB"
            ex.components.append(Component("ssd", key, f"{label} {'NVMe' if fam == 'nvme' else 'SATA?'} SSD (LLM)", 1,
                                           {"capacity_gb": cap, "nvme": fam == "nvme", "llm": True}))
    for p in data.get("condition_problems", []) or []:
        tag = f"seller states: {p}"
        if tag not in ex.hard_flags:
            ex.hard_flags.append(tag)
    if data.get("is_wanted_ad"):
        ex.is_wanted_ad = True
    if data.get("is_laptop") and (ex.categories() & {"gpu", "cpu"}):
        ex.is_laptop = True
    cats = ex.categories()
    if data.get("is_prebuilt") or len(cats) >= 3:
        ex.kind = "prebuilt"
    elif len(cats) == 2 and ex.kind == "single":
        ex.kind = "bundle"
    return ex


def extract_with_llm(title: str, description: str | None, watchlist: Watchlist, llm: LlmExtractor | None,
                     only_for_prebuilts: bool = True) -> Extraction:
    ex = extract(title, description, watchlist)
    if llm is None:
        return ex
    looks_prebuilt = ex.kind == "prebuilt" or (description and len(description) > 120)
    if only_for_prebuilts and not looks_prebuilt:
        return ex
    # only spend tokens when regex left gaps
    missing = {"gpu", "cpu", "ram", "ssd"} - ex.categories()
    if not missing and ex.kind != "prebuilt":
        return ex
    if ex.kind == "prebuilt" and not missing:
        return ex
    try:
        data = llm.analyze(title, description)
    except Exception as e:  # LLM is optional; never block the pipeline
        ex.notes.append(f"LLM extraction skipped: {e}")
        return ex
    return merge_llm(ex, data, watchlist)
