"""Machine-checkable gate required before any Version B baseline run.

This command runs deterministic contract tests only.  It never downloads market
data, runs a performance report, or writes a baseline artifact.

Usage from the package directory::

    python version_b_acceptance.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


APP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parent
MANIFEST_PATH = APP_ROOT / "VERSION_A_MANIFEST.json"
VERSION_A_COMMIT = "89bec19164417ca27bb40ea32cc071c8c0300d8a"
TEST_FILES = [
    "test_version_b_config_contracts.py",
    "test_version_b_data_semantics.py",
    "test_version_b_strategy_freeze.py",
    "test_version_b_risk_model.py",
    "test_version_b_execution_lifecycle.py",
    "test_version_b_execution_service.py",
    "test_version_b_persistence.py",
    "test_version_b_backtest.py",
    "test_version_b_paper.py",
    "test_version_b_replay_parity.py",
    "test_version_b_order_manager.py",
    "test_version_b_external_execution.py",
    "test_version_b_baseline_readiness.py",
    "test_version_b_integration_acceptance.py",
    "test_version_b_operational_acceptance.py",
    "test_version_b_paper_driver.py",
    "test_legacy_paper_exchange.py",
]

# Deterministic contract groups reported separately from residual exchange
# unknowns.  Keys are acceptance-surface names; values are junit class prefixes.
DETERMINISTIC_GROUPS = {
    "external_execution_contract": (
        "test_version_b_external_execution.WriteAheadAndIdentityTests",
        "test_version_b_external_execution.FailureAccountingTests",
        "test_version_b_external_execution.StoreContractTests",
    ),
    "restart_recovery_contract": (
        "test_version_b_external_execution.RestartRecoveryTests",
    ),
    "operational_acceptance_contract": (
        "test_version_b_operational_acceptance.NormalBarByBarTests",
        "test_version_b_operational_acceptance.CrashRestartContinueTests",
        "test_version_b_operational_acceptance.DuplicateHandlingTests",
        "test_version_b_operational_acceptance.VenueDegradationTests",
        "test_version_b_operational_acceptance.BadDataTests",
        "test_version_b_operational_acceptance.SingleInstanceAndLegacyTests",
        "test_version_b_operational_acceptance.OperationalControlsTests",
        "test_version_b_operational_acceptance.EventDrivenReplayParityTests",
    ),
    "paper_driver_contract": (
        "test_version_b_paper_driver.DriverEndToEndTests",
        "test_version_b_paper_driver.MarketDataAdapterBoundaryTests",
        "test_version_b_paper_driver.ClockSeparationTests",
        "test_version_b_paper_driver.WindowAndBoundaryNegativeControlTests",
        "test_version_b_paper_driver.LeaseAndHeartbeatTests",
        "test_version_b_paper_driver.DriverParityAndPreservationTests",
    ),
    "integration_acceptance_contract": (
        "test_version_b_integration_acceptance.OnePathNotParallelImplementationTests",
        "test_version_b_integration_acceptance.StrategyRiskExecutionEndToEndTests",
        "test_version_b_integration_acceptance.RestartDuringOpenLifecycleTests",
        "test_version_b_integration_acceptance.LegacyFallbackClosureTests",
    ),
    "baseline_readiness_contract": (
        "test_version_b_baseline_readiness.LineageTests",
        "test_version_b_baseline_readiness.MetricDefinitionTests",
        "test_version_b_baseline_readiness.ArtifactContractTests",
        "test_version_b_baseline_readiness.ReplayLineageTests",
        "test_version_b_baseline_readiness.ReproducibilityTests",
    ),
}

# Surfaces that a network-free gate can never prove.  These stay UNKNOWN until
# real exchange evidence exists; they are never inferred from local tests.
RESIDUAL_EXCHANGE_UNKNOWNS = {
    "live_exchange_acknowledgement": (
        "requires real venue evidence that create->fetch/ack confirms a resting "
        "stop and that clientOrderId is deduplicated on retry"
    ),
    "restart_exchange_reconciliation": (
        "requires a restart drill against a real account reconciling every open "
        "trade_id, intent, order, event, and fill"
    ),
}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _version_a_object_present() -> bool:
    return _git("cat-file", "-e", f"{VERSION_A_COMMIT}^{{commit}}").returncode == 0


def _ensure_version_a_object() -> dict[str, Any]:
    """Make the immutable Version A commit locally readable, without bypassing.

    A shallow or single-branch clone can be missing the Version A object even
    though it exists upstream.  That made three mandatory checks fail for an
    environmental reason.  This repairs availability only: every check below
    still runs unchanged and still has to pass.  It never rewrites, moves, or
    reinterprets Version A, and it never touches HEAD, a branch pointer, or the
    working tree.
    """
    if _version_a_object_present():
        ancestry = _git("merge-base", "--is-ancestor", VERSION_A_COMMIT, "HEAD")
        if ancestry.returncode == 0:
            return {"attempted": False, "reason": "already present and ancestral"}
        repair: list[dict[str, Any]] = []
    else:
        repair = []
    # Order matters: fetch the object first, then remove shallow boundaries so
    # the ancestry walk can actually reach it.
    attempts = (
        ["fetch", "--quiet", "origin", VERSION_A_COMMIT],
        ["fetch", "--quiet", "--unshallow", "origin", "+refs/heads/*:refs/remotes/origin/*"],
        ["fetch", "--quiet", "--deepen", "2147483647", "origin"],
    )
    for args in attempts:
        completed = _git(*args)
        repair.append({
            "command": "git " + " ".join(args),
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip()[:200],
        })
        if _version_a_object_present() and _git(
            "merge-base", "--is-ancestor", VERSION_A_COMMIT, "HEAD"
        ).returncode == 0:
            break
    return {
        "attempted": True,
        "restored": _version_a_object_present(),
        "ancestral": _git("merge-base", "--is-ancestor", VERSION_A_COMMIT, "HEAD").returncode == 0,
        "steps": repair,
    }


def _manifest_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        checks.append({"name": "manifest_parse", "passed": True})
    except Exception as exc:
        return [{"name": "manifest_parse", "passed": False, "reason": str(exc)}]

    # Availability repair is reported as its own check so a PASS is always
    # attributable: either the object was already readable, or the repair ran
    # and is visible here.  The immutable checks below are never skipped.
    availability = _ensure_version_a_object()
    checks.append({
        "name": "version_a_object_available",
        "passed": availability.get("restored", True) and availability.get("ancestral", True)
        if availability.get("attempted") else True,
        "reason": json.dumps(availability, ensure_ascii=False)[:600],
    })

    checks.append({
        "name": "version_a_commit_immutable",
        "passed": manifest.get("commit") == VERSION_A_COMMIT,
        "reason": f"manifest commit={manifest.get('commit')!r}",
    })
    object_check = _git("cat-file", "-e", f"{VERSION_A_COMMIT}^{{commit}}")
    checks.append({
        "name": "version_a_commit_exists",
        "passed": object_check.returncode == 0,
        "reason": object_check.stderr.strip(),
    })
    ancestry = _git("merge-base", "--is-ancestor", VERSION_A_COMMIT, "HEAD")
    checks.append({
        "name": "version_a_is_ancestor",
        "passed": ancestry.returncode == 0,
        "reason": ancestry.stderr.strip(),
    })

    config_blob = _git("show", f"{VERSION_A_COMMIT}:trading_bot/config.yaml")
    expected_hash = manifest.get("config_sha256")
    actual_hash = hashlib.sha256(config_blob.stdout.encode()).hexdigest() if config_blob.returncode == 0 else None
    checks.append({
        "name": "version_a_config_hash",
        "passed": config_blob.returncode == 0 and actual_hash == expected_hash,
        "reason": f"expected={expected_hash}, actual={actual_hash}",
    })
    checks.append({
        "name": "manifest_has_no_baseline",
        "passed": manifest.get("measurement_status", {}).get("baseline_collected") is False,
        "reason": "baseline_collected must remain false before this gate",
    })
    return checks


def _pytest_command() -> list[str]:
    """Select an installed test runner without assuming the system Python owns it."""
    try:
        import pytest  # noqa: F401
        return [sys.executable, "-m", "pytest"]
    except ImportError:
        candidates = [
            shutil.which("pytest"),
            "/tmp/tradingbot-report-venv/bin/pytest",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return [candidate]
    return [sys.executable, "-m", "pytest"]


def _run_tests() -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(prefix="version-b-acceptance-", suffix=".xml") as report:
        command = [
            *_pytest_command(),
            "-q",
            *TEST_FILES,
            f"--junitxml={report.name}",
        ]
        completed = subprocess.run(
            command,
            cwd=APP_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        result: dict[str, Any] = {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "failures": [],
            "class_results": {},
        }
        try:
            root = ET.parse(report.name).getroot()
            for case in root.iter("testcase"):
                result["passed"] += 1
                classname = case.attrib.get("classname", "")
                bucket = result["class_results"].setdefault(
                    classname, {"passed": 0, "failed": 0}
                )
                bucket["passed"] += 1
                failure = case.find("failure")
                error = case.find("error")
                skipped = case.find("skipped")
                if skipped is not None:
                    result["passed"] -= 1
                    result["skipped"] += 1
                    bucket["passed"] -= 1
                if failure is not None:
                    result["passed"] -= 1
                    result["failed"] += 1
                    bucket["passed"] -= 1
                    bucket["failed"] += 1
                    result["failures"].append({
                        "name": f"{case.attrib.get('classname', '')}.{case.attrib.get('name', '')}",
                        "reason": failure.attrib.get("message", failure.text or "failure"),
                    })
                if error is not None:
                    result["passed"] -= 1
                    result["errors"] += 1
                    bucket["passed"] -= 1
                    bucket["failed"] += 1
                    result["failures"].append({
                        "name": f"{case.attrib.get('classname', '')}.{case.attrib.get('name', '')}",
                        "reason": error.attrib.get("message", error.text or "error"),
                    })
        except Exception as exc:
            result["failures"].append({"name": "junit-report", "reason": str(exc)})
        result["stdout_tail"] = completed.stdout[-2000:]
        result["stderr_tail"] = completed.stderr[-2000:]
        result["passed_all"] = completed.returncode == 0 and not result["failures"]
        return result


def _group_status(tests: dict[str, Any], prefixes: tuple[str, ...], blocked: bool) -> str:
    """Compute one acceptance surface from the junit classes that prove it."""
    if blocked:
        return "BLOCKED"
    class_results = tests.get("class_results") or {}
    passed = failed = 0
    for classname, counts in class_results.items():
        if any(classname.startswith(prefix) for prefix in prefixes):
            passed += counts["passed"]
            failed += counts["failed"]
    if passed == 0 and failed == 0:
        return "BLOCKED"
    return "FAIL" if failed else "PASS"


def _acceptance_statuses(tests: dict[str, Any]) -> dict[str, str]:
    """Report mandatory path and parity independently from residual unknowns."""
    blocked = "No module named pytest" in tests.get("stderr_tail", "")
    if blocked:
        mandatory = "BLOCKED"
    elif tests.get("passed_all"):
        mandatory = "PASS"
    else:
        mandatory = "FAIL"
    parity = mandatory if mandatory in {"PASS", "FAIL", "BLOCKED"} else "UNKNOWN"
    statuses = {
        "full_version_b_path": mandatory,
        "backtest_paper_replay_parity": parity,
    }
    for name, prefixes in DETERMINISTIC_GROUPS.items():
        statuses[name] = _group_status(tests, prefixes, blocked)
    # Never inferred from local tests: these need real exchange evidence.
    for name in RESIDUAL_EXCHANGE_UNKNOWNS:
        statuses[name] = "UNKNOWN"
    return statuses


def run_gate() -> dict[str, Any]:
    checks = _manifest_checks()
    tests = _run_tests()
    acceptance = _acceptance_statuses(tests)
    all_checks_passed = all(check.get("passed", False) for check in checks)
    passed = all_checks_passed and tests["passed_all"]
    return {
        "status": "PASS" if passed else "FAIL",
        "baseline_allowed": passed,
        "baseline_collected": False,
        "acceptance": acceptance,
        "checks": checks,
        "tests": tests,
        "residual_unknowns": [
            {"surface": name, "required_evidence": reason}
            for name, reason in RESIDUAL_EXCHANGE_UNKNOWNS.items()
        ],
        "known_exceptions": [
            "pandas may emit a non-blocking pyarrow deprecation warning",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-only", action="store_true", help="emit only the JSON payload before the final status")
    args = parser.parse_args()
    result = run_gate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # The final line is intentionally machine-readable and is the only
    # authorization signal for a subsequent baseline command.
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
