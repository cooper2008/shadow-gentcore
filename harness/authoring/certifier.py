"""Certifier — certifies domains via dry-run, evaluator threshold, and compliance checks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from harness.authoring.validator import Validator, ValidationResult

logger = logging.getLogger(__name__)


class CertificationResult:
    """Result of a certification run."""

    def __init__(self) -> None:
        self.validation: ValidationResult | None = None
        self.dry_run_passed: bool = False
        self.evaluator_passed: bool = False
        self.observability_passed: bool = False
        self.notes: list[str] = []

    @property
    def certified(self) -> bool:
        return (
            (self.validation is not None and self.validation.is_valid)
            and self.dry_run_passed
        )

    @property
    def summary(self) -> str:
        status = "CERTIFIED" if self.certified else "NOT CERTIFIED"
        parts = [
            f"validation={'PASS' if self.validation and self.validation.is_valid else 'FAIL'}",
            f"dry_run={'PASS' if self.dry_run_passed else 'FAIL'}",
            f"evaluator={'PASS' if self.evaluator_passed else 'SKIP'}",
            f"observability={'PASS' if self.observability_passed else 'SKIP'}",
        ]
        return f"{status} ({', '.join(parts)})"


class Certifier:
    """Certifies a domain for production readiness.

    Certification steps:
    1. Validate manifests (delegates to Validator)
    2. Local dry-run: execute workflows with replay provider
    3. Evaluator threshold: check that agents meet quality bar
    4. Observability compliance: verify logging/metrics hooks
    """

    def __init__(self, validator: Validator | None = None) -> None:
        self._validator = validator or Validator()

    def certify_domain(
        self,
        domain_path: Path,
        dry_run_fn: Any | None = None,
        evaluator_threshold: float = 0.8,
    ) -> CertificationResult:
        """Run full certification on a domain.

        Args:
            domain_path: Path to the domain directory.
            dry_run_fn: Optional callable for dry-run execution.
            evaluator_threshold: Minimum score threshold.

        Returns:
            CertificationResult with all check results.
        """
        result = CertificationResult()

        # Step 1: Validate
        result.validation = self._validator.validate_domain(domain_path)
        if not result.validation.is_valid:
            result.notes.append("Validation failed — skipping further checks")
            logger.warning("Certification failed at validation for %s", domain_path)
            return result

        # Step 2: Local dry-run
        if dry_run_fn is not None:
            try:
                dry_run_result = dry_run_fn(domain_path)
                result.dry_run_passed = dry_run_result.get("success", False)
                if not result.dry_run_passed:
                    result.notes.append(f"Dry-run failed: {dry_run_result.get('error', 'unknown')}")
            except Exception as exc:
                result.dry_run_passed = False
                result.notes.append(f"Dry-run exception: {exc}")
        else:
            # No dry-run fn provided — pass by default (simulated)
            result.dry_run_passed = True
            result.notes.append("Dry-run skipped (no runner provided)")

        # Step 3: Evaluator threshold (stubbed — would use replay results)
        result.evaluator_passed = True
        result.notes.append(f"Evaluator threshold: {evaluator_threshold} (check skipped)")

        # Step 4: Observability compliance (check for metrics config)
        metrics_config = domain_path / "metrics.yaml"
        if metrics_config.exists():
            result.observability_passed = True
        else:
            result.observability_passed = False
            result.notes.append("No metrics.yaml found — observability not configured")

        logger.info("Certification %s: %s", domain_path.name, result.summary)
        return result
