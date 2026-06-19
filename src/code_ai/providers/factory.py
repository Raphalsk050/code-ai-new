from __future__ import annotations

from code_ai.config.models import AppConfig
from code_ai.core.errors import ConfigurationError
from code_ai.providers.base import ModelProvider
from code_ai.providers.ollama import NativeOllamaProvider
from code_ai.providers.openai_completions import OpenAIChatCompletionsProvider
from code_ai.providers.openai_responses import OpenAIResponsesProvider


def create_provider(config: AppConfig) -> ModelProvider:
    if config.api_mode == "responses":
        return OpenAIResponsesProvider(config)
    if config.api_mode == "completions":
        return OpenAIChatCompletionsProvider(config)
    if config.api_mode == "ollama":
        return NativeOllamaProvider(config)
    raise ConfigurationError(f"Unsupported api_mode: {config.api_mode}")
