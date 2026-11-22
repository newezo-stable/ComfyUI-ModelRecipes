"""
ComfyUI-ModelRecipes
Auto-apply per-model generation recipes (steps, CFG, sampler, scheduler) directly
onto native KSampler widgets when switching models in UNETLoader / CheckpointLoaderSimple,
with full ComfyUI-Model-Manager (.md) metadata bridge and 100% SaveImageWithMetaData compatibility.
"""

from __future__ import annotations

import os
from typing import Optional

from aiohttp import web

from .recipe_engine import RecipeStore, write_recipe_to_md_frontmatter

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

EXT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RECIPES_PATH = os.path.join(EXT_DIR, "default_recipes.json")

MODEL_FOLDERS = ("diffusion_models", "unet", "unet_gguf", "checkpoints")


def _get_user_recipes_path() -> str:
    try:
        import folder_paths

        user_dir = folder_paths.get_user_directory()
        return os.path.join(user_dir, "default", "model_recipes.json")
    except Exception:
        return os.path.join(EXT_DIR, "model_recipes.json")


def _find_abs_model_path(model_name: str, folder_hint: Optional[str] = None) -> Optional[str]:
    if not model_name:
        return None
    try:
        import folder_paths
    except Exception:
        return None

    folders = []
    if folder_hint:
        folders.append(folder_hint)
    for f in MODEL_FOLDERS:
        if f not in folders:
            folders.append(f)

    variants = [model_name, model_name.replace("/", os.sep), model_name.replace("\\", os.sep)]
    for folder in folders:
        for cand in variants:
            try:
                full = folder_paths.get_full_path(folder, cand)
                if full and os.path.isfile(full):
                    return full
            except Exception:
                continue
    return None


STORE = RecipeStore(_get_user_recipes_path(), DEFAULT_RECIPES_PATH)


def _register_routes() -> None:
    try:
        import server
    except Exception:
        return

    ps = getattr(server, "PromptServer", None)
    if ps is None or getattr(ps, "instance", None) is None:
        return

    routes = ps.instance.routes

    @routes.get("/model-recipes/resolve")
    async def api_resolve_recipe(request: web.Request) -> web.Response:
        model = request.query.get("model", "").strip()
        folder = request.query.get("folder", "").strip() or None
        parse_md = request.query.get("parse_md", "true").lower() not in ("0", "false", "no")
        if not model:
            return web.json_response({"found": False, "error": "Missing model query parameter"}, status=400)

        abs_path = _find_abs_model_path(model, folder)
        # Always re-read JSON if modified externally on disk
        try:
            STORE.reload()
        except Exception:
            pass
        result = STORE.resolve(model, abs_model_path=abs_path, parse_md_body=parse_md)
        result["recipes_file"] = STORE.user_json_path
        result["has_model_file"] = bool(abs_path)
        return web.json_response(result)

    @routes.post("/model-recipes/save")
    async def api_save_recipe(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        model = str(payload.get("model", "")).strip()
        target = str(payload.get("target", "json")).strip().lower()
        folder = str(payload.get("folder", "")).strip() or None
        recipe = payload.get("recipe")
        if not model or not isinstance(recipe, dict):
            return web.json_response({"ok": False, "error": "Missing model or recipe"}, status=400)

        if target == "md":
            abs_path = _find_abs_model_path(model, folder)
            if not abs_path:
                return web.json_response(
                    {"ok": False, "error": f"Model file not found on disk for '{model}'"},
                    status=404,
                )
            md_path = os.path.splitext(abs_path)[0] + ".md"
            written = write_recipe_to_md_frontmatter(md_path, recipe)
            return web.json_response({"ok": True, "target": "md", "path": written})

        key = STORE.save_exact_recipe(model, recipe)
        return web.json_response({"ok": True, "target": "json", "key": key, "path": STORE.user_json_path})

    @routes.get("/model-recipes/list")
    async def api_list_recipes(request: web.Request) -> web.Response:
        try:
            data = STORE.reload()
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "path": STORE.user_json_path, "data": data})

    @routes.post("/model-recipes/reload")
    async def api_reload_recipes(request: web.Request) -> web.Response:
        try:
            data = STORE.reload()
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response(
            {
                "ok": True,
                "path": STORE.user_json_path,
                "exact_count": len(data.get("exact", {})),
                "pattern_count": len(data.get("patterns", [])),
            }
        )

    print(f"[ComfyUI-ModelRecipes] Loaded ({STORE.user_json_path})")


_register_routes()
