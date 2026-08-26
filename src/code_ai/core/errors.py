class CodeAIError(Exception):
    """Base exception for Code-AI failures."""


class ConfigurationError(CodeAIError):
    """Configuration loading or validation failed."""


class ProviderError(CodeAIError):
    """Provider request failed."""


class TransientProviderError(ProviderError):
    """Provider request failed for a retryable reason."""


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


class ContextCapacityError(CodeAIError):
    """The active request cannot fit within the configured context limit."""


class ToolArgumentError(CodeAIError):
    """Tool arguments are malformed or unsafe."""


class ToolExecutionError(CodeAIError):
    """Tool execution failed."""


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
