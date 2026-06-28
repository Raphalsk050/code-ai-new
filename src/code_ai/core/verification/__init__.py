from __future__ import annotations

from code_ai.core.verification.detection import detect_project_verification
from code_ai.core.verification.matching import is_genuine_verification
from code_ai.core.verification.models import (
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)

__all__ = [
    "CommandKind",
    "ProjectVerification",
    "VerificationCommand",
    "detect_project_verification",
    "is_genuine_verification",
]
