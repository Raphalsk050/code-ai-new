from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


@dataclass(slots=True)
class Finding:
    """One thing worth acting on, stated with the evidence that produced it."""

    id: str
    severity: Severity
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    recommendation: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
        }
        if self.evidence:
            payload["evidence"] = list(self.evidence)
        if self.recommendation:
            payload["recommendation"] = self.recommendation
        return payload


@dataclass(slots=True)
class ApkReport:
    """The compact result handed back to the model.

    Every list in here is capped by the analyzer, so a 200 MB APK with ten
    thousand entries produces a report the same shape and size as a small one.
    """

    path: str
    size_bytes: int
    sha256: str
    sections: dict[str, object]
    findings: list[Finding]
    summary: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "summary": self.summary,
            "finding_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
        }
        payload.update(self.sections)
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload
