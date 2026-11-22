# ComfyUI-ModelRecipes

Automatically apply per-model generation recipes (`steps`, `cfg`, `sampler_name`, `scheduler`) directly onto your native ComfyUI `KSampler` widgets whenever you switch models in `UNETLoader`, `CheckpointLoaderSimple`, or `UnetLoaderGGUF`.

## Why this approach?

- **Zero extra nodes in your workflows** — works out of the box with all existing workflows without rewiring anything.
- **100% `SaveImageWithMetaData` compatible** — `UNETLoader`, `CheckpointLoaderSimple`, and `KSampler` remain completely native standard nodes, so image metadata capture continues to record model name, hash, steps, CFG, sampler, and scheduler cleanly.
- **Native `ComfyUI-Model-Manager` `.md` bridge** — reads optional `recipe:` YAML frontmatter blocks from your Model Manager `.md` files (editable right inside ComfyUI Model Manager) and can automatically parse creator recommendations from downloaded CivitAI/HuggingFace `.md` descriptions as a fallback.
- **Scalable central `model_recipes.json`** — supports exact filename matches, glob/wildcard pattern rules (so future finetune versions like `NovaAnimeAMV80` or `anima_turboV12` work automatically), and folder-based family defaults.
- **Top-bar Toggle & VRAM Buttons** — mounts cleanly next to `rgthree` / `Pixaroma` in ComfyUI's top toolbar (`Auto-Apply ON/OFF`, plus 1-click **Unload Models** and **Free Model & Execution Cache** buttons via `/free`).
- **Automatic Secondary Stage Sync** — optionally keeps `Detailer Config` (`KSampler Config (rgthree)`, **60% steps**), `Tiled Upscaler` (`UltimateSDUpscale`, **60% steps**), and `KSampler 2nd Pass` (**50% steps**) aligned to the model's CFG, sampler, and scheduler (never touching `denoise`, and respecting optional explicit `detailer_steps`, `upscale_steps`, and `second_pass_steps` recipe keys).

## Resolution Priority

1. **`ComfyUI-Model-Manager` `.md` YAML Frontmatter** (`recipe:` block beside the model file)
2. **Exact match** in `ComfyUI/user/default/model_recipes.json`
3. **Pattern rules** in `ComfyUI/user/default/model_recipes.json` (e.g. `*nova*29b*`, `*anima*turbo*`)
4. **`.md` description auto-parser** (optional fallback for freshly downloaded models scanned via `ComfyUI-Model-Manager`)
5. **Folder / Family defaults** in `ComfyUI/user/default/model_recipes.json`

## Example `.md` Frontmatter Block (`ComfyUI-Model-Manager`)

```yaml
---
author: example_creator
baseModel: Anima
recipe:
  steps: 25
  cfg: 3.5
  sampler_name: er_sde
  scheduler: simple
  notes: Creator favorite setting
---
```

## Usage

- **Top-bar Flask Button:** Left-click toggles **Auto-Apply ON/OFF**; right-click applies the recipe for the active loader immediately.
- **Right-click on `UNETLoader` / `CheckpointLoaderSimple` / `UnetLoaderGGUF`:**
  - `🧪 Apply Model Recipe now`
  - `💾 Save KSampler → model_recipes.json`
  - `📝 Save KSampler → Model Manager (.md)`
  - `🔄 Reload model_recipes.json`
- **ComfyUI Settings (`🧪 Model Recipes`):** Toggle auto-apply, `.md` description fallback parsing, and toast notifications.

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/newezo-stable/ComfyUI-ModelRecipes.git
```

Restart ComfyUI. On first start, `ComfyUI/user/default/model_recipes.json` is automatically initialized from `default_recipes.json` (and never overwritten on updates).
