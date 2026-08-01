from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from code_ai.core.internet_intent import requires_current_web_search
from code_ai.events.models import utc_now_iso


class PlannerMode(StrEnum):
    AUTO = "auto"
    PLAN = "plan"
    ACT = "act"


class PlanningPhase(StrEnum):
    UNDERSTAND = "UNDERSTAND"
    DISCOVER_LOCAL = "DISCOVER_LOCAL"
    CREATE_PLAN = "CREATE_PLAN"
    WAITING_FOR_ACT = "WAITING_FOR_ACT"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REPAIR = "REPAIR"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class TaskIntent(StrEnum):
    CONVERSATION = "conversation"
    LOCAL_INSPECTION = "local_inspection"
    IMPLEMENTATION = "implementation"
    BUG_FIX = "bug_fix"
    REFACTORING = "refactoring"
    BUILD_OR_TEST_REPAIR = "build_or_test_repair"
    COMMAND_EXECUTION = "command_execution"
    EXTERNAL_RESEARCH = "external_research"


class TaskComplexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PlanStepKind(StrEnum):
    INSPECT_LOCAL = "INSPECT_LOCAL"
    IMPLEMENT = "IMPLEMENT"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    RESEARCH_WEB = "RESEARCH_WEB"
    ASK_USER = "ASK_USER"
    COMPLETE = "COMPLETE"


class PlanStepStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class EvidenceType(StrEnum):
    WORKSPACE_LISTED = "WORKSPACE_LISTED"
    FILE_READ = "FILE_READ"
    LOCAL_SEARCH_MATCH = "LOCAL_SEARCH_MATCH"
    LOCAL_SEARCH_COMPLETED = "LOCAL_SEARCH_COMPLETED"
    FILE_CREATED = "FILE_CREATED"
    FILE_CHANGED = "FILE_CHANGED"
    COMMAND_SUCCEEDED = "COMMAND_SUCCEEDED"
    COMMAND_FAILED = "COMMAND_FAILED"
    TERMINAL_OBSERVED = "TERMINAL_OBSERVED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    WEB_RESULT = "WEB_RESULT"
    USER_ANSWER = "USER_ANSWER"
    DISCOVERY_COMPLETED = "DISCOVERY_COMPLETED"
    COMPLETION_REQUESTED = "COMPLETION_REQUESTED"


class TaskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: TaskIntent
    objective: str
    acceptance_criteria: list[str]
    constraints: list[str] = Field(default_factory=list)
    requires_local_context: bool = False
    requires_workspace_mutation: bool = False
    requires_verification: bool = False
    requires_external_information: bool = False
    allows_web_first: bool = False
    complexity: TaskComplexity = TaskComplexity.SIMPLE
    blocking_ambiguities: list[str] = Field(default_factory=list)

    @field_validator("objective")
    @classmethod
    def _objective_must_not_be_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("objective must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_workspace_flags(self) -> TaskProfile:
        if self.requires_workspace_mutation:
            self.requires_local_context = True
            self.requires_verification = True
            self.allows_web_first = False
        if self.requires_local_context and not self.requires_external_information:
            self.allows_web_first = False
        return self

    @classmethod
    def from_user_text(cls, text: str) -> TaskProfile:
        objective = text.strip()
        normalized = _normalize(objective)
        mutation = _is_mutation_request(normalized)
        command = _is_command_request(normalized)
        local_reference = _mentions_workspace(normalized)
        local_inspection = _is_local_inspection_request(normalized)
        requires_local = mutation or command or local_reference or local_inspection
        explicit_web = _is_explicit_web_request(normalized)
        external = _is_external_research_request(normalized)
        current_info = requires_current_web_search(objective)
        current_external_info = current_info and not (requires_local and not explicit_web)
        requires_external = (
            not mutation
            and (
                explicit_web
                or current_external_info
                or (external and not requires_local)
            )
        )

        if mutation and _contains_any(normalized, {"fix", "bug", "corrija", "conserte"}):
            intent = TaskIntent.BUG_FIX
        elif mutation and _contains_any(normalized, {"refactor", "refatore", "reorganize"}):
            intent = TaskIntent.REFACTORING
        elif mutation:
            intent = TaskIntent.IMPLEMENTATION
        elif command:
            intent = TaskIntent.COMMAND_EXECUTION
        elif requires_external and not requires_local:
            intent = TaskIntent.EXTERNAL_RESEARCH
        elif local_reference or local_inspection:
            intent = TaskIntent.LOCAL_INSPECTION
        elif requires_external:
            intent = TaskIntent.EXTERNAL_RESEARCH
        else:
            intent = TaskIntent.CONVERSATION

        allows_web_first = requires_external and not requires_local
        criteria = _acceptance_criteria(
            objective=objective,
            mutation=mutation,
            local=requires_local,
            external=requires_external,
        )
        return cls(
            intent=intent,
            objective=objective,
            acceptance_criteria=criteria,
            constraints=[
                "Use local workspace evidence before external sources."
                if requires_local
                else "Answer only from available evidence."
            ],
            requires_local_context=requires_local,
            requires_workspace_mutation=mutation,
            requires_verification=mutation or command,
            requires_external_information=requires_external,
            allows_web_first=allows_web_first,
            complexity=TaskComplexity.MODERATE if mutation else TaskComplexity.SIMPLE,
        )


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    title: str
    kind: PlanStepKind
    status: PlanStepStatus = PlanStepStatus.PENDING
    target_paths: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceType] = Field(default_factory=list)
    attempt_count: int = 0
    last_error: str | None = None


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    revision: int = 1
    objective: str
    acceptance_criteria: list[str]
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    steps: list[PlanStep]
    current_step_index: int = 0
    status: PlanStatus = PlanStatus.ACTIVE
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_plan(self) -> ExecutionPlan:
        if not self.steps:
            raise ValueError("plan must contain at least one step")
        if self.current_step_index < 0 or self.current_step_index >= len(self.steps):
            raise ValueError("current_step_index is outside steps")
        return self

    @property
    def current_step(self) -> PlanStep:
        return self.steps[self.current_step_index]

    @classmethod
    def for_profile(cls, profile: TaskProfile, *, max_steps: int = 20) -> ExecutionPlan:
        steps = _steps_for_profile(profile)
        if len(steps) > max_steps:
            raise ValueError("deterministic plan exceeds max_plan_steps")
        return cls(
            objective=profile.objective,
            acceptance_criteria=list(profile.acceptance_criteria),
            constraints=list(profile.constraints),
            assumptions=[
                "The local workspace is authoritative for project-specific behavior."
            ]
            if profile.requires_local_context
            else [],
            steps=steps,
        )

    def snapshot(self) -> dict[str, object]:
        current = self.current_step
        completed = [
            step.title for step in self.steps if step.status == PlanStepStatus.COMPLETED
        ]
        return {
            "plan_id": self.plan_id,
            "revision": self.revision,
            "objective": self.objective,
            "status": self.status.value,
            "current_step": current.title,
            "current_step_id": current.step_id,
            "current_step_kind": current.kind.value,
            "current_step_status": current.status.value,
            "progress": f"{len(completed)}/{len(self.steps)}",
            "completed_steps": completed,
            "remaining_steps": [
                step.title for step in self.steps if step.status != PlanStepStatus.COMPLETED
            ],
        }


class AgentPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    status: PlanStepStatus = PlanStepStatus.PENDING

    @field_validator("title")
    @classmethod
    def _title_must_not_be_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("plan step title must be non-empty")
        return normalized


class AgentPlan(BaseModel):
    """The concrete, model-authored checklist shown in the task sidebar.

    Distinct from :class:`ExecutionPlan`, which is the deterministic skeleton
    that drives tool policy and completion gating. This holds the steps the model
    itself declared it will follow, so the UI reflects real intent instead of
    generic templates, and it only exists once the model has defined those steps.
    """

    model_config = ConfigDict(extra="forbid")

    steps: list[AgentPlanStep]
    current_index: int = 0
    status: PlanStatus = PlanStatus.ACTIVE
    # The model called complete_plan_step on the final step. advance() refuses to
    # settle that step (only completion settles the whole plan), but remembering
    # the declaration lets a turn that ends in a clean final answer complete the
    # plan instead of freezing the sidebar on a step the model reported done.
    final_step_declared: bool = False

    @model_validator(mode="after")
    def _validate(self) -> AgentPlan:
        if not self.steps:
            raise ValueError("agent plan must contain at least one step")
        self.current_index = max(0, min(self.current_index, len(self.steps) - 1))
        return self

    @classmethod
    def from_titles(cls, titles: list[str], *, max_steps: int = 20) -> AgentPlan:
        cleaned = [title.strip() for title in titles if title and title.strip()]
        if not cleaned:
            raise ValueError("agent plan requires at least one non-empty step")
        if len(cleaned) > max_steps:
            cleaned = cleaned[:max_steps]
        steps = [AgentPlanStep(title=title) for title in cleaned]
        steps[0].status = PlanStepStatus.IN_PROGRESS
        return cls(steps=steps)

    @property
    def current_step(self) -> AgentPlanStep | None:
        if self.status != PlanStatus.ACTIVE:
            return None
        return self.steps[self.current_index]

    @property
    def on_final_step(self) -> bool:
        return (
            self.status == PlanStatus.ACTIVE
            and self.current_index >= len(self.steps) - 1
        )

    def resolve_completed_index(self, title: str) -> int:
        """Index of the not-yet-completed step the model declared finished.

        The model reports *which* step it completed by title; when that step sits
        ahead of the cursor (the model did several steps' work in one burst and
        only then reported), the cursor must catch up through it instead of
        lagging one advance behind until the end of the task. Falls back to the
        current step when the title is missing or matches nothing pending, which
        preserves the plain advance-by-one behaviour.
        """
        normalized = title.strip().casefold()
        if normalized:
            for index in range(self.current_index, len(self.steps)):
                if self.steps[index].title.strip().casefold() == normalized:
                    return index
        return self.current_index

    def advance(self) -> bool:
        """Mark the running step done and move to the next, if any.

        Returns ``True`` when the cursor moved forward. The final step stays
        ``IN_PROGRESS`` until :meth:`complete_all` settles the whole plan, so the
        sidebar keeps showing a live step rather than an empty tail.
        """
        if self.status != PlanStatus.ACTIVE:
            return False
        if self.current_index >= len(self.steps) - 1:
            return False
        self.steps[self.current_index].status = PlanStepStatus.COMPLETED
        self.current_index += 1
        self.steps[self.current_index].status = PlanStepStatus.IN_PROGRESS
        return True

    def complete_all(self) -> None:
        for step in self.steps:
            step.status = PlanStepStatus.COMPLETED
        self.current_index = len(self.steps) - 1
        self.status = PlanStatus.COMPLETED

    def pause(self) -> None:
        """Suspend an active plan while control is handed back to the user.

        The turn ended without settling the plan (a blocking question, a prose
        answer, a cancellation, a failure); the current step keeps its position
        but the plan is no longer running, so the sidebar must stop spinning.
        :meth:`resume` reactivates it when a follow-up turn picks the plan up.
        """
        if self.status == PlanStatus.ACTIVE:
            self.status = PlanStatus.WAITING

    def resume(self) -> None:
        if self.status == PlanStatus.WAITING:
            self.status = PlanStatus.ACTIVE

    def settle(self, status: PlanStatus) -> None:
        """Freeze the plan in a terminal non-success state (blocked/failed).

        The running step is marked FAILED so the sidebar shows where the plan
        stopped, instead of a step that keeps spinning after the turn ended.
        A paused (WAITING) plan settles the same way: it still points at the
        step it stopped on.
        """
        if self.status in {PlanStatus.ACTIVE, PlanStatus.WAITING}:
            self.steps[self.current_index].status = PlanStepStatus.FAILED
        self.status = status

    def snapshot(self) -> dict[str, object]:
        completed = [
            step.title for step in self.steps if step.status == PlanStepStatus.COMPLETED
        ]
        # A settled (blocked/failed) plan still points at the step it stopped on,
        # so the sidebar can render it as failed rather than silently pending.
        current = (
            None
            if self.status == PlanStatus.COMPLETED
            else self.steps[self.current_index]
        )
        return {
            "status": self.status.value,
            "progress": f"{len(completed)}/{len(self.steps)}",
            "current_step": current.title if current else None,
            "current_step_status": current.status.value if current else "",
            "completed_steps": completed,
            "remaining_steps": [
                step.title
                for step in self.steps
                if step.status != PlanStepStatus.COMPLETED
            ],
        }


class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    outcome: str = "success"
    acceptance_evidence: dict[str, list[str]] = Field(default_factory=dict)
    verification_summary: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    remaining_issues: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    double_check_acknowledged: bool = False

    @field_validator("outcome")
    @classmethod
    def _validate_outcome(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"success", "blocked", "failed"}:
            raise ValueError("outcome must be success, blocked, or failed")
        return normalized


def _steps_for_profile(profile: TaskProfile) -> list[PlanStep]:
    if profile.requires_workspace_mutation:
        return [
            PlanStep(
                step_id="inspect-local",
                title="Inspect the local workspace",
                kind=PlanStepKind.INSPECT_LOCAL,
                required_evidence=[EvidenceType.WORKSPACE_LISTED],
                success_criteria=["Repository structure or relevant files were inspected."],
            ),
            PlanStep(
                step_id="implement",
                title="Apply the requested workspace change",
                kind=PlanStepKind.IMPLEMENT,
                required_evidence=[EvidenceType.FILE_CREATED, EvidenceType.FILE_CHANGED],
                success_criteria=["The requested change exists in workspace files."],
            ),
            PlanStep(
                step_id="verify",
                title="Verify the changed workspace state",
                kind=PlanStepKind.VERIFY,
                required_evidence=[EvidenceType.VERIFICATION_PASSED],
                success_criteria=["An applicable project check ran against current files."],
            ),
            PlanStep(
                step_id="complete",
                title="Complete with evidence-backed summary",
                kind=PlanStepKind.COMPLETE,
                required_evidence=[EvidenceType.COMPLETION_REQUESTED],
                success_criteria=["The final result maps claims to recorded evidence."],
            ),
        ]
    if profile.requires_local_context:
        steps = [
            PlanStep(
                step_id="inspect-local",
                title="Inspect the local workspace",
                kind=PlanStepKind.INSPECT_LOCAL,
                required_evidence=[EvidenceType.WORKSPACE_LISTED],
            )
        ]
        if profile.requires_external_information:
            steps.append(
                PlanStep(
                    step_id="research-web",
                    title="Gather approved external evidence",
                    kind=PlanStepKind.RESEARCH_WEB,
                    required_evidence=[EvidenceType.WEB_RESULT],
                )
            )
        steps.append(
            PlanStep(
                step_id="complete",
                title="Answer from local evidence",
                kind=PlanStepKind.COMPLETE,
            )
        )
        return steps
    if profile.requires_external_information:
        return [
            PlanStep(
                step_id="research-web",
                title="Gather current external evidence",
                kind=PlanStepKind.RESEARCH_WEB,
                required_evidence=[EvidenceType.WEB_RESULT],
            ),
            PlanStep(
                step_id="complete",
                title="Answer with sourced evidence",
                kind=PlanStepKind.COMPLETE,
            ),
        ]
    return [
        PlanStep(
            step_id="answer",
            title="Answer the user",
            kind=PlanStepKind.COMPLETE,
        )
    ]


# Criteria appended only for mutation-classified tasks. Named so the planner
# can drop exactly these when a user denial downgrades the task mid-turn (see
# PlannerService.note_user_denial).
CRITERION_APPLY_VIA_TOOLS = "Apply changes through file tools rather than chat text."
CRITERION_VERIFY_AFTER_MUTATION = (
    "Run an applicable verification command after the last mutation."
)


def _acceptance_criteria(
    *, objective: str, mutation: bool, local: bool, external: bool
) -> list[str]:
    criteria = [f"Address the user's objective: {objective}"]
    if local:
        criteria.append("Use actual local workspace evidence.")
    if mutation:
        criteria.extend([CRITERION_APPLY_VIA_TOOLS, CRITERION_VERIFY_AFTER_MUTATION])
    if external:
        criteria.append("Use current external evidence before answering.")
    return criteria


def _normalize(text: str) -> str:
    return text.casefold()


def _contains_any(text: str, needles: set[str]) -> bool:
    return any(needle in text for needle in needles)


# Mutation verbs, matched on word boundaries so a verb *inside* another word
# never trips the gate ("adder" is not "add", "descreva" is not "escreva").
# Morphological suffixes cover the common PT/EN inflections ("adicione",
# "adicionar", "updated", "atualizando"), which plain substring markers missed.
_MUTATION_RE = re.compile(
    r"\b(?:"
    r"add(?:ed|ing|s)?|adicion\w*|appl(?:y|ies|ied|ying)|"
    r"atualiz\w*|chang(?:e|es|ed|ing)|consert\w*|corrig\w*|corrija|"
    r"creat(?:e|es|ed|ing)|cri(?:e|ar|a)|edit(?:e|ar|a|ed|ing|s)?|"
    r"escrev\w*|fix(?:es|ed|ing)?|implement\w*|modif\w*|"
    r"refator\w*|refactor\w*|remov\w*|renam\w*|renome\w*|updat\w*"
    r")\b"
)

# A message that *opens* as a question or an explanation request is asking to
# understand something, not to change it - even when a mutation verb appears as
# the subject ("explique a função update_user", "como adicionar um endpoint?",
# "pelo que voce comecaria a implementar hoje?"). Misreading these as mutations
# traps the turn behind a file-change evidence gate no explanation can ever
# satisfy - and actively steers the model into changes nobody asked for. The
# veto is deliberately biased: a real mutation phrased as a question degrades
# gracefully (tools stay available and any actual file change still demands
# verification through the evidence-keyed gate), while a trapped explanation
# has no way out.
_EXPLANATION_START_RE = re.compile(
    r"^(?:"
    r"explique|explica|me explique|me explica|explain|"
    r"descreva|descreve|describe|"
    r"o que|oque|what|por que|porque|why|como|how|"
    r"qual|quais|which|quando|when|onde|where|quem|who|"
    r"pelo que|pelo qual|pela qual|por onde|por qual|"
    r"sera que|será que|que tal|devo|deveria|deveriamos|deveríamos|"
    r"vale a pena|faz sentido|"
    r"should i|should we|would it|is it|"
    r"me diga|diga|tell me|"
    r"resuma|resumo|summarize|summarise|"
    r"analise|análise|analyze|analyse|"
    r"entenda|help me understand|walk me through"
    r")\b"
)

# Second-person request markers: a question that asks the *agent* to do the
# change ("pode criar o arquivo?", "can you add a test?") is still a mutation
# request. "pode ser"/"poderia ser" are excluded - those open a hypothesis
# ("could it be..."), not a request.
_REQUEST_MARKER_RE = re.compile(
    r"\b(?:"
    r"por favor|please|"
    r"pode(?:s|ria|riam)?(?!\s+ser\b)|voc[eê] pode|consegue(?:ria)?|"
    r"can you|could you|would you|will you"
    r")\b"
)

# Unambiguous "do it" shapes that survive the question veto: PT imperative /
# subjunctive forms ("implemente", "crie", "atualize") anywhere, or an English
# base verb opening the message ("update the README ...").
_IMPERATIVE_MUTATION_RE = re.compile(
    r"\b(?:"
    r"implemente(?:m)?|crie(?:m)?|adicione(?:m)?|corrija(?:m)?|conserte(?:m)?|"
    r"atualize(?:m)?|edite(?:m)?|escreva(?:m)?|remova(?:m)?|renomeie(?:m)?|"
    r"refatore(?:m)?|modifique(?:m)?|aplique(?:m)?|mude(?:m)?|troque(?:m)?|"
    r"ajuste(?:m)?|arrume(?:m)?|fa[çc]a(?:m)?"
    r")\b"
)
_EN_LEADING_MUTATION_RE = re.compile(
    r"^(?:implement|create|add|fix|update|write|edit|remove|rename|refactor|"
    r"modify|apply|change|make)\b"
)


def _is_mutation_request(text: str) -> bool:
    stripped = text.strip()
    if _EXPLANATION_START_RE.match(stripped):
        return False
    if not _MUTATION_RE.search(stripped):
        return False
    # Question-shaped text asks *about* changing, it does not ask to change:
    # "seria melhor implementar isso em rust?" wants an opinion, not files. It
    # only stays a mutation when the phrasing addresses the agent with the
    # action ("pode criar...?", "crie X, pode ser?", "can you add...?").
    if (
        stripped.endswith("?")
        and not _REQUEST_MARKER_RE.search(stripped)
        and not _IMPERATIVE_MUTATION_RE.search(stripped)
        and not _EN_LEADING_MUTATION_RE.match(stripped)
    ):
        return False
    return True


def _is_command_request(text: str) -> bool:
    return _contains_any(text, {"run ", "execute ", "rode ", "execute o comando"})


def _is_local_inspection_request(text: str) -> bool:
    return _contains_any(
        text,
        {
            "inspect",
            "leia",
            "ler ",
            "list ",
            "liste",
            "open ",
            "read ",
            "show ",
        },
    )


def _is_external_research_request(text: str) -> bool:
    return _contains_any(
        text,
        {"web", "internet", "search", "pesquise", "procure", "current", "latest"},
    )


def _is_explicit_web_request(text: str) -> bool:
    return _contains_any(
        text,
        {
            "browser",
            "google",
            "internet",
            "online",
            "web",
            "web_search",
        },
    )


def _mentions_workspace(text: str) -> bool:
    workspace_markers = {
        "repo",
        "repository",
        "workspace",
        "project",
        "file",
        "folder",
        "directory",
        "diretorio",
        "diretório",
        "codigo",
        "código",
        "arquivo",
        "pasta",
        "projeto",
    }
    path_like = bool(re.search(r"(^|\s)[\w./-]+\.(py|toml|md|json|cpp|h|hpp|ts|tsx|js)\b", text))
    return path_like or _contains_any(text, workspace_markers)


# Verbs that, on their own, only ask the agent to carry on with what it was
# already doing.
_CONTINUATION_VERBS = frozenset(
    {
        "continue", "continua", "continuar", "continuemos", "continuando",
        "prossiga", "prossegue", "prosseguir", "prossigamos",
        "siga", "segue", "seguir", "sigamos",
        "retome", "retoma", "retomar", "resume",
        "vai", "va", "bora", "manda", "proceed",
        "go", "keep", "carry",
    }
)

# Words that may keep a continuation marker company without turning it into a
# new request ("continue de onde paramos", "ok, pode seguir com o plano").
# The set is closed on purpose: any message carrying a genuinely new objective
# ("continue, mas agora faça X") contains tokens outside it and is therefore
# never mistaken for a continuation.
_CONTINUATION_FILLER = frozenset(
    {
        "por", "favor", "please", "ok", "okay", "beleza", "blz", "sim", "yes",
        "obrigado", "obrigada", "thanks", "vamos", "let", "lets", "s",
        "e", "and", "entao", "agora", "now", "ai", "ja", "pode", "podes",
        "voce", "vc", "you", "can",
        "de", "do", "da", "onde", "paramos", "parou", "paramo", "ponto",
        "daqui", "dai", "em", "frente", "adiante",
        "from", "where", "we", "left", "off", "on", "ahead", "going", "up",
        "com", "isso", "aquilo", "it", "that", "the", "with",
        "o", "a", "os", "as", "trabalho", "tarefa", "plano", "task", "work",
        "plan",
    }
)

# A longer message is making a request, not just poking the agent forward.
_MAX_CONTINUATION_TOKENS = 8

_WORD_RE = re.compile(r"[a-z0-9]+")


def is_continuation_request(text: str) -> bool:
    """Whether this turn only asks the agent to carry on with the current task.

    Re-running the surface classifier on such a message is a bug, not a
    refinement: "continue" carries no mutation keyword, so a follow-up to an
    implementation task gets relabelled ``CONVERSATION`` and the whole runtime
    task state - profile, plan, evidence ledger - is thrown away mid-work.

    Recognition is by closed vocabulary rather than a prefix match: the message
    must consist *only* of continuation verbs and filler, so "continue, mas
    agora faça X" stays the new request it is.
    """
    tokens = _WORD_RE.findall(_strip_accents(_normalize(text)))
    if not tokens or len(tokens) > _MAX_CONTINUATION_TOKENS:
        return False
    if not any(token in _CONTINUATION_VERBS for token in tokens):
        return False
    return all(
        token in _CONTINUATION_VERBS or token in _CONTINUATION_FILLER
        for token in tokens
    )


def _strip_accents(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
