"""Baseline-readiness tests: artifact contract, lineage, metrics, separation.

These prove that a Historical Baseline cannot be produced, named, or scored in
a way that lets a Type-1 model measurement be read as a Type-2 operational
result.  No strategy rule, threshold, or risk parameter is touched, and no
baseline result is collected here.
"""

from datetime import datetime, timezone
import unittest

import pandas as pd

from backtesting.version_b_backtest import VersionBBacktestEngine
from core.baseline_artifact import (
    ARTIFACT_SCHEMA,
    OPERATIONAL_PERFORMANCE,
    STRATEGY_MODEL_BASELINE,
    TYPE_1_STRATEGY_MODEL,
    TYPE_2_OPERATIONAL,
    artifact_fingerprint,
    assert_not_type_1,
    baseline_artifact_filename,
    build_baseline_artifact,
    deterministic_payload,
    validate_baseline_artifact,
    BaselineArtifactError,
)
from core.measurement_lineage import (
    UNRESOLVED,
    build_lineage,
    hash_frames,
    resolve_code_identity,
    validate_lineage,
)
from core.measurement_metrics import (
    compute_equity_curve_max_drawdown,
    compute_model_metrics,
)


NOW = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
DATA_HASH = "a" * 64


class FixtureStrategy:
    """Deterministic strategy double; proves wiring, never signal quality."""

    def __init__(self):
        self.calls = 0

    def check_buy_signal(self, df_1h, df_15m, df_5m):
        self.calls += 1
        if self.calls != 1:
            return False, {"reason": "fixture"}
        return True, {"signal": "BUY", "entry_price": 100.0, "atr": 1.0}

    def validate_signal(self, df_1h, df_15m, df_5m, signal):
        return True, {"score_result": {"total_score": 100.0}}

    def calculate_exits(self, entry_price, atr):
        return {
            "valid": True,
            "stop_loss": entry_price - 2,
            "take_profit_1": entry_price + 2,
            "take_profit_2": entry_price + 4,
        }

    def calculate_position_size(self, **kwargs):
        return {"contract_size": 1.0, "leverage": 1.0}


def make_frames(tp1_high: float = 102.0, tp2_high: float = 104.5):
    end = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    one_hour = pd.date_range(end=end, periods=220, freq="1h")
    fifteen = pd.date_range(end=end, periods=880, freq="15min")
    five = pd.date_range(end=end, periods=2640, freq="5min")

    def frame(index, highs=None):
        highs = highs or {}
        values = [highs.get(i, 100.2) for i in range(len(index))]
        return pd.DataFrame(
            {"open": 100.0, "high": values, "low": 100.1, "close": 100.0}, index=index
        )

    entry_idx = int((one_hour[200] - five[0]).total_seconds() / 300)
    highs = {entry_idx + 1: tp1_high, entry_idx + 4: tp2_high}
    return frame(one_hour), frame(fifteen), frame(five, highs)


def sample_lineage(**overrides):
    lineage = build_lineage(
        code={"identity_version": "vb-code-identity-1", "commit": "b" * 40, "dirty": False, "branch": "test"},
        data_hash=DATA_HASH,
        frame_row_counts={"1h": 220, "15m": 880, "5m": 2640},
        config={"config_sha256": "c" * 64},
        execution_model_version="vb-1.0-5m-stop-first",
        strategy_version="1.3",
        symbol="BTC/USDT",
        direction="long",
        initial_balance=1000.0,
        universe=["BTC/USDT"],
        timeframes={"decision": "15m", "exit": "5m"},
    )
    for key, value in overrides.items():
        if key == "data_hash":
            lineage["data"]["data_hash"] = value
        elif key == "config_sha256":
            lineage["config"]["config_sha256"] = value
        else:
            lineage[key] = value
    return lineage


def sample_metrics():
    return compute_model_metrics(
        trades=[{"profit": 32.5, "exit_type": "TAKE_PROFIT_2", "fees": 0.0}],
        equity_curve=[1000.0, 1000.0, 1032.5],
        initial_balance=1000.0,
    )


class LineageTests(unittest.TestCase):
    def test_code_identity_is_a_real_commit_not_a_placeholder(self):
        identity = resolve_code_identity()
        self.assertEqual(identity["identity_version"], "vb-code-identity-1")
        commit = identity["commit"]
        self.assertNotEqual(commit, UNRESOLVED)
        self.assertNotEqual(commit, "working-tree")
        self.assertEqual(len(commit), 40)
        self.assertTrue(all(c in "0123456789abcdef" for c in commit))

    def test_data_hash_is_deterministic_and_frame_order_independent(self):
        one_hour, fifteen, five = make_frames()
        first = hash_frames({"1h": one_hour, "15m": fifteen, "5m": five})
        second = hash_frames({"5m": five, "1h": one_hour, "15m": fifteen})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_data_hash_changes_when_the_data_changes(self):
        one_hour, fifteen, five = make_frames()
        altered = five.copy()
        altered.iloc[10, altered.columns.get_loc("high")] = 999.0
        self.assertNotEqual(
            hash_frames({"1h": one_hour, "15m": fifteen, "5m": five}),
            hash_frames({"1h": one_hour, "15m": fifteen, "5m": altered}),
        )

    def test_lineage_rejects_an_unresolved_commit(self):
        lineage = sample_lineage()
        lineage["code"] = dict(lineage["code"], commit=UNRESOLVED)
        with self.assertRaisesRegex(ValueError, "not reproducible"):
            validate_lineage(lineage)

    def test_lineage_rejects_a_dirty_tree_in_strict_mode(self):
        lineage = sample_lineage()
        lineage["code"] = dict(lineage["code"], dirty=True)
        with self.assertRaisesRegex(ValueError, "dirty"):
            validate_lineage(lineage)
        # Relaxation is explicit and visible, never silent.
        validate_lineage(lineage, require_clean_tree=False)

    def test_lineage_rejects_a_malformed_data_hash(self):
        for bad in (None, "", UNRESOLVED, "abc123", "Z" * 64):
            with self.assertRaises(ValueError):
                validate_lineage(sample_lineage(data_hash=bad))

    def test_lineage_rejects_a_missing_config_identity(self):
        lineage = sample_lineage(config_sha256="UNSPECIFIED")
        with self.assertRaisesRegex(ValueError, "config identity"):
            validate_lineage(lineage)


class MetricDefinitionTests(unittest.TestCase):
    def test_max_drawdown_matches_a_hand_computed_value(self):
        # peak 120 -> trough 90 is a 25% decline; the later recovery is not a drawdown.
        result = compute_equity_curve_max_drawdown(
            [100.0, 120.0, 90.0, 110.0], initial_balance=100.0
        )
        self.assertAlmostEqual(result["equity_curve_max_drawdown_pct"], 25.0)
        self.assertEqual(result["equity_curve_peak_index"], 1)
        self.assertEqual(result["equity_curve_trough_index"], 2)
        self.assertAlmostEqual(result["equity_curve_end"], 110.0)

    def test_a_monotonic_curve_has_zero_drawdown(self):
        result = compute_equity_curve_max_drawdown([100.0, 101.0, 102.0], initial_balance=100.0)
        self.assertEqual(result["equity_curve_max_drawdown_pct"], 0.0)

    def test_drawdown_refuses_an_empty_curve_or_bad_balance(self):
        with self.assertRaises(ValueError):
            compute_equity_curve_max_drawdown([], initial_balance=100.0)
        with self.assertRaises(ValueError):
            compute_equity_curve_max_drawdown([100.0], initial_balance=0.0)

    def test_profit_factor_is_none_when_there_are_no_losses(self):
        metrics = compute_model_metrics(
            trades=[{"profit": 10.0, "exit_type": "TAKE_PROFIT_2"}],
            equity_curve=[100.0, 110.0],
            initial_balance=100.0,
        )
        self.assertIsNone(metrics["model_profit_factor"])
        self.assertEqual(metrics["model_trade_count"], 1)

    def test_forced_end_of_data_exits_are_counted_separately(self):
        metrics = compute_model_metrics(
            trades=[
                {"profit": 10.0, "exit_type": "TAKE_PROFIT_2"},
                {"profit": -5.0, "exit_type": "END_OF_DATA"},
            ],
            equity_curve=[100.0, 110.0, 105.0],
            initial_balance=100.0,
        )
        self.assertEqual(metrics["model_forced_exit_count"], 1)
        self.assertEqual(metrics["model_exit_reason_distribution"]["END_OF_DATA"], 1)
        self.assertAlmostEqual(metrics["model_net_pnl"], 5.0)


class ArtifactContractTests(unittest.TestCase):
    def build(self, **overrides):
        kwargs = dict(
            metrics=sample_metrics(),
            lineage=sample_lineage(),
            execution_model_version="vb-1.0-5m-stop-first",
            counts={"accepted_signals": 1},
            generated_at=NOW,
            require_clean_tree=False,
        )
        kwargs.update(overrides)
        return build_baseline_artifact(**kwargs)

    def test_a_valid_artifact_is_self_labelling(self):
        artifact = self.build()
        self.assertEqual(artifact["artifact_schema"], ARTIFACT_SCHEMA)
        self.assertEqual(artifact["report_type"], STRATEGY_MODEL_BASELINE)
        self.assertEqual(artifact["measurement_scope"], TYPE_1_STRATEGY_MODEL)
        self.assertIs(artifact["not_an_operational_result"], True)
        self.assertIsNone(artifact["profitability_verdict"])
        name = baseline_artifact_filename(artifact)
        self.assertTrue(name.startswith("baseline_strategymodel_BTCUSDT_long_vb-1.0-5m-stop-first_"))
        for term in ("performance", "operational", "live"):
            self.assertNotIn(term, name.lower())

    def test_an_operational_verdict_key_is_refused(self):
        for key in ("final_verdict", "win_rate", "max_drawdown_pct", "verdict", "criteria"):
            with self.assertRaises(BaselineArtifactError, msg=key):
                self.build(counts={key: "PASS"})

    def test_a_profitability_verdict_is_refused(self):
        artifact = self.build()
        artifact["profitability_verdict"] = "PASS"
        with self.assertRaisesRegex(BaselineArtifactError, "profitability verdict"):
            validate_baseline_artifact(artifact, require_clean_tree=False)

    def test_unpinned_metric_keys_are_refused(self):
        metrics = sample_metrics()
        metrics["sharpe_ratio"] = 1.0
        with self.assertRaisesRegex(BaselineArtifactError, "outside the pinned set"):
            self.build(metrics=metrics)

    def test_a_missing_pinned_metric_is_refused(self):
        metrics = sample_metrics()
        del metrics["model_forced_exit_count"]
        with self.assertRaisesRegex(BaselineArtifactError, "missing pinned keys"):
            self.build(metrics=metrics)

    def test_an_operational_scope_cannot_masquerade_as_a_baseline(self):
        artifact = self.build()
        artifact["measurement_scope"] = TYPE_2_OPERATIONAL
        artifact["report_type"] = OPERATIONAL_PERFORMANCE
        with self.assertRaises(BaselineArtifactError):
            validate_baseline_artifact(artifact, require_clean_tree=False)

    def test_undeclared_nondeterminism_is_refused(self):
        artifact = self.build()
        artifact["non_deterministic_fields"] = ["generated_at", "metrics"]
        with self.assertRaisesRegex(BaselineArtifactError, "not permitted"):
            validate_baseline_artifact(artifact, require_clean_tree=False)

    def test_a_silently_aggregated_universe_is_refused(self):
        artifact = self.build()
        artifact["lineage"]["run_scope"]["universe"] = ["BTC/USDT", "ETH/USDT"]
        with self.assertRaisesRegex(BaselineArtifactError, "per-symbol baseline"):
            validate_baseline_artifact(artifact, require_clean_tree=False)

    def test_a_baseline_artifact_cannot_be_consumed_as_operational(self):
        artifact = self.build()
        with self.assertRaises(BaselineArtifactError):
            assert_not_type_1(artifact)

    def test_the_legacy_report_is_typed_and_carries_no_bare_verdict(self):
        from performance_report import SUCCESS_CRITERIA, assert_not_baseline_artifact, build_report

        trades = pd.DataFrame({
            "pnl": [10.0, -4.0, 6.0],
            "side": ["long", "long", "short"],
            "symbol": ["BTC/USDT"] * 3,
            "exit_reason": ["TAKE_PROFIT_2", "STOP_LOSS", "TAKE_PROFIT_1"],
        })
        report = build_report(trades, signals_count=3, criteria=SUCCESS_CRITERIA)
        self.assertEqual(report["report_type"], "LEGACY_OPERATIONAL_SUMMARY")
        self.assertEqual(report["measurement_scope"], "TYPE_2_OPERATIONAL_LEGACY")
        self.assertIs(report["not_a_version_b_measurement"], True)
        self.assertNotIn("final_verdict", report, "the ambiguous bare verdict is gone")
        self.assertIn("legacy_operational_verdict", report)
        self.assertTrue(report["legacy_operational_verdict"].startswith("LEGACY_OPERATIONAL_"))
        # The legacy path still refuses a Version B artifact.
        with self.assertRaises(BaselineArtifactError):
            assert_not_baseline_artifact(self.build())


class ReplayLineageTests(unittest.TestCase):
    def run_engine(self, **frames):
        engine = VersionBBacktestEngine(
            initial_balance=1000,
            fee_rate=0.0,
            slippage_rate=0.0,
            strategy=FixtureStrategy(),
        )
        one_hour, fifteen, five = make_frames(**frames)
        report = engine.run(one_hour, fifteen, five, symbol="BTC/USDT")
        return engine, report

    def test_the_run_records_real_lineage_not_placeholders(self):
        engine, report = self.run_engine()
        self.assertEqual(report["status"], "completed")
        self.assertNotEqual(report["code_identity"]["commit"], "working-tree")
        self.assertNotEqual(report["code_identity"]["commit"], UNRESOLVED)
        self.assertEqual(len(report["code_identity"]["commit"]), 40)
        self.assertEqual(len(report["data_hash"]), 64)
        self.assertNotEqual(report["config_hash"], "UNSPECIFIED")
        self.assertEqual(report["frame_row_counts"], {"1h": 220, "15m": 880, "5m": 2640})
        self.assertIn("equity_curve_max_drawdown_pct", report)

    def test_lineage_validates_and_builds_an_accepted_artifact(self):
        engine, report = self.run_engine()
        lineage = engine.measurement_lineage(symbol="BTC/USDT", direction="long")
        validate_lineage(lineage, require_clean_tree=False)
        artifact = build_baseline_artifact(
            metrics=report["model_metrics"],
            lineage=lineage,
            execution_model_version=report["execution_model_version"],
            counts={
                "accepted_signals": report["accepted_signals"],
                "rejected_signals": report["rejected_signals"],
                "data_rejections": report["data_rejections"],
            },
            generated_at=NOW,
            require_clean_tree=False,
        )
        validate_baseline_artifact(artifact, require_clean_tree=False)
        self.assertEqual(artifact["metrics"]["model_trade_count"], report["total_trades"])

    def test_trade_rows_expose_fees_so_costs_are_not_silently_zero(self):
        """`model_cost_total` reads `fees`; a missing key would read as zero."""
        engine, report = self.run_engine()
        self.assertEqual(report["total_trades"], 1)
        self.assertIn("fees", report["trades"][0])
        self.assertIn("slippage", report["trades"][0])

        metrics = compute_model_metrics(
            trades=[{"profit": 30.0, "exit_type": "TAKE_PROFIT_2", "fees": 2.5}],
            equity_curve=[1000.0, 1030.0],
            initial_balance=1000.0,
        )
        self.assertAlmostEqual(metrics["model_cost_total"], 2.5)

        silent = compute_model_metrics(
            trades=[{"profit": 30.0, "exit_type": "TAKE_PROFIT_2"}],
            equity_curve=[1000.0, 1030.0],
            initial_balance=1000.0,
        )
        self.assertEqual(silent["model_cost_total"], 0.0, "documents why the key must exist")

    def test_measurement_lineage_requires_a_completed_run(self):
        engine = VersionBBacktestEngine(
            initial_balance=1000, fee_rate=0.0, slippage_rate=0.0, strategy=FixtureStrategy()
        )
        with self.assertRaisesRegex(ValueError, "completed run"):
            engine.measurement_lineage(symbol="BTC/USDT", direction="long")


class ReproducibilityTests(unittest.TestCase):
    def artifact_for(self, generated_at=NOW, **frames):
        engine = VersionBBacktestEngine(
            initial_balance=1000, fee_rate=0.0, slippage_rate=0.0, strategy=FixtureStrategy()
        )
        one_hour, fifteen, five = make_frames(**frames)
        report = engine.run(one_hour, fifteen, five, symbol="BTC/USDT")
        lineage = engine.measurement_lineage(symbol="BTC/USDT", direction="long")
        return build_baseline_artifact(
            metrics=report["model_metrics"],
            lineage=lineage,
            execution_model_version=report["execution_model_version"],
            generated_at=generated_at,
            require_clean_tree=False,
        )

    def test_identical_inputs_produce_an_identical_fingerprint(self):
        first = self.artifact_for(generated_at=NOW)
        second = self.artifact_for(generated_at=LATER)
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(artifact_fingerprint(first, require_clean_tree=False), artifact_fingerprint(second, require_clean_tree=False))

    def test_generated_at_is_the_only_declared_nondeterministic_field(self):
        artifact = self.artifact_for()
        self.assertEqual(artifact["non_deterministic_fields"], ["generated_at"])
        payload = deterministic_payload(artifact, require_clean_tree=False)
        self.assertNotIn("generated_at", payload)
        self.assertIn("metrics", payload)
        self.assertIn("lineage", payload)

    def test_different_data_produces_a_different_fingerprint(self):
        # Never reaching TP1/TP2 leaves the position to be force-closed.
        first = self.artifact_for()
        second = self.artifact_for(tp1_high=100.5, tp2_high=100.6)
        self.assertNotEqual(
            first["lineage"]["data"]["data_hash"], second["lineage"]["data"]["data_hash"]
        )
        self.assertNotEqual(artifact_fingerprint(first, require_clean_tree=False), artifact_fingerprint(second, require_clean_tree=False))
        self.assertGreater(second["metrics"]["model_forced_exit_count"], 0)


if __name__ == "__main__":
    unittest.main()
