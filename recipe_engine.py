"""
ComfyUI-ModelRecipes — Pure Python recipe resolution & persistence engine.

Resolution priority (highest -> lowest):
  1. ComfyUI-Model-Manager .md YAML frontmatter (`recipe:` block) beside the model file
  2. Central `model_recipes.json` exact filename/path match
  3. Central `model_recipes.json` glob/regex pattern rules (auto-matches new finetune versions)
  4. Optional auto-parser for ComfyUI-Model-Manager .md description body
  5. Central `model_recipes.json` folder / model-family default
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML (already bundled with ComfyUI / ComfyUI-Model-Manager)
except Exception:
    yaml = None


# Canonical ComfyUI sampler & scheduler alias normalization (used by .md auto-parser)
SAMPLER_ALIASES: Dict[str, str] = {
    "euler a": "euler_ancestral",
    "euler_a": "euler_ancestral",
    "euler ancestral": "euler_ancestral",
    "eular a": "euler_ancestral",
    "eular": "euler",
    "euler": "euler",
    "er sde": "er_sde",
    "er-sde": "er_sde",
    "er_sde": "er_sde",
    "dpm++ 2m sde": "dpmpp_2m_sde",
    "dpm++ 2m sde gpu": "dpmpp_2m_sde_gpu",
    "dpmpp_2m_sde_gpu": "dpmpp_2m_sde_gpu",
    "dpmpp_2m_sde": "dpmpp_2m_sde",
    "dpm++ 2m": "dpmpp_2m",
    "dpmpp_2m": "dpmpp_2m",
    "dpm++ sde": "dpmpp_sde",
    "dpmpp_sde": "dpmpp_sde",
    "res_multistep": "res_multistep",
    "res multistep": "res_multistep",
    "uni_pc": "uni_pc",
    "unipc": "uni_pc",
    "ddim": "ddim",
}

SCHEDULER_ALIASES: Dict[str, str] = {
    "simple": "simple",
    "normal": "normal",
    "karras": "karras",
    "beta": "beta",
    "beta57": "beta57",
    "sgm_uniform": "sgm_uniform",
    "sgm uniform": "sgm_uniform",
    "linear_quadratic": "linear_quadratic",
    "linear quadratic": "linear_quadratic",
    "ddim_uniform": "ddim_uniform",
    "exponential": "exponential",
    "bong_tangent": "bong_tangent",
}


def normalize_rel_path(model_name: str) -> str:
    return str(model_name or "").strip().replace("\\", "/").lstrip("/")


def _coerce_recipe_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "steps" in raw and raw["steps"] is not None:
        out["steps"] = int(round(float(raw["steps"])))
    if "cfg" in raw and raw["cfg"] is not None:
        out["cfg"] = round(float(raw["cfg"]), 2)
    sampler = raw.get("sampler_name") or raw.get("sampler")
    if sampler:
        s_low = str(sampler).strip().lower()
        out["sampler_name"] = SAMPLER_ALIASES.get(s_low, str(sampler).strip())
    sched = raw.get("scheduler")
    if sched:
        sc_low = str(sched).strip().lower()
        out["scheduler"] = SCHEDULER_ALIASES.get(sc_low, str(sched).strip())
    for opt_int_key in ("detailer_steps", "upscale_steps", "second_pass_steps"):
        if opt_int_key in raw and raw[opt_int_key] is not None:
            out[opt_int_key] = int(round(float(raw[opt_int_key])))
    if raw.get("label"):
        out["label"] = str(raw["label"])
    if raw.get("notes"):
        out["notes"] = str(raw["notes"])
    return out


def split_frontmatter(md_text: str) -> Tuple[Optional[str], str]:
    """Split a Markdown file with optional YAML frontmatter into (yaml_str, body)."""
    text = md_text.lstrip("\ufeff")
    if not text.startswith("---"):
        return None, md_text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, md_text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return fm, body
    return None, md_text


def parse_md_frontmatter_recipe(md_path: str) -> Optional[Dict[str, Any]]:
    """Read `recipe:` block from a ComfyUI-Model-Manager .md YAML frontmatter."""
    if not md_path or not os.path.isfile(md_path):
        return None
    try:
        with open(md_path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except OSError:
        return None

    fm_str, _ = split_frontmatter(txt)
    if not fm_str:
        return None

    data = None
    if yaml is not None:
        try:
            data = yaml.safe_load(fm_str)
        except Exception:
            data = None
    if not isinstance(data, dict):
        # Minimal fallback parser for simple `recipe:` YAML block when PyYAML is unavailable
        m = re.search(r"^recipe\s*:\s*\n((?:[ \t]+\w+\s*:.*\n?)+)", fm_str, re.M)
        if not m:
            return None
        recipe_dict: Dict[str, Any] = {}
        for line in m.group(1).splitlines():
            kv = re.match(r"^[ \t]+(\w+)\s*:\s*(.+?)\s*$", line)
            if kv:
                recipe_dict[kv.group(1)] = kv.group(2).strip().strip("\"'")
        data = {"recipe": recipe_dict}

    recipe = data.get("recipe")
    if isinstance(recipe, dict):
        coerced = _coerce_recipe_fields(recipe)
        if any(k in coerced for k in ("steps", "cfg", "sampler_name", "scheduler")):
            return coerced
    return None


def _pick_midpoint(num_a: str, num_b: Optional[str], is_float: bool = False):
    a = float(num_a.replace(",", "."))
    if num_b:
        b = float(num_b.replace(",", "."))
        val = (a + b) / 2.0
    else:
        val = a
    return round(val, 2) if is_float else int(round(val))


def parse_md_body_recipe(md_path: str) -> Optional[Dict[str, Any]]:
    """Heuristic parser for CivitAI / HuggingFace descriptions saved by ComfyUI-Model-Manager."""
    if not md_path or not os.path.isfile(md_path):
        return None
    try:
        with open(md_path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except OSError:
        return None

    _, body = split_frontmatter(txt)
    # Clean HTML / markdown escapes for reliable regex matching
    clean = re.sub(r"<[^>]+>", " ", body)
    clean = clean.replace("\\_", "_").replace("–", "-").replace("—", "-").replace("~", "-")

    result: Dict[str, Any] = {}

    # Steps: e.g. "Steps: 20-30", "25 steps", "Steps: 16+"
    m_steps = re.search(
        r"(?:steps?\s*[:=]\s*(\d{1,3})(?:\s*[-]\s*(\d{1,3}))?|\b(\d{1,3})(?:\s*[-]\s*(\d{1,3}))?\s*steps?\b)",
        clean,
        re.I,
    )
    if m_steps:
        a = m_steps.group(1) or m_steps.group(3)
        b = m_steps.group(2) or m_steps.group(4)
        val = _pick_midpoint(a, b, is_float=False)
        if 1 <= val <= 150:
            result["steps"] = val

    # CFG: e.g. "CFG Scale: 4-6", "CFG: 3.5", "CFG 1+"
    m_cfg = re.search(
        r"cfg(?:\s*scale)?\s*[:=]?\s*(\d{1,2}(?:[.,]\d+)?)(?:\s*[-]\s*(\d{1,2}(?:[.,]\d+)?))?",
        clean,
        re.I,
    )
    if m_cfg:
        val = _pick_midpoint(m_cfg.group(1), m_cfg.group(2), is_float=True)
        if 0.5 <= val <= 30.0:
            result["cfg"] = val

    # Sampler: pick the earliest-mentioned valid sampler in the description (tie-break: longest alias)
    best_sampler = None
    best_pos = len(clean) + 1
    best_len = 0
    for alias, canon in SAMPLER_ALIASES.items():
        m = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", clean, re.I)
        if m:
            pos = m.start()
            if pos < best_pos or (pos == best_pos and len(alias) > best_len):
                best_pos = pos
                best_len = len(alias)
                best_sampler = canon
    if best_sampler:
        result["sampler_name"] = best_sampler

    # Scheduler: match explicit scheduler names (avoid matching "normal quality" tag)
    sched_text = re.sub(r"\bnormal\s+quality\b", "", clean, flags=re.I)
    for alias in sorted(SCHEDULER_ALIASES.keys(), key=len, reverse=True):
        # For "normal", require sampler/scheduler context to avoid false positives
        if alias == "normal":
            if re.search(r"(?:scheduler\s*[:=]\s*normal|euler\s*a?\s+normal)", sched_text, re.I):
                result["scheduler"] = "normal"
                break
        elif re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", sched_text, re.I):
            # Ignore negated recommendations like "Karras is not recommended"
            if alias == "karras" and re.search(r"karras\s+is\s+not", sched_text, re.I):
                continue
            result["scheduler"] = SCHEDULER_ALIASES[alias]
            break

    # Require at least 2 extracted parameters to avoid noisy guesses
    if len(result) >= 2:
        result["notes"] = "Auto-parsed from ComfyUI-Model-Manager .md description"
        return result
    return None


def write_recipe_to_md_frontmatter(md_path: str, recipe: Dict[str, Any]) -> str:
    """Insert or update the `recipe:` block inside a ComfyUI-Model-Manager .md file."""
    clean_recipe = _coerce_recipe_fields(recipe)
    existing_text = ""
    if os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8", errors="ignore") as f:
            existing_text = f.read()

    fm_str, body = split_frontmatter(existing_text)
    if yaml is not None:
        fm_data: Dict[str, Any] = {}
        if fm_str:
            try:
                loaded = yaml.safe_load(fm_str)
                if isinstance(loaded, dict):
                    fm_data = loaded
            except Exception:
                fm_data = {}
        fm_data["recipe"] = clean_recipe
        new_fm = yaml.safe_dump(fm_data, sort_keys=False, allow_unicode=True).strip()
    else:
        # Preserve existing frontmatter lines, replacing any previous `recipe:` block
        preserved = ""
        if fm_str:
            preserved = re.sub(r"(?m)^recipe\s*:\s*\n(?:[ \t]+.*\n?)*", "", fm_str).strip()
        recipe_lines = ["recipe:"]
        for k, v in clean_recipe.items():
            recipe_lines.append(f"  {k}: {v}")
        recipe_block = "\n".join(recipe_lines)
        new_fm = f"{preserved}\n{recipe_block}" if preserved else recipe_block

    body_clean = body.lstrip("\r\n")
    new_content = f"---\n{new_fm}\n---\n\n{body_clean}" if body_clean else f"---\n{new_fm}\n---\n"
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    return md_path


class RecipeStore:
    """Manages loading, resolving, and saving model recipes."""

    def __init__(self, user_json_path: str, default_json_path: str):
        self.user_json_path = user_json_path
        self.default_json_path = default_json_path
        self._data: Dict[str, Any] = {}
        self.ensure_initialized()
        self.reload()

    def ensure_initialized(self) -> None:
        if not os.path.isfile(self.user_json_path) and os.path.isfile(self.default_json_path):
            os.makedirs(os.path.dirname(self.user_json_path), exist_ok=True)
            shutil.copy2(self.default_json_path, self.user_json_path)

    def reload(self) -> Dict[str, Any]:
        self.ensure_initialized()
        target = self.user_json_path if os.path.isfile(self.user_json_path) else self.default_json_path
        with open(target, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    def resolve(
        self,
        model_name: str,
        abs_model_path: Optional[str] = None,
        parse_md_body: bool = True,
    ) -> Dict[str, Any]:
        norm = normalize_rel_path(model_name)
        norm_lower = norm.lower()
        base = os.path.basename(norm)
        base_lower = base.lower()
        stem_lower = os.path.splitext(base_lower)[0]

        md_path = None
        if abs_model_path:
            candidate_md = os.path.splitext(abs_model_path)[0] + ".md"
            if os.path.isfile(candidate_md):
                md_path = candidate_md

        # Priority 1: ComfyUI-Model-Manager .md YAML frontmatter (`recipe:`)
        if md_path:
            fm_recipe = parse_md_frontmatter_recipe(md_path)
            if fm_recipe:
                return {
                    "found": True,
                    "source": "model_manager_frontmatter",
                    "matched_key": os.path.basename(md_path),
                    "model": norm,
                    "md_path": md_path,
                    "recipe": fm_recipe,
                }

        # Priority 2a: Exact match in model_recipes.json (relative path, filename, or stem)
        exact_map: Dict[str, Any] = self._data.get("exact", {})
        for k, v in exact_map.items():
            k_norm = normalize_rel_path(k).lower()
            k_stem = os.path.splitext(os.path.basename(k_norm))[0]
            if k_norm in (norm_lower, base_lower) or k_stem == stem_lower:
                return {
                    "found": True,
                    "source": "exact_recipe",
                    "matched_key": k,
                    "model": norm,
                    "md_path": md_path,
                    "recipe": _coerce_recipe_fields(v),
                }

        # Priority 2b: Pattern rules in model_recipes.json (ordered list)
        for rule in self._data.get("patterns", []):
            pats = rule.get("match") or []
            if isinstance(pats, str):
                pats = [pats]
            for pat in pats:
                pat_norm = normalize_rel_path(pat).lower()
                if fnmatch.fnmatch(norm_lower, pat_norm) or fnmatch.fnmatch(base_lower, pat_norm):
                    return {
                        "found": True,
                        "source": "pattern_rule",
                        "matched_key": rule.get("label") or pat,
                        "model": norm,
                        "md_path": md_path,
                        "recipe": _coerce_recipe_fields(rule),
                    }

        # Priority 3: Auto-parse ComfyUI-Model-Manager .md description body
        if parse_md_body and md_path:
            parsed = parse_md_body_recipe(md_path)
            if parsed:
                return {
                    "found": True,
                    "source": "model_manager_md_parsed",
                    "matched_key": os.path.basename(md_path),
                    "model": norm,
                    "md_path": md_path,
                    "recipe": parsed,
                }

        # Priority 4: Folder / family default
        for fam in self._data.get("family_defaults", []):
            prefixes = fam.get("folder_prefixes") or []
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            for pref in prefixes:
                pref_norm = normalize_rel_path(pref).lower().rstrip("/") + "/"
                if norm_lower.startswith(pref_norm):
                    return {
                        "found": True,
                        "source": "family_default",
                        "matched_key": fam.get("label") or pref,
                        "model": norm,
                        "md_path": md_path,
                        "recipe": _coerce_recipe_fields(fam),
                    }

        return {
            "found": False,
            "source": "none",
            "matched_key": "",
            "model": norm,
            "md_path": md_path,
            "recipe": {},
        }

    def save_exact_recipe(self, model_name: str, recipe: Dict[str, Any]) -> str:
        norm = normalize_rel_path(model_name)
        key = os.path.basename(norm) or norm
        clean = _coerce_recipe_fields(recipe)
        self.reload()
        self._data.setdefault("exact", {})[key] = clean
        os.makedirs(os.path.dirname(self.user_json_path), exist_ok=True)
        with open(self.user_json_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        return key
