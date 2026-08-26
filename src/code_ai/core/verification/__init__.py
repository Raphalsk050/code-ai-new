from __future__ import annotations

from code_ai.core.verification.detection import detect_project_verification
from code_ai.core.verification.matching import (
    is_genuine_verification,
    strongest_kind,
    verification_kind,
)
from code_ai.core.verification.models import (
    KIND_PRIORITY,
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)

__all__ = [
    "KIND_PRIORITY",
    "CommandKind",
    "ProjectVerification",
    "VerificationCommand",
    "detect_project_verification",
    "is_genuine_verification",
    "strongest_kind",
    "verification_kind",
]
