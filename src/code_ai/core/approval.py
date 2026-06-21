from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ApprovalScope(StrEnum):
    """How far a single approval decision reaches."""

    ONCE = "once"
    """Allow this one call only."""

    SESSION = "session"
    """Allow this call and remember the signature for the rest of the session."""

    DENY = "deny"
    """Reject the call."""


@dataclass(slots=True)
class ApprovalRequest:
    """Everything the user needs to decide whether a tool call may run."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    signature: str
    reason: str = ""
    capabilities: tuple[str, ...] = ()
    policy_denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "signature": self.signature,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "policy_denied": self.policy_denied,
        }


@dataclass(slots=True)
class ApprovalDecision:
    """The user's answer to an :class:`ApprovalRequest`."""

    scope: ApprovalScope
    reason: str = ""

    @property
    def approved(self) -> bool:
        return self.scope is not ApprovalScope.DENY

    @property
    def remember(self) -> bool:
        return self.scope is ApprovalScope.SESSION

    @classmethod
    def allow_once(cls) -> ApprovalDecision:
        return cls(scope=ApprovalScope.ONCE)

    @classmethod
    def allow_session(cls) -> ApprovalDecision:
        return cls(scope=ApprovalScope.SESSION)

    @classmethod
    def deny(cls, reason: str = "") -> ApprovalDecision:
        return cls(scope=ApprovalScope.DENY, reason=reason)


@runtime_checkable
class ApprovalGateway(Protocol):
    """Asks a human (or a stand-in) to approve a tool call.

    Implementations live at the boundary: the Textual UI surfaces a modal,
    while non-interactive runners deny by default. The orchestrator decides
    *whether* approval is required based on the permission mode; the gateway
    only handles the prompt itself.
    """

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision: ...


class DenyAllGateway:
    """Default non-interactive gateway: cannot prompt, so it denies.

    Used in headless runs and before an interactive UI attaches one. This keeps
    the prior behaviour (gated tools fail) instead of silently auto-approving.
    """

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.deny(
            "No interactive approver is attached; cannot grant permission."
        )


@dataclass(slots=True)
class _StaticGateway:
    """Test/automation helper that always returns the same decision."""

    decision: ApprovalDecision
    requests: list[ApprovalRequest] = field(default_factory=list)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decision


def call_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """Stable key used to remember 'always allow' choices for the session.

    For ``execute_command`` the signature keys on the program name (``argv[0]``)
    so approving ``pip`` once does not also silently approve ``rm``. Every other
    tool keys on its name, which is a reasonable granularity for a session-wide
    grant.
    """
    if tool_name == "execute_command":
        program = _command_program(arguments)
        if program:
            return f"execute_command:{program}"
    return tool_name


def _command_program(arguments: dict[str, Any]) -> str:
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        import shlex

        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        if parts:
            return parts[0]
    argv = arguments.get("argv")
    if isinstance(argv, list) and argv and isinstance(argv[0], str):
        return argv[0]
    return ""
