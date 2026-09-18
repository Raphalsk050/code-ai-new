class CodeAIError(Exception):
    """Base exception for Code-AI failures."""


class ConfigurationError(CodeAIError):
    """Configuration loading or validation failed."""


class ProviderError(CodeAIError):
    """Provider request failed."""


class TransientProviderError(ProviderError):
    """Provider request failed for a retryable reason."""


class EmbeddingInputError(ProviderError):
    """The embedding endpoint refused the input itself, not the request.

    Too many texts in one call or one text past the model's context window.
    Neither is an outage: the caller shrinks what it sends and carries on.
    """


class UnsupportedProviderCapability(ProviderError):
    """The selected provider or endpoint does not support a requested capability."""


class ImageLimitError(ProviderError):
    """The request carried more images than the endpoint accepts in one prompt.

    Carries the limit the endpoint named so the caller can fit the conversation
    to it and try again, instead of throwing every attachment away.
    """

    def __init__(self, message: str, *, limit: int) -> None:
        super().__init__(message)
        self.limit = max(0, int(limit))


class ToolCallingUnsupportedError(ProviderError):
    """The endpoint refused the request because it does not serve tool calls.

    A vLLM started without a tool parser answers a request carrying ``tools``
    with a 400 rather than ignoring the field, which used to make every
    otherwise-good model on such a server unusable. Raised so the caller can
    switch that session to the prompt-based tool protocol and try again instead
    of failing the turn.
    """


class ContextCapacityError(CodeAIError):
    """The active request cannot fit within the configured context limit."""


class ToolArgumentError(CodeAIError):
    """Tool arguments are malformed or unsafe."""


class ToolExecutionError(CodeAIError):
    """Tool execution failed."""


class EnvironmentUnavailableError(ToolExecutionError):
    """The host lacks what the tool needs (a backend, a platform feature).

    Unlike a bad argument, this fails the same way on every retry for the rest
    of the session, so the runtime withdraws the tool rather than let the model
    keep trying.
    """


class WorkspaceBoundaryError(ToolExecutionError):
    """A path or command attempted to escape the configured workspace."""


class CommandTimeoutError(ToolExecutionError):
    """A command exceeded its timeout."""


class CancellationError(CodeAIError):
    """The active operation was cancelled."""


class TerminalSessionError(ToolExecutionError):
    """Persistent terminal operation failed."""


class GoalStateError(CodeAIError):
    """An illegal goal lifecycle transition was requested."""
