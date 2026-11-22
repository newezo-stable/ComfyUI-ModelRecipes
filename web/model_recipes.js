import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

// =============================================================================
// ComfyUI-ModelRecipes — Auto-apply per-model recipes directly onto native
// KSampler widgets when changing models in UNETLoader / CheckpointLoaderSimple.
//
// Design goals:
//  - Zero extra nodes in workflows (100% SaveImageWithMetaData compatible!)
//  - ComfyUI-Model-Manager .md frontmatter & description bridge
//  - Top-bar toggle button mounted beside Pixaroma & rgthree settings
// =============================================================================

const SETTING_AUTO_APPLY = "ModelRecipes.AutoApply";
const SETTING_PARSE_MD = "ModelRecipes.ParseModelManagerMd";
const SETTING_SHOW_TOAST = "ModelRecipes.ShowToasts";

const LOADER_WIDGETS = {
  UNETLoader: { widget: "unet_name", folder: "diffusion_models" },
  CheckpointLoaderSimple: { widget: "ckpt_name", folder: "checkpoints" },
  UnetLoaderGGUF: { widget: "unet_name", folder: "unet_gguf" },
};

const SOURCE_LABELS = {
  model_manager_frontmatter: "Model Manager .md",
  exact_recipe: "Exact Recipe",
  pattern_rule: "Pattern Rule",
  model_manager_md_parsed: ".md Auto-Parsed",
  family_default: "Family Default",
};

const state = {
  autoApply: true,
  parseMd: true,
  showToasts: true,
  configuring: false,
  configureGuardUntil: 0,
  toolbarBtn: null,
};

// Crisp inline SVG flask/recipe icon (18x18)
const FLASK_SVG = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events:none;display:block;"><path d="M9 3h6"/><path d="M10 3v6.5L4.8 18.2A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.8L14 9.5V3"/><path d="M8.5 14h7"/></svg>`;

function injectCSS() {
  if (document.getElementById("comfyui-model-recipes-css")) return;
  const style = document.createElement("style");
  style.id = "comfyui-model-recipes-css";
  style.textContent = `
    .model-recipes-btn:not(.model-recipes-on) {
      background-color: #2a2c2e !important;
      color: #bbb !important;
      border-color: #444 !important;
    }
    .model-recipes-btn:not(.model-recipes-on):hover {
      background-color: #3a3d40 !important;
      color: #fff !important;
    }
    .model-recipes-btn.model-recipes-on {
      background-color: #2b7a4b !important;
      color: #fff !important;
      border-color: #36995e !important;
    }
    .model-recipes-btn.model-recipes-on:hover {
      background-color: #2b7a4b !important;
      filter: brightness(1.1);
    }
    .model-recipes-toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 99999;
      max-width: 440px;
      padding: 10px 14px;
      border-radius: 8px;
      background: rgba(24, 28, 34, 0.96);
      color: #f0f6fc;
      border: 1px solid #36995e;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 13px;
      line-height: 1.4;
      pointer-events: none;
      opacity: 0;
      transform: translateY(8px);
      transition: opacity 180ms ease, transform 180ms ease;
    }
    .model-recipes-toast.visible {
      opacity: 1;
      transform: translateY(0);
    }
    .model-recipes-toast.warn {
      border-color: #d29922;
    }
    .model-recipes-toast .mr-title {
      font-weight: 600;
      margin-bottom: 2px;
    }
    .model-recipes-toast .mr-sub {
      color: #9fb0c2;
      font-size: 12px;
    }
  `;
  document.head.appendChild(style);
}

let toastTimer = null;
function showToast(title, detail = "", severity = "info") {
  if (!state.showToasts && severity === "info") return;

  // Prefer ComfyUI's native PrimeVue toast if exposed
  if (app.extensionManager?.toast?.add) {
    try {
      app.extensionManager.toast.add({
        severity: severity === "warn" ? "warn" : severity === "error" ? "error" : "success",
        summary: title,
        detail,
        life: 4000,
      });
      return;
    } catch (_) {
      // fall through to DOM toast
    }
  }

  injectCSS();
  let el = document.getElementById("comfyui-model-recipes-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "comfyui-model-recipes-toast";
    el.className = "model-recipes-toast";
    document.body.appendChild(el);
  }
  el.classList.toggle("warn", severity === "warn" || severity === "error");
  el.innerHTML = `<div class="mr-title">${escapeHtml(title)}</div>${detail ? `<div class="mr-sub">${escapeHtml(detail)}</div>` : ""}`;
  requestAnimationFrame(() => el.classList.add("visible"));
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), 4000);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function updateToolbarButton() {
  const btn = state.toolbarBtn;
  if (!btn) return;
  btn.classList.toggle("model-recipes-on", state.autoApply);
  btn.title = state.autoApply
    ? "Model Recipes: ON — Auto-applies Steps/CFG/Sampler/Scheduler on model switch (Click to toggle · Right-click to apply now)"
    : "Model Recipes: OFF — Click to enable auto-apply on model switch (Right-click to apply manually)";
}

function toggleAutoApply() {
  const settings = app.ui?.settings;
  const next = !state.autoApply;
  state.autoApply = next;
  if (settings) {
    try {
      settings.setSettingValue(SETTING_AUTO_APPLY, next);
    } catch (_) {}
  }
  updateToolbarButton();
  showToast(
    next ? "Model Recipes: ON" : "Model Recipes: OFF",
    next
      ? "Switching models in UNETLoader / CheckpointLoader will auto-apply recipes."
      : "Auto-apply paused. Right-click a model loader to apply manually."
  );
}

function mountToolbarButton() {
  if (state.toolbarBtn?.isConnected) return;
  const settingsGroupEl = app.menu?.settingsGroup?.element;
  if (!settingsGroupEl) {
    mountToolbarButton._tries = (mountToolbarButton._tries || 0) + 1;
    if (mountToolbarButton._tries <= 25) {
      setTimeout(mountToolbarButton, 250);
    }
    return;
  }

  injectCSS();

  const btn = document.createElement("button");
  btn.className = "comfyui-button model-recipes-btn";
  btn.innerHTML = FLASK_SVG;
  btn.addEventListener("click", toggleAutoApply);
  btn.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    applyForActiveCanvasLoader();
  });

  const group = document.createElement("div");
  group.className = "comfyui-button-group model-recipes-group";
  group.appendChild(btn);

  settingsGroupEl.before(group);
  state.toolbarBtn = btn;
  updateToolbarButton();
}

// -----------------------------------------------------------------------------
// Graph traversal: find the primary active KSampler downstream of a model loader
// -----------------------------------------------------------------------------
function getWidget(node, name) {
  return node?.widgets?.find((w) => w.name === name) || null;
}

function isPrimarySamplerCandidate(node) {
  if (!node) return false;
  if (node.mode === 2 || node.mode === 4) return false; // skip muted/bypassed
  if (node.type !== "KSampler" && node.type !== "KSamplerAdvanced") return false;
  const title = (node.title || "").toLowerCase();
  if (title.includes("2nd pass") || title.includes("refiner") || title.includes("hires")) {
    return false;
  }
  return true;
}

function findDownstreamNodes(startNode, maxDepth = 18) {
  const graph = app.graph;
  if (!graph || !startNode) return [];
  const visited = new Set([startNode.id]);
  const queue = [{ node: startNode, depth: 0 }];
  const found = [];

  while (queue.length > 0) {
    const { node, depth } = queue.shift();
    if (depth > maxDepth) continue;
    const outputs = node.outputs || [];
    for (const out of outputs) {
      const links = out.links || [];
      for (const lid of links) {
        const link = graph.links?.[lid] ?? graph.links?.get?.(lid);
        if (!link) continue;
        const nextNode = graph.getNodeById(link.target_id);
        if (!nextNode || visited.has(nextNode.id)) continue;
        visited.add(nextNode.id);
        found.push(nextNode);
        queue.push({ node: nextNode, depth: depth + 1 });
      }
    }
  }
  return found;
}

function findPrimaryKSampler(loaderNode) {
  const downstream = findDownstreamNodes(loaderNode);
  const candidates = downstream.filter(isPrimarySamplerCandidate);
  if (candidates.length > 0) {
    // Prefer denoise >= 0.99 (base generation pass)
    const basePass = candidates.find((n) => {
      const w = getWidget(n, "denoise");
      return !w || Number(w.value) >= 0.99;
    });
    return basePass || candidates[0];
  }

  // Fallback: first active KSampler anywhere on canvas
  const allNodes = app.graph?._nodes || [];
  const fallback = allNodes.filter(isPrimarySamplerCandidate);
  return fallback[0] || null;
}

function findModularSamplerTargets(loaderNode) {
  // Support SamplerCustomAdvanced pipelines (KSamplerSelect + BasicScheduler)
  const downstream = findDownstreamNodes(loaderNode);
  const samplerSelect = downstream.find((n) => n.type === "KSamplerSelect" && n.mode === 0);
  const basicScheduler = downstream.find((n) => n.type === "BasicScheduler" && n.mode === 0);
  const cfgGuider = downstream.find((n) => n.type === "CFGGuider" && n.mode === 0);
  if (!samplerSelect && !basicScheduler) return null;
  return { samplerSelect, basicScheduler, cfgGuider };
}

function setWidgetSafe(node, widgetName, value) {
  if (value === undefined || value === null) return false;
  const w = getWidget(node, widgetName);
  if (!w) return false;

  // Validate combo widget options before assigning
  const vals = w.options?.values;
  if (Array.isArray(vals) && vals.length > 0 && !vals.includes(value)) {
    return false;
  }

  w.value = value;
  if (typeof w.callback === "function") {
    try {
      w.callback(w.value, app.canvas, node, [0, 0], null);
    } catch (_) {}
  }
  return true;
}

function readSamplerValues(loaderNode) {
  const ks = findPrimaryKSampler(loaderNode);
  if (ks) {
    return {
      steps: Number(getWidget(ks, "steps")?.value),
      cfg: Number(getWidget(ks, "cfg")?.value),
      sampler_name: String(getWidget(ks, "sampler_name")?.value || ""),
      scheduler: String(getWidget(ks, "scheduler")?.value || ""),
    };
  }
  const mod = findModularSamplerTargets(loaderNode);
  if (mod) {
    const out = {};
    if (mod.basicScheduler) {
      out.steps = Number(getWidget(mod.basicScheduler, "steps")?.value);
      out.scheduler = String(getWidget(mod.basicScheduler, "scheduler")?.value || "");
    }
    if (mod.samplerSelect) {
      out.sampler_name = String(getWidget(mod.samplerSelect, "sampler_name")?.value || "");
    }
    if (mod.cfgGuider) {
      out.cfg = Number(getWidget(mod.cfgGuider, "cfg")?.value);
    }
    return out;
  }
  return null;
}

// -----------------------------------------------------------------------------
// API calls: Resolve & Save
// -----------------------------------------------------------------------------
async function applyRecipeForLoader(loaderNode, manualTrigger = false) {
  const spec = LOADER_WIDGETS[loaderNode?.type];
  if (!spec) return;
  const modelWidget = getWidget(loaderNode, spec.widget);
  const modelName = modelWidget?.value;
  if (!modelName) return;

  try {
    const params = new URLSearchParams({
      model: String(modelName),
      folder: spec.folder,
      parse_md: state.parseMd ? "true" : "false",
    });
    const resp = await api.fetchApi(`/model-recipes/resolve?${params.toString()}`);
    if (!resp.ok) return;
    const data = await resp.json();

    if (!data.found || !data.recipe) {
      if (manualTrigger) {
        showToast(
          "No recipe found",
          `No preset for ${modelName}. Right-click loader to save current KSampler as recipe.`,
          "warn"
        );
      }
      return;
    }

    const r = data.recipe;
    const applied = [];
    const ks = findPrimaryKSampler(loaderNode);

    if (ks) {
      if (setWidgetSafe(ks, "steps", r.steps)) applied.push(`${r.steps} Steps`);
      if (setWidgetSafe(ks, "cfg", r.cfg)) applied.push(`CFG ${r.cfg}`);
      if (setWidgetSafe(ks, "sampler_name", r.sampler_name)) applied.push(r.sampler_name);
      if (setWidgetSafe(ks, "scheduler", r.scheduler)) applied.push(r.scheduler);
    } else {
      const mod = findModularSamplerTargets(loaderNode);
      if (mod) {
        if (mod.basicScheduler && setWidgetSafe(mod.basicScheduler, "steps", r.steps)) applied.push(`${r.steps} Steps`);
        if (mod.cfgGuider && setWidgetSafe(mod.cfgGuider, "cfg", r.cfg)) applied.push(`CFG ${r.cfg}`);
        if (mod.samplerSelect && setWidgetSafe(mod.samplerSelect, "sampler_name", r.sampler_name)) applied.push(r.sampler_name);
        if (mod.basicScheduler && setWidgetSafe(mod.basicScheduler, "scheduler", r.scheduler)) applied.push(r.scheduler);
      }
    }

    if (applied.length === 0) {
      if (manualTrigger) {
        showToast("Recipe found, no active KSampler", `Matched: ${r.label || data.matched_key}`, "warn");
      }
      return;
    }

    app.graph?.setDirtyCanvas?.(true, true);

    const srcLabel = SOURCE_LABELS[data.source] || data.source;
    const title = `Recipe (${srcLabel}): ${r.label || data.matched_key}`;
    const summary = applied.join(" · ") + (r.notes ? ` — ${r.notes}` : "");
    showToast(title, summary, "info");
  } catch (err) {
    console.warn("[ModelRecipes] Failed to resolve recipe:", err);
  }
}

async function saveRecipeFromLoader(loaderNode, target = "json") {
  const spec = LOADER_WIDGETS[loaderNode?.type];
  if (!spec) return;
  const modelName = getWidget(loaderNode, spec.widget)?.value;
  if (!modelName) return;

  const vals = readSamplerValues(loaderNode);
  if (!vals || (!vals.steps && !vals.cfg)) {
    showToast("Cannot save recipe", "No active KSampler found downstream of this loader.", "warn");
    return;
  }

  try {
    const resp = await api.fetchApi("/model-recipes/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: String(modelName),
        folder: spec.folder,
        target,
        recipe: {
          steps: vals.steps,
          cfg: vals.cfg,
          sampler_name: vals.sampler_name,
          scheduler: vals.scheduler,
        },
      }),
    });
    const res = await resp.json();
    if (!resp.ok || !res.ok) {
      showToast("Save failed", res.error || "Unknown error", "error");
      return;
    }
    const where = target === "md" ? "Model Manager .md frontmatter" : "model_recipes.json";
    showToast(
      `Saved Recipe → ${where}`,
      `${modelName}: ${vals.steps} Steps · CFG ${vals.cfg} · ${vals.sampler_name} / ${vals.scheduler}`
    );
  } catch (err) {
    showToast("Save failed", String(err), "error");
  }
}

function applyForActiveCanvasLoader() {
  const nodes = app.graph?._nodes || [];
  const loaders = nodes.filter((n) => LOADER_WIDGETS[n.type] && n.mode === 0);
  if (loaders.length === 0) {
    showToast("No active model loader found", "Place or enable a UNETLoader / CheckpointLoaderSimple.", "warn");
    return;
  }
  for (const loader of loaders) {
    applyRecipeForLoader(loader, true);
  }
}

function hookLoaderNode(node) {
  const spec = LOADER_WIDGETS[node?.type];
  if (!spec || node._modelRecipesHooked) return;
  const w = getWidget(node, spec.widget);
  if (!w) return;

  node._modelRecipesHooked = true;
  node._lastModelRecipeVal = w.value;

  const maybeTrigger = () => {
    const now = Date.now();
    const cur = w.value;
    if (cur === node._lastModelRecipeVal) return;
    node._lastModelRecipeVal = cur;
    if (state.autoApply && !state.configuring && now >= state.configureGuardUntil) {
      applyRecipeForLoader(node, false);
    }
  };

  const origCb = w.callback;
  w.callback = function (...args) {
    const res = origCb ? origCb.apply(this, args) : undefined;
    maybeTrigger();
    return res;
  };

  const origWidgetChanged = node.onWidgetChanged;
  node.onWidgetChanged = function (name, value, oldVal, widget) {
    const res = origWidgetChanged ? origWidgetChanged.apply(this, arguments) : undefined;
    if (name === spec.widget) {
      maybeTrigger();
    }
    return res;
  };
}

// -----------------------------------------------------------------------------
// Extension Registration
// -----------------------------------------------------------------------------
app.registerExtension({
  name: "ComfyUI.ModelRecipes",
  settings: [
    {
      id: SETTING_AUTO_APPLY,
      name: "Auto-apply Model Recipes on model switch",
      type: "boolean",
      defaultValue: true,
      category: ["🧪 Model Recipes", "General"],
      tooltip: "Automatically update KSampler steps, CFG, sampler and scheduler when changing models in UNETLoader / CheckpointLoaderSimple.",
      onChange: (v) => {
        state.autoApply = !!v;
        updateToolbarButton();
      },
    },
    {
      id: SETTING_PARSE_MD,
      name: "Parse ComfyUI-Model-Manager .md descriptions as fallback",
      type: "boolean",
      defaultValue: true,
      category: ["🧪 Model Recipes", "Model Manager Bridge"],
      tooltip: "If no explicit recipe exists in frontmatter or model_recipes.json, attempt to parse creator recommendations from the Model Manager .md file.",
      onChange: (v) => {
        state.parseMd = !!v;
      },
    },
    {
      id: SETTING_SHOW_TOAST,
      name: "Show toast notification when recipe is applied",
      type: "boolean",
      defaultValue: true,
      category: ["🧪 Model Recipes", "UI"],
      onChange: (v) => {
        state.showToasts = !!v;
      },
    },
  ],

  async setup() {
    const s = app.ui?.settings;
    if (s) {
      state.autoApply = s.getSettingValue(SETTING_AUTO_APPLY) ?? true;
      state.parseMd = s.getSettingValue(SETTING_PARSE_MD) ?? true;
      state.showToasts = s.getSettingValue(SETTING_SHOW_TOAST) ?? true;
    }
    mountToolbarButton();
  },

  beforeConfigureGraph() {
    state.configuring = true;
  },

  afterConfigureGraph() {
    state.configuring = false;
    state.configureGuardUntil = Date.now() + 300;
    for (const node of app.graph?._nodes || []) {
      hookLoaderNode(node);
      const spec = LOADER_WIDGETS[node?.type];
      if (spec) {
        const w = getWidget(node, spec.widget);
        if (w) node._lastModelRecipeVal = w.value;
      }
    }
  },

  nodeCreated(node) {
    hookLoaderNode(node);
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!LOADER_WIDGETS[nodeData.name]) return;
    const origGetExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      if (origGetExtraMenuOptions) {
        origGetExtraMenuOptions.apply(this, arguments);
      }
      options.push(
        null,
        {
          content: "🧪 Apply Model Recipe now",
          callback: () => applyRecipeForLoader(this, true),
        },
        {
          content: "💾 Save KSampler → model_recipes.json",
          callback: () => saveRecipeFromLoader(this, "json"),
        },
        {
          content: "📝 Save KSampler → Model Manager (.md)",
          callback: () => saveRecipeFromLoader(this, "md"),
        },
        {
          content: "🔄 Reload model_recipes.json",
          callback: async () => {
            try {
              const r = await api.fetchApi("/model-recipes/reload", { method: "POST" });
              const j = await r.json();
              showToast("Recipes reloaded", `${j.exact_count} exact · ${j.pattern_count} patterns`);
            } catch (e) {
              showToast("Reload failed", String(e), "error");
            }
          },
        }
      );
    };
  },
});
