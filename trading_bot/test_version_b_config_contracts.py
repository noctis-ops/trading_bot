"""Phase 1 tests: config reproducibility and Version B domain contracts."""

from datetime import datetime, timezone
from pathlib import Path
import unittest

from core.contracts import DecisionContext, EventRecord, Fill, RiskSnapshot
from core.decision_taxonomy import DecisionReason, DecisionStage, taxonomy_record
from core.runtime_config import ConfigError, load_runtime_config


CONFIG = Path(__file__).parent / "config.yaml"


class VersionBConfigContractTests(unittest.TestCase):
    def test_runtime_config_resolves_environment_without_activating_dormant_volume_multiplier(self):
        cfg = load_runtime_config(
            CONFIG,
            environ={"TRADING_MODE": "paper", "PAPER_INITIAL_BALANCE": "12500"},
        )

        self.assertEqual(cfg.metadata.trading_mode, "paper")
        self.assertEqual(cfg.metadata.paper_initial_balance, 12500.0)
        self.assertEqual(cfg.get("trading", "main_timeframe"), "15m")
        self.assertEqual(cfg.get("execution", "model_version"), "vb-1.0-5m-stop-first")
        self.assertEqual(cfg.execution_model_version, "vb-1.0-5m-stop-first")
        self.assertEqual(cfg.get("execution", "fee_rate"), 0.0004)
        self.assertEqual(cfg.get("execution", "slippage_rate"), 0.0002)
        self.assertEqual(cfg.get("strategy", "volume", "volume_multiplier"), 1.5)
        self.assertEqual(cfg.get("strategy", "volume", "effective_gate"), "volume > volume_sma")
        self.assertIn("dormant", cfg.get("strategy", "volume", "declared_multiplier_status"))
        self.assertEqual(len(cfg.source_sha256), 64)
        self.assertEqual(len(cfg.snapshot_sha256), 64)

    def test_effective_execution_model_matches_runtime_snapshot(self):
        cfg = load_runtime_config(
            CONFIG,
            environ={"TRADING_MODE": "paper", "PAPER_INITIAL_BALANCE": "10000"},
        )
        self.assertEqual(
            cfg.get("execution", "model_version"),
            "vb-1.0-5m-stop-first",
        )
        snapshot = cfg.snapshot()
        self.assertEqual(
            snapshot["effective"]["execution"]["model_version"],
            "vb-1.0-5m-stop-first",
        )
        self.assertNotEqual(
            snapshot["effective"]["execution"]["model_version"],
            "version-a-current",
        )

    def test_runtime_config_is_reproducible_for_same_input(self):
        env = {"TRADING_MODE": "paper", "PAPER_INITIAL_BALANCE": "10000"}
        first = load_runtime_config(CONFIG, environ=env)
        second = load_runtime_config(CONFIG, environ=env)
        self.assertEqual(first.source_sha256, second.source_sha256)
        self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(first.snapshot(), second.snapshot())

    def test_runtime_config_rejects_unknown_mode_and_invalid_balance(self):
        with self.assertRaises(ConfigError):
            load_runtime_config(CONFIG, environ={"TRADING_MODE": "testnet"})
        with self.assertRaises(ConfigError):
            load_runtime_config(CONFIG, environ={"PAPER_INITIAL_BALANCE": "0"})

    def test_domain_contracts_are_serializable_and_explicit(self):
        now = datetime.now(timezone.utc)
        context = DecisionContext("BTC/USDT", now, {"1h": now, "15m": now, "5m": now})
        fill = Fill("fill-1", "intent-1", now, 100.0, 2.0, fee=0.1, slippage=0.02)
        risk = RiskSnapshot(1000, 800, 200, 400, 10, 15, 5)
        event = EventRecord("event-1", "trade-1", "ENTRY_FILLED", now, 1, {"price": 100})

        self.assertEqual(context.symbol, "BTC/USDT")
        self.assertEqual(fill.fee, 0.1)
        self.assertEqual(risk.portfolio_risk_at_stop, 15)
        self.assertEqual(event.to_dict()["event_type"], "ENTRY_FILLED")

    def test_taxonomy_does_not_collapse_reasons_to_skip(self):
        record = taxonomy_record(
            stage=DecisionStage.RISK,
            reason=DecisionReason.CORRELATION_BLOCK,
            detail="correlation=0.82",
        )
        self.assertEqual(
            record,
            {
                "stage": "RISK",
                "reason": "CORRELATION_BLOCK",
                "detail": "correlation=0.82",
                "secondary_reasons": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
