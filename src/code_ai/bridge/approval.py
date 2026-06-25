from __future__ import annotations

import asyncio

from code_ai.core.approval import ApprovalDecision, ApprovalRequest, ApprovalScope


class WireApprovalGateway:
    """Approval gateway backed by a JSON-RPC client across the bridge.

    Mirrors :class:`~code_ai.ui.terminal.approval.TerminalApprovalGateway`, but
    instead of pushing a Textual modal it parks an :class:`asyncio.Future` keyed
    by ``call_id`` and waits for the client to send a ``resolveApproval`` command.

    It deliberately emits nothing: the orchestrator already emits
    ``tool.approval.requested`` (with ``request.to_dict()``, which carries the
    ``call_id``) before calling :meth:`request_approval`, and
    ``tool.approval.resolved`` after it returns. The client reads those off the
    event stream and answers with the matching ``call_id``.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[ApprovalDecision]] = {}

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        # A second request with the same call_id should never happen, but if it
        # did the stale future would leak forever; deny it so its caller unblocks.
        previous = self._pending.get(request.call_id)
        if previous is not None and not previous.done():
            previous.set_result(ApprovalDecision.deny("Superseded by a newer request."))
        self._pending[request.call_id] = future
        try:
            return await future
        finally:
            self._pending.pop(request.call_id, None)

    def resolve(self, call_id: str, decision: ApprovalDecision) -> bool:
        """Resolve a pending approval. Returns ``False`` if nothing was waiting."""
        future = self._pending.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def resolve_scope(self, call_id: str, scope: str, reason: str = "") -> bool:
        """Resolve from the wire ``scope`` string (``once``/``session``/``deny``)."""
        return self.resolve(call_id, _decision_from_scope(scope, reason))

    def close(self) -> None:
        """Deny every still-pending approval so blocked turns can unwind."""
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(ApprovalDecision.deny("bridge closed"))
        self._pending.clear()


def _decision_from_scope(scope: str, reason: str = "") -> ApprovalDecision:
    match ApprovalScope(scope):
        case ApprovalScope.ONCE:
            return ApprovalDecision.allow_once()
        case ApprovalScope.SESSION:
            return ApprovalDecision.allow_session()
        case ApprovalScope.DENY:
            return ApprovalDecision.deny(reason)
