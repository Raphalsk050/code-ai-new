from __future__ import annotations

from code_ai.config.models import AppConfig
from code_ai.core.errors import ConfigurationError
from code_ai.providers.base import ModelProvider
from code_ai.providers.ollama import NativeOllamaProvider
from code_ai.providers.openai_completions import OpenAIChatCompletionsProvider
from code_ai.providers.openai_responses import OpenAIResponsesProvider

# The settings this function and the clients it builds consume up front, rather
# than reading per request the way the model name is read. Changing one has no
# effect on a client that already exists, so every caller that persists one of
# these has to rebuild the provider afterwards.
#
# Declared here because this is the module that bakes them in: the /config
# handler, the settings panel and the guided setup all derive their lists from
# this one, so adding a setting to the factory cannot leave a caller behind.
PROVIDER_BAKED_SETTINGS = frozenset({"api_key", "api_mode", "base_url"})


def create_provider(config: AppConfig) -> ModelProvider:
    if config.api_mode == "responses":
        return OpenAIResponsesProvider(config)
    if config.api_mode == "completions":
        return OpenAIChatCompletionsProvider(config)
    if config.api_mode == "ollama":
        return NativeOllamaProvider(config)
    raise ConfigurationError(f"Unsupported api_mode: {config.api_mode}")
