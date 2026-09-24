"""Runs one orchestrator graph: the executor the interpreter delegates to, and
the object that owns a single run.

The split matters. :class:`GraphInterpreter` (core) decides *what* happens next;
:class:`GraphNodeExecutor` (here) knows how to actually hire an agent, ask a
model a yes/no question, and park on a human. Only this half touches providers,
sub-agent runtimes and the event bus, which is what keeps the interpreter
testable with a fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from code_ai.config.models import AppConfig, GraphConfig
from code_ai.core.graph.compile import ValidationIssue, compile_graph
from code_ai.core.graph.professions import PLAIN_PROFESSION, REPORT_RULE, graph_profile_registry
from code_ai.core.graph.program import GraphProgram
from code_ai.core.graph.runtime import (
    AgentOutcome,
    GraphInterpreter,
    GraphLimits,
    GraphRunOutcome,
    ModelBinding,
    ProfessionBinding,
    RunStatus,
    ToolAccess,
)
from code_ai.core.graph.toolbox import apply_access, capabilities_of, grantable_names
from code_ai.core.orchestration import WIND_DOWN_TIME_BUDGET
from code_ai.core.subagents.naming import generate_agent_name
from code_ai.core.subagents.profiles import SubagentProfile, SubagentProfileRegistry
from code_ai.core.subagents.runtime import SubagentRuntime
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import EventEnvelope
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import Message, ModelRequest
from code_ai.tools.output import bound_text

logger = logging.getLogger(__name__)

# Child events worth surfacing per node. Streaming deltas are excluded for the
# same reason the sub-agent coordinator excludes them: token-level output from a
# graph would scramble the log without telling the user anything.
_FORWARDED = frozenset(
    {"tool.call.started", "tool.call.completed", "tool.call.failed", "model.response.completed"}
)

# Grace over a node's own timeout before the run stops waiting for it.
_HARD_GRACE_SECONDS = 30.0

# Enough to show what an agent was given; the count says if there was more.
_MAX_TOOLS_REPORTED = 80

# A judge answers with one word; anything longer is the model ignoring the ask.
_JUDGE_SYSTEM = (
    "You decide one yes/no question about the work described below. Answer with "
    "exactly one word: YES or NO. No punctuation, no explanation."
)

# A model call for the judge cannot be unbounded, and reasoning models spend
# output budget on hidden thinking before the word arrives.
_JUDGE_MAX_TOKENS = 2048

JudgeCall = Callable[[str, str, ModelBinding | None], Awaitable[str]]


class UserChatGateway:
    """Parks a graph node on a human answer.

    Same shape as :class:`~code_ai.bridge.approval.WireApprovalGateway`: a future
    per pending question, resolved when the client sends the answer back. It
    emits nothing - the interpreter already announced the question.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[str]] = {}

    def open(self, call_id: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        previous = self._pending.get(call_id)
        if previous is not None and not previous.done():
            previous.set_result("")
        self._pending[call_id] = future
        return future

    def resolve(self, call_id: str, text: str) -> bool:
        future = self._pending.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(text)
        return True

    def close(self) -> None:
        """Unblock everything still waiting, so a stopped run can unwind."""

        for future in list(self._pending.values()):
            if not future.done():
                future.set_result("")
        self._pending.clear()

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(self._pending)

    def discard(self, call_id: str) -> None:
        self._pending.pop(call_id, None)


class GraphNodeExecutor:
    """Turns the interpreter's requests into real agents and real model calls."""

    def __init__(
        self,
        *,
        config: AppConfig,
        runtime: SubagentRuntime,
        profiles: SubagentProfileRegistry,
        event_bus: AsyncEventBus,
        judge_call: JudgeCall,
        gateway: UserChatGateway,
        run_id: str,
        provider_factory: Callable[[AppConfig], ModelProvider] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._profiles = profiles
        self._bus = event_bus
        self._judge_call = judge_call
        self._gateway = gateway
        self._run_id = run_id
        self._provider_factory = provider_factory
        self._secrets = dict(secrets or {})
        # One provider per distinct endpoint and sampling, so ten nodes pointing
        # at the same server share a client instead of opening ten.
        self._providers: dict[tuple[str, ...], ModelProvider] = {}

    # -- NodeExecutor -----------------------------------------------------

    async def run_agent(
        self,
        *,
        node_id: str,
        profession: str,
        custom: ProfessionBinding | None = None,
        task: str,
        extra_instructions: str,
        model: ModelBinding | None,
        max_steps: int,
        timeout_s: int,
        cancel: asyncio.Event,
        access: ToolAccess | None = None,
        name: str = "",
    ) -> AgentOutcome:
        if custom is not None:
            try:
                profile = _profile_from(custom)
            except ValueError as exc:
                return AgentOutcome(status="rejected", error=str(exc))
        elif profession.strip():
            profile = self._profiles.get(profession)
        else:
            profile = _custom_agent(extra_instructions)
            extra_instructions = ""
        # Only a profession written in the editor may go unframed; a built-in
        # one is written expecting the framing and breaks without it.
        raw_prompt = custom is not None and not custom.framing
        if profile is None:
            return AgentOutcome(
                status="rejected",
                error=(
                    f"Unknown profession {profession!r}. Available: "
                    f"{', '.join(self._profiles.names())}."
                ),
            )
        profile = apply_access(profile, access, self._runtime.base_registry)
        label = name.strip() or profile.name
        agent_name = generate_agent_name()
        try:
            provider, overrides = self._endpoint_for(model)
        except ValueError as exc:
            return AgentOutcome(status="rejected", error=str(exc), agent_name=agent_name)

        built = self._runtime.build(
            profile,
            model=(model.model if model else ""),
            provider=provider,
            extra_instructions=extra_instructions,
            max_model_steps=max_steps,
            timeout_seconds=timeout_s,
            config_overrides=overrides,
            raw_prompt=raw_prompt,
        )
        tools = built.orchestrator.tool_registry.names()
        forwarder = self._forwarder(node_id, agent_name, label)
        built.event_bus.subscribe(forwarder)
        await self._bus.emit(
            "graph.agent.started",
            {
                "run_id": self._run_id,
                "node_id": node_id,
                "agent_name": agent_name,
                "profession": label,
                "model": model.label() if model else self._config.model,
                "writes": profile.writes,
                "tools": tools[:_MAX_TOOLS_REPORTED],
                "tool_count": len(tools),
                "task": bound_text(task, 500),
            },
            source="graph",
        )

        child_cancel = asyncio.Event()
        mirror = asyncio.ensure_future(_mirror(cancel, child_cancel))
        hard_timeout = built.timeout_seconds + _HARD_GRACE_SECONDS
        try:
            result = await asyncio.wait_for(
                built.orchestrator.run_turn(task, cancel_event=child_cancel),
                timeout=hard_timeout,
            )
        except TimeoutError:
            child_cancel.set()
            return AgentOutcome(
                status="timeout",
                error=f"The agent exceeded its {built.timeout_seconds}s budget.",
                usage=built.usage.to_dict(),
                agent_name=agent_name,
            )
        except asyncio.CancelledError:
            child_cancel.set()
            raise
        except Exception as exc:  # degrade: a broken node reports, never crashes the run
            logger.exception("Graph node %s failed", node_id)
            return AgentOutcome(
                status="failed",
                error=str(exc) or type(exc).__name__,
                usage=built.usage.to_dict(),
                agent_name=agent_name,
            )
        finally:
            mirror.cancel()
            with contextlib.suppress(BaseException):
                await mirror
            built.event_bus.unsubscribe(forwarder)

        usage = built.usage.to_dict()
        if result.cancelled:
            return AgentOutcome(status="cancelled", usage=usage, agent_name=agent_name)
        if result.error is not None:
            return AgentOutcome(
                status="failed",
                summary=result.text or "",
                error=result.error,
                usage=usage,
                agent_name=agent_name,
            )
        if result.wind_down_reason == WIND_DOWN_TIME_BUDGET:
            return AgentOutcome(
                status="timeout",
                summary=result.text or "",
                error=f"The agent exceeded its {built.timeout_seconds}s budget.",
                usage=usage,
                agent_name=agent_name,
            )
        if result.wind_down_reason is not None:
            return AgentOutcome(
                status="failed",
                summary=result.text or "",
                error=f"The agent stopped early: {result.wind_down_reason.replace('_', ' ')}.",
                usage=usage,
                agent_name=agent_name,
            )
        return AgentOutcome(
            status="completed", summary=result.text or "", usage=usage, agent_name=agent_name
        )

    async def run_clean(
        self,
        *,
        node_id: str,
        name: str,
        system: str,
        task: str,
        model: ModelBinding | None,
        timeout_s: int,
        cancel: asyncio.Event,
    ) -> AgentOutcome:
        """One model call carrying exactly what the node says: no framing, no tools."""

        agent_name = generate_agent_name()
        try:
            provider, _ = self._endpoint_for(model)
        except ValueError as exc:
            return AgentOutcome(status="rejected", error=str(exc), agent_name=agent_name)
        messages = [Message(role="user", content=task)]
        if system.strip():
            messages.insert(0, Message(role="system", content=system))
        request = ModelRequest(
            model=(model.model if model and model.model else self._config.model),
            messages=messages,
            max_output_tokens=self._config.output_token_reserve,
        )
        label = name.strip() or "clean agent"
        await self._bus.emit(
            "graph.agent.started",
            {
                "run_id": self._run_id,
                "node_id": node_id,
                "agent_name": agent_name,
                "profession": label,
                "model": model.label() if model else self._config.model,
                "writes": False,
                "tools": [],
                "tool_count": 0,
                "task": bound_text(task, 500),
            },
            source="graph",
        )
        await self._bus.emit(
            "graph.node.progress",
            {
                "run_id": self._run_id,
                "node_id": node_id,
                "agent_name": agent_name,
                "profession": label,
                "event": "model.request.started",
                "detail": "waiting for the model",
            },
            source="graph",
        )
        timeout = timeout_s or self._config.budgets.subagent_worker_timeout_s
        try:
            response = await asyncio.wait_for(
                (provider or self._runtime.provider).complete(request), timeout=timeout
            )
        except TimeoutError:
            return AgentOutcome(
                status="timeout",
                error=f"The model did not answer within {timeout}s.",
                agent_name=agent_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # degrade: a broken node reports, never crashes the run
            logger.exception("Graph node %s failed", node_id)
            return AgentOutcome(
                status="failed", error=str(exc) or type(exc).__name__, agent_name=agent_name
            )
        usage = response.usage
        return AgentOutcome(
            status="completed",
            summary=response.text or "",
            usage=(
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage
                else {}
            ),
            agent_name=agent_name,
        )

    async def judge(
        self,
        *,
        node_id: str,
        question: str,
        context: str,
        model: ModelBinding | None,
        cancel: asyncio.Event,
    ) -> bool:
        prompt = f"Question: {question}\n\nWhat happened so far:\n{context or '(nothing yet)'}"
        try:
            answer = await self._judge_call(_JUDGE_SYSTEM, prompt, model)
        except Exception as exc:  # a judge that cannot answer takes the false branch
            logger.warning("Graph judge on %s failed: %s", node_id, exc)
            await self._bus.emit(
                "graph.warning",
                {
                    "run_id": self._run_id,
                    "node_id": node_id,
                    "message": f"The judge could not answer ({exc}); taking `false`.",
                },
                source="graph",
            )
            return False
        return _reads_as_yes(answer)

    async def ask_user(self, *, node_id: str, prompt: str, cancel: asyncio.Event) -> str:
        call_id = uuid4().hex[:12]
        future = self._gateway.open(call_id)
        await self._bus.emit(
            "graph.user_chat.requested",
            {
                "run_id": self._run_id,
                "node_id": node_id,
                "call_id": call_id,
                "prompt": prompt,
            },
            source="graph",
        )
        try:
            answer = await future
        finally:
            self._gateway.discard(call_id)
        await self._bus.emit(
            "graph.user_chat.answered",
            {"run_id": self._run_id, "node_id": node_id, "call_id": call_id},
            source="graph",
        )
        return answer

    # -- endpoints --------------------------------------------------------

    def _endpoint_for(
        self, binding: ModelBinding | None
    ) -> tuple[ModelProvider | None, dict[str, Any]]:
        """Resolve a Model node into a provider plus the config fields to replace.

        ``None`` means "the session's own provider": a node that only names a
        model runs on the session endpoint. A provider reads sampling from the
        config it was built with, so a node that sets any gets a client of its
        own even on that endpoint - on the session's, it would be ignored.
        """

        if binding is None:
            return None, {}
        overrides: dict[str, Any] = {}
        sampling = self._sampling_overrides(binding)
        if sampling is not None:
            overrides["sampling"] = sampling
        own_endpoint = bool(
            binding.base_url or binding.api_mode or binding.api_key_ref not in ("", "session")
        )
        if not own_endpoint and sampling is None:
            return None, overrides

        api_mode = binding.api_mode or self._config.api_mode
        base_url = binding.base_url or self._config.base_url
        api_key = self._resolve_key(binding.api_key_ref)
        if own_endpoint:
            overrides |= {"api_mode": api_mode, "base_url": base_url, "api_key": api_key}
        sampling = sampling or self._config.sampling

        key = (api_mode, base_url, api_key, repr(sampling))
        cached = self._providers.get(key)
        if cached is not None:
            return cached, overrides
        candidate = dataclasses.replace(
            self._config, api_mode=api_mode, base_url=base_url, api_key=api_key, sampling=sampling
        )
        factory = self._provider_factory or _default_provider_factory
        provider = factory(candidate)
        self._providers[key] = provider
        return provider, overrides

    def _sampling_overrides(self, binding: ModelBinding) -> Any:
        if binding.temperature is None and binding.top_p is None and binding.top_k is None:
            return None
        current = self._config.sampling
        return dataclasses.replace(
            current,
            temperature=current.temperature if binding.temperature is None else binding.temperature,
            top_p=current.top_p if binding.top_p is None else binding.top_p,
            top_k=current.top_k if binding.top_k is None else binding.top_k,
        )

    def _resolve_key(self, reference: str) -> str:
        """Turn an ``api_key_ref`` into a key, or say why it cannot be resolved."""

        reference = (reference or "session").strip()
        if reference in ("", "session"):
            return self._config.api_key
        if reference.startswith("env:"):
            name = reference[4:]
            value = os.environ.get(name, "")
            if not value:
                raise ValueError(
                    f"The environment variable {name} is not set on the machine running "
                    "the agent, so this endpoint has no key."
                )
            return value
        if reference.startswith("secret:"):
            name = reference[7:]
            value = self._secrets.get(name, "")
            if not value:
                raise ValueError(
                    f"No stored secret named {name!r} was supplied for this run. Add it "
                    "with the editor's key button."
                )
            return value
        raise ValueError(f"Unsupported API key reference {reference!r}.")

    def _forwarder(self, node_id: str, agent_name: str, profession: str):
        async def handler(envelope: EventEnvelope) -> None:
            if envelope.event_type not in _FORWARDED:
                return
            await self._bus.emit(
                "graph.node.progress",
                {
                    "run_id": self._run_id,
                    "node_id": node_id,
                    "agent_name": agent_name,
                    "profession": profession,
                    "event": envelope.event_type,
                    "detail": _detail_of(envelope),
                },
                source="graph",
            )

        return handler


class GraphRunner:
    """One graph run, from compile to final event.

    Holds nothing the caller cannot see: the compiled program, the interpreter,
    and the gateway a paused node waits on. Stopping is cooperative - the
    interpreter observes the cancel event at its next checkpoint, and an agent
    already running unwinds through the same event.
    """

    def __init__(
        self,
        *,
        raw_graph: dict[str, Any],
        config: AppConfig,
        runtime: SubagentRuntime,
        event_bus: AsyncEventBus,
        judge_call: JudgeCall,
        profiles: SubagentProfileRegistry | None = None,
        provider_factory: Callable[[AppConfig], ModelProvider] | None = None,
        variables: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        run_id: str = "",
    ) -> None:
        self.run_id = run_id or uuid4().hex[:12]
        self._raw = raw_graph
        self._runtime = runtime
        self._config = config
        self._graph_config: GraphConfig = config.graph
        self._profiles = profiles or graph_profile_registry()
        self._bus = event_bus
        self._variables = dict(variables or {})
        self.gateway = UserChatGateway()
        self._cancel = asyncio.Event()
        self._executor = GraphNodeExecutor(
            config=config,
            runtime=runtime,
            profiles=self._profiles,
            event_bus=event_bus,
            judge_call=judge_call,
            gateway=self.gateway,
            run_id=self.run_id,
            provider_factory=provider_factory,
            secrets=secrets,
        )
        self._program: GraphProgram | None = None
        self._interpreter: GraphInterpreter | None = None

    # -- validation -------------------------------------------------------

    def validate(self) -> list[ValidationIssue]:
        runtime = self._runtime
        program, issues = compile_graph(
            self._raw,
            professions=tuple(self._profiles.names()),
            tools=grantable_names(runtime.base_registry) if runtime is not None else (),
            max_loop_iterations=self._graph_config.max_loop_iterations,
        )
        self._program = program
        return issues

    @property
    def program(self) -> GraphProgram | None:
        return self._program

    # -- running ----------------------------------------------------------

    async def run(self) -> GraphRunOutcome:
        issues = self.validate()
        if self._program is None:
            payload = [item.payload() for item in issues]
            await self._bus.emit(
                "graph.validation.failed",
                {"run_id": self.run_id, "issues": payload},
                source="graph",
            )
            return GraphRunOutcome(
                run_id=self.run_id,
                status=RunStatus.FAILED,
                error="The graph has errors that must be fixed before it can run.",
                reason="validation failed",
            )

        self._interpreter = GraphInterpreter(
            program=self._program,
            executor=self._executor,
            emit=self._emit,
            run_id=self.run_id,
            limits=GraphLimits(
                max_nodes_executed=self._graph_config.max_nodes_executed,
                max_seconds=self._graph_config.max_seconds,
                max_loop_iterations=self._graph_config.max_loop_iterations,
            ),
            cancel=self._cancel,
            variables=self._variables,
        )
        try:
            return await self._interpreter.run()
        finally:
            # A node still parked on a human would otherwise hold a future
            # nobody will ever answer.
            self.gateway.close()

    def request_stop(self) -> None:
        self._cancel.set()
        self.gateway.close()

    def answer(self, call_id: str, text: str) -> bool:
        return self.gateway.resolve(call_id, text)

    @property
    def stopped(self) -> bool:
        return self._cancel.is_set()

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        await self._bus.emit(event_type, payload, source="graph")


# -- helpers --------------------------------------------------------------


async def _mirror(parent: asyncio.Event, child: asyncio.Event) -> None:
    """Propagate a stop from the run down into the agent currently running."""

    await parent.wait()
    child.set()


def _profile_from(binding: ProfessionBinding) -> SubagentProfile:
    """Turn a Profession node into the profile the sub-agent runtime hires.

    The switches map straight onto capabilities; the role text is the system
    prompt, verbatim. Nothing is appended - not the report rule the built-in
    professions carry, not the sub-agent framing - because the point of writing
    a profession here is to say exactly what the agent is. Two of these talking
    to each other is the case that proves it: boilerplate about reporting back
    to a dispatcher is what turns a conversation into a pair of status updates.
    """

    role = binding.role.strip()
    if not role:
        raise ValueError("This profession has no role written for it.")
    name = binding.name.strip() or "custom"
    return SubagentProfile(
        name=name,
        description=f"Custom profession defined in the graph: {name}.",
        # No switch on is a talking-only agent, which is what a conversation wants.
        allowed_capabilities=capabilities_of(binding.switches()),
        max_model_steps=binding.max_steps if binding.max_steps > 0 else 40,
        timeout_field="subagent_worker_timeout_s",
        role_prompt=role,
        blocked_tools=frozenset(binding.blocked),
    )


def _custom_agent(instructions: str) -> SubagentProfile:
    """An Agent with no profession: the plain toolbox, its instructions as the role."""

    text = instructions.strip()
    if not text:
        return PLAIN_PROFESSION
    return dataclasses.replace(
        PLAIN_PROFESSION,
        name="custom",
        description="Agent defined by its own instructions in the graph.",
        role_prompt=text + REPORT_RULE,
    )


def _default_provider_factory(config: AppConfig) -> ModelProvider:
    # Imported lazily: the factory pulls in every provider client, and a graph
    # that never leaves the session endpoint should not pay for that.
    from code_ai.providers.factory import create_provider

    return create_provider(config)


def _reads_as_yes(answer: str) -> bool:
    """Read a judge's reply as a verdict.

    Deliberately narrow: a model that answered with a paragraph has not decided,
    and guessing from the first positive-sounding word it happened to use is how
    a pipeline silently takes the wrong branch.
    """

    text = (answer or "").strip().lower()
    if not text:
        return False
    first = text.split()[0].strip(".,:;!\"'*`")
    return first in ("yes", "y", "true", "sim", "approved", "ok")


def _detail_of(envelope: EventEnvelope) -> str:
    payload = envelope.payload or {}
    for key in ("name", "tool", "summary", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return bound_text(value.strip(), 160)
    return ""
