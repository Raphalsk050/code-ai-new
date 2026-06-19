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
