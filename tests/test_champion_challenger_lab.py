import unittest
from src.engines.champion_challenger_lab import ChampionChallengerLab

class TestChampionChallengerLab(unittest.TestCase):
    def setUp(self):
        self.lab = ChampionChallengerLab()

    def test_variants_initialization(self):
        """Verifies default Champion and Challenger variants are loaded."""
        self.assertIn("STRAT_9_CHAMPION", self.lab.variants)
        self.assertIn("STRAT_9_CHALLENGER", self.lab.variants)
        self.assertIn("STRAT_1_CHAMPION", self.lab.variants)
        self.assertIn("STRAT_1_CHALLENGER", self.lab.variants)
        self.assertEqual(self.lab.variants["STRAT_9_CHAMPION"].variant_type, "CHAMPION")
        self.assertEqual(self.lab.variants["STRAT_9_CHALLENGER"].variant_type, "CHALLENGER")

    def test_record_tournament_outcome(self):
        """Verifies trade results update win rate and profit factor accurately."""
        # Record 4 wins and 1 loss for Strategy 9 Challenger
        for _ in range(4):
            self.lab.record_tournament_outcome("STRAT_9_CHALLENGER", is_win=True, r_mult=2.5)
        self.lab.record_tournament_outcome("STRAT_9_CHALLENGER", is_win=False, r_mult=-1.0)

        v = self.lab.variants["STRAT_9_CHALLENGER"]
        self.assertGreaterEqual(v.samples, 5)
        self.assertEqual(v.wins, 4)
        self.assertEqual(v.losses, 1)
        self.assertEqual(v.win_rate, 80.0)
        self.assertGreater(v.profit_factor, 5.0)

    def test_local_mlx_challenger_variant(self):
        """Verifies CHALLENGER_LOCAL_MLX is registered as an official shadow tournament variant."""
        self.assertIn("CHALLENGER_LOCAL_MLX", self.lab.variants)
        v = self.lab.variants["CHALLENGER_LOCAL_MLX"]
        self.assertEqual(v.variant_type, "CHALLENGER")
        self.assertEqual(v.parameters.get("engine"), "mlx-lm")
        self.assertEqual(v.parameters.get("adapter"), "adapters/bayesian-pivot-lora")
        self.assertEqual(v.parameters.get("live_risk"), 0.0)

    def test_local_mlx_tournament_recording(self):
        """Verifies trade resolution updates CHALLENGER_LOCAL_MLX stats correctly."""
        initial_samples = self.lab.variants["CHALLENGER_LOCAL_MLX"].samples
        self.lab.record_tournament_outcome("CHALLENGER_LOCAL_MLX", is_win=True, r_mult=2.5)
        self.lab.record_tournament_outcome("CHALLENGER_LOCAL_MLX", is_win=True, r_mult=2.5)
        self.lab.record_tournament_outcome("CHALLENGER_LOCAL_MLX", is_win=False, r_mult=-1.0)
        v = self.lab.variants["CHALLENGER_LOCAL_MLX"]
        self.assertEqual(v.samples, initial_samples + 3)
        self.assertGreaterEqual(v.wins, 2)
        self.assertGreaterEqual(v.losses, 1)
        self.assertGreater(v.profit_factor, 1.0)

    def test_pattern_mapping_challenger_local_mlx(self):
        """Verifies CounterfactualTracker routes local MLX patterns to variant ID."""
        from src.engines.counterfactual_tracker import CounterfactualTracker
        var_id = CounterfactualTracker._map_pattern_to_variant_id("[CHALLENGER_LOCAL_MLX] TURTLE_SOUP_LIQUIDITY_SWEEP")
        self.assertEqual(var_id, "CHALLENGER_LOCAL_MLX")
        var_id_legacy = CounterfactualTracker._map_pattern_to_variant_id("LOCAL_MLX_SWEEP")
        self.assertEqual(var_id_legacy, "CHALLENGER_LOCAL_MLX")

    def test_local_ollama_retired(self):
        """Verifies CHALLENGER_LOCAL_OLLAMA is retired from active tournament variants."""
        self.assertNotIn("CHALLENGER_LOCAL_OLLAMA", self.lab.variants)

    def test_pattern_mapping_challenger_local_ollama(self):
        """Verifies CounterfactualTracker routes historical local ollama patterns to variant ID."""
        from src.engines.counterfactual_tracker import CounterfactualTracker
        var_id = CounterfactualTracker._map_pattern_to_variant_id("[CHALLENGER_LOCAL_OLLAMA] TURTLE_SOUP_LIQUIDITY_SWEEP")
        self.assertEqual(var_id, "CHALLENGER_LOCAL_OLLAMA")
        var_id_legacy = CounterfactualTracker._map_pattern_to_variant_id("LOCAL_OLLAMA_SWEEP")
        self.assertEqual(var_id_legacy, "CHALLENGER_LOCAL_OLLAMA")

    def test_local_llm_handler_graceful_fallback(self):
        """Verifies LocalLLMHandler handles offline server cleanly without throwing."""
        from src.engines.local_llm_handler import LocalLLMHandler
        # Point to unreachable port to test offline safety
        offline_handler = LocalLLMHandler(
            mlx_url="http://127.0.0.1:9998/v1"
        )
        self.assertFalse(offline_handler.is_available())
        fallback_res = offline_handler._parse_score("invalid json gibberish")
        self.assertEqual(fallback_res["score"], 0.0)
        self.assertEqual(fallback_res["verdict"], "SHADOW_OBSERVATION")

if __name__ == '__main__':
    unittest.main()
