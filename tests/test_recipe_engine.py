import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from recipe_engine import (
    RecipeStore,
    parse_md_body_recipe,
    parse_md_frontmatter_recipe,
    write_recipe_to_md_frontmatter,
)

DEFAULT_JSON = os.path.join(REPO_ROOT, "default_recipes.json")


class TestRecipeEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user_json = os.path.join(self.tmp.name, "model_recipes.json")
        self.store = RecipeStore(self.user_json, DEFAULT_JSON)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_anima_recipes(self):
        cases = [
            (r"Anima Turbo\anima_turboV11.safetensors", 10, 1.0, "euler", "simple"),
            (r"Anima\oneObsessionAnima_v50.safetensors", 10, 1.0, "euler", "simple"),
            (r"Anima\hassakuAnima_v11.safetensors", 25, 3.5, "er_sde", "simple"),
            (r"Anima\dasiwaAnima_obsidianArchivesV2.safetensors", 45, 5.0, "euler", "linear_quadratic"),
            (r"Anima\loOmaij_thread002Closer.safetensors", 32, 5.0, "er_sde", "beta"),
            (r"Anima\AnimaNoct_V3_int8_convrot.safetensors", 30, 4.0, "euler", "simple"),
            (r"Anima\UnholyDesireMixDarkSerenity_V3_Anima.safetensors", 18, 1.5, "er_sde", "simple"),
            (r"Anima 29B\Anima-2.9B-preview-v1.safetensors", 32, 4.0, "euler", "sgm_uniform"),
            (r"Anima 29B\oneObsession_anima29BV2.safetensors", 30, 4.5, "euler_ancestral", "normal"),
        ]
        for model, steps, cfg, sampler, sched in cases:
            res = self.store.resolve(model)
            self.assertTrue(res["found"], model)
            self.assertEqual(res["source"], "exact_recipe", model)
            r = res["recipe"]
            self.assertEqual((r["steps"], r["cfg"], r["sampler_name"], r["scheduler"]), (steps, cfg, sampler, sched), model)

    def test_pattern_and_family_resolution(self):
        # Future Nova 2.9B version should match pattern automatically
        res_nova = self.store.resolve(r"Anima 29B\NovaAnimeAMV80.safetensors")
        self.assertEqual(res_nova["source"], "pattern_rule")
        self.assertEqual(res_nova["recipe"]["sampler_name"], "euler_ancestral")
        self.assertEqual(res_nova["recipe"]["scheduler"], "normal")

        # Flux3 Klein must NOT be shadowed by Flux2 Klein
        res_f3 = self.store.resolve(r"Flux3 Klein\flux3-image-klein-int8-convrot.safetensors")
        self.assertEqual(res_f3["recipe"]["steps"], 12)
        res_f2 = self.store.resolve(r"Klein 9B\flux-2-klein-9b-kv-fp8.safetensors")
        self.assertEqual(res_f2["recipe"]["steps"], 4)

        # MiniMax H3 Image & Qwen2.1 & Z-Image
        self.assertEqual(self.store.resolve(r"MiniMax H3\minimax_h3_image_pruned_w4a8.safetensors")["recipe"]["steps"], 6)
        self.assertEqual(self.store.resolve(r"Qwen\NoctQ_V4_int8_convrot.safetensors")["recipe"]["steps"], 25)
        self.assertEqual(self.store.resolve(r"Qwen\Ray Qwen2.1编辑强化 turbo v1 int8convrot.safetensors")["recipe"]["steps"], 8)
        self.assertEqual(self.store.resolve(r"ZIT\cyberrealisticZImage_v90_int8.safetensors")["recipe"]["sampler_name"], "res_multistep")

        # Unknown Anima 2B model falls back to Anima 2B Base default
        res_fb = self.store.resolve(r"Anima\brandNewFinetune_v1.safetensors")
        self.assertEqual(res_fb["source"], "family_default")
        self.assertEqual(res_fb["recipe"]["steps"], 35)
        self.assertEqual(res_fb["recipe"]["cfg"], 4.5)

    def test_model_manager_md_frontmatter_priority_and_roundtrip(self):
        fake_model = os.path.join(self.tmp.name, "hassakuAnima_v11.safetensors")
        fake_md = os.path.join(self.tmp.name, "hassakuAnima_v11.md")
        with open(fake_md, "w", encoding="utf-8") as f:
            f.write("---\nauthor: creator\nbaseModel: Anima\n---\n\n# Trigger Words\n\nNo trigger words\n")

        # Write recipe block into Model Manager .md and verify it preserves body & overrides exact JSON
        write_recipe_to_md_frontmatter(
            fake_md,
            {"steps": 28, "cfg": 4.2, "sampler_name": "euler_ancestral", "scheduler": "beta"},
        )
        with open(fake_md, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# Trigger Words", content)
        self.assertIn("author: creator", content)

        fm = parse_md_frontmatter_recipe(fake_md)
        self.assertEqual(fm["steps"], 28)
        self.assertEqual(fm["cfg"], 4.2)

        res = self.store.resolve(r"Anima\hassakuAnima_v11.safetensors", abs_model_path=fake_model)
        self.assertEqual(res["source"], "model_manager_frontmatter")
        self.assertEqual(res["recipe"]["steps"], 28)

    def test_md_body_auto_parser_fallback(self):
        fake_model = os.path.join(self.tmp.name, "unknownCustomFinetune_v1.safetensors")
        fake_md = os.path.join(self.tmp.name, "unknownCustomFinetune_v1.md")
        with open(fake_md, "w", encoding="utf-8") as f:
            f.write(
                "---\nauthor: test\n---\n\n# Recommended Settings\n"
                "- Sampler: Euler a Normal\n- Steps: 20-30\n- CFG Scale: 4-6\n"
            )
        parsed = parse_md_body_recipe(fake_md)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["steps"], 25)
        self.assertEqual(parsed["cfg"], 5.0)
        self.assertEqual(parsed["sampler_name"], "euler_ancestral")
        self.assertEqual(parsed["scheduler"], "normal")

        res = self.store.resolve(r"Anima\unknownCustomFinetune_v1.safetensors", abs_model_path=fake_model)
        self.assertEqual(res["source"], "model_manager_md_parsed")

    def test_save_exact_recipe(self):
        self.store.save_exact_recipe(
            r"Anima\myNewTune.safetensors",
            {"steps": 22, "cfg": 3.8, "sampler_name": "er_sde", "scheduler": "beta"},
        )
        res = self.store.resolve(r"Anima\myNewTune.safetensors")
        self.assertEqual(res["source"], "exact_recipe")
        self.assertEqual(res["recipe"]["steps"], 22)
        self.assertEqual(res["recipe"]["cfg"], 3.8)


if __name__ == "__main__":
    unittest.main()
