"""Client for an OpenJev decision server.

OpenJev answers typed questions about a piece of text - a choice among
options, yes/no - with a probability for every option, read from the model's
first output position. No free text comes back, so there is nothing to parse
and nothing to hallucinate, and the probability says how far each answer can
be trusted.

Each question is one forward pass, and a choice takes many options in that one
pass - so one choice question is both cheaper and sharper than a handful of
yes/no ones about the same text.

Two kinds of server answer it. OpenJev's own helper (in front of vLLM) takes
the questions whole on ``POST /v1/systemone``. A plain llama.cpp server holding
the GGUF build has no such route, so there the readout is done here: the prompt
the model was trained on, one token asked for, the option letters read off its
log-probabilities. Which one is on the other end is found out by asking.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

# How the server is spoken to. "auto" tries the helper's route and settles on
# the llama.cpp readout when the server does not have it.
API_AUTO = "auto"
API_SYSTEMONE = "systemone"
API_LLAMACPP = "llamacpp"
SUPPORTED_APIS = {API_AUTO, API_SYSTEMONE, API_LLAMACPP}

# The readout below mirrors OpenJev's helper (helper/shim.py in the model
# repository): the prompt wording is what the model was tuned on, and the
# constants are the calibration its model card was measured with.
_LETTERS = [chr(65 + i) for i in range(26)] + [chr(97 + i) for i in range(26)]
_READOUT_T = 0.85
_NOUL_T = 1.829074
_NOUL_BIAS = 0.0
# What an option letter outside the server's top tokens is taken to score.
_MISSING_LOGPROB = -30.0
_TOP_LOGPROBS = 20


class OpenJevError(RuntimeError):
    pass


def noul(instructions: str) -> dict[str, Any]:
    """A yes/no question; answered with the probability of yes."""
    return {"type": "noul", "instructions": instructions}


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """One of ``criteria``'s keys; the values describe each option."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


class OpenJevClient:
    def __init__(
        self,
        *,
        base_url: str,
        api: str = API_AUTO,
        api_key: str = "",
        timeout_s: float = 8.0,
        retry_after_s: float = 30.0,
        ssl_verification: bool = False,
        transport: Any = None,
    ) -> None:
        # Either spelling of the address works: with or without the /v1.
        self._base = base_url.rstrip("/").removesuffix("/v1")
        self._api = api
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._retry_after_s = retry_after_s
        self._ssl_verification = ssl_verification
        self._transport = transport
        self._down_until = 0.0

    @property
    def available(self) -> bool:
        """False for a while after a failed request, so callers skip the wait."""
        return time.monotonic() >= self._down_until

    @property
    def api(self) -> str:
        """How the server is being spoken to; "auto" until the first answer."""
        return self._api

    async def system_one(
        self, state: str, questions: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Answers keyed by question id, within ``timeout_s`` for all of them."""
        import httpx

        try:
            # One short-lived client per call: a turn asks a couple of times,
            # so there is no connection worth keeping.
            async with httpx.AsyncClient(
                verify=self._ssl_verification,
                timeout=self._timeout_s,
                transport=self._transport,
                headers=(
                    {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
                ),
            ) as http:
                return await asyncio.wait_for(
                    self._answer(http, state, questions), timeout=self._timeout_s
                )
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            self._down_until = time.monotonic() + self._retry_after_s
            raise OpenJevError(f"OpenJev request failed: {exc or type(exc).__name__}") from exc

    async def _answer(
        self, http: Any, state: str, questions: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        if self._api != API_LLAMACPP:
            response = await http.post(
                f"{self._base}/v1/systemone",
                # The helper serves one model and ignores the name; a stricter
                # front end in its place may require one.
                json={"model": "openjev", "state": state, "questions": questions},
            )
            if not (self._api == API_AUTO and response.status_code in {404, 405}):
                response.raise_for_status()
                body = response.json()
                answers = body.get("answers") if isinstance(body, dict) else None
                if not isinstance(answers, dict):
                    raise OpenJevError("OpenJev reply carries no answers.")
                self._api = API_SYSTEMONE
                return answers
            # No such route: a bare model server. Remembered, so the 404 is
            # paid once per client rather than once per decision.
            self._api = API_LLAMACPP
        # One at a time: a llama.cpp server has a handful of slots, and each
        # request costs about the same however short it is - measured, the
        # shared state is not reused between them. So the cost of a decision
        # is its number of questions, and callers keep that to one.
        answers = {}
        for question_id, question in questions.items():
            answers[question_id] = await self._readout(http, state, question_id, question)
        return answers

    async def _readout(
        self, http: Any, state: str, question_id: str, question: dict[str, Any]
    ) -> dict[str, Any]:
        kind = question.get("type")
        instructions = str(question.get("instructions") or "")
        if kind == "choice":
            options = [(str(k), str(v or "")) for k, v in (question.get("criteria") or {}).items()]
        elif kind == "noul":
            options = [("yes", "The statement is true."), ("no", "The statement is false.")]
        else:
            raise OpenJevError(f"Question {question_id!r} has an unsupported type: {kind!r}.")
        if not 2 <= len(options) <= len(_LETTERS):
            raise OpenJevError(f"Question {question_id!r} needs between 2 and 52 options.")

        lines = "\n".join(
            f"[{_LETTERS[i]}] {key}: {description}" for i, (key, description) in enumerate(options)
        )
        prompt = (
            f"State:\n{state}\n\nQuestion: {instructions}\nOptions:\n{lines}\n\n"
            "Answer with the letter of the best option only."
        )
        response = await http.post(
            f"{self._base}/v1/chat/completions",
            json={
                "model": "openjev",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1,
                "temperature": 0,
                "logprobs": True,
                "top_logprobs": max(_TOP_LOGPROBS, len(options)),
                # The first token has to be the answer, not the opening of a
                # think block.
                "chat_template_kwargs": {"enable_thinking": False},
                "cache_prompt": True,
            },
        )
        response.raise_for_status()
        probabilities = _letter_probabilities(response.json(), len(options), question_id)

        if kind == "noul":
            yes = min(max(probabilities[0], 1e-4), 1 - 1e-4)
            z = math.log(yes / (1 - yes)) / _NOUL_T + _NOUL_BIAS
            return {"type": "noul", "noul": round(1 / (1 + math.exp(-z)), 4)}
        best = max(range(len(options)), key=probabilities.__getitem__)
        uniform = 1.0 / len(options)
        return {
            "type": "choice",
            "choice": options[best][0],
            "probabilities": {
                key: round(p, 4) for (key, _), p in zip(options, probabilities, strict=True)
            },
            "confidence": round(max(0.0, (probabilities[best] - uniform) / (1.0 - uniform)), 4),
        }


def _letter_probabilities(body: Any, count: int, question_id: str) -> list[float]:
    """Softmax over the first ``count`` option letters at the first output position."""
    try:
        top = body["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenJevError(
            "The server returned no token probabilities; it has to serve "
            "/v1/chat/completions with logprobs."
        ) from exc
    scores: dict[str, float] = {}
    for entry in top:
        token, logprob = str(entry.get("token", "")), entry.get("logprob")
        if not isinstance(logprob, (int, float)):
            continue
        # The exact letter wins: " A" must not overwrite "A".
        letter = token if token in _LETTERS else token.strip()
        if letter in _LETTERS and (letter not in scores or token in _LETTERS):
            scores[letter] = float(logprob)
    letters = _LETTERS[:count]
    if not any(letter in scores for letter in letters):
        # Nothing to normalise: the model opened with something else entirely,
        # which is what a model that is not OpenJev, or one left thinking, does.
        raise OpenJevError(
            f"No option letter among the model's first tokens for {question_id!r}; "
            "is this an OpenJev model, with thinking off?"
        )
    z = [scores.get(letter, _MISSING_LOGPROB) / _READOUT_T for letter in letters]
    peak = max(z)
    weights = [math.exp(value - peak) for value in z]
    total = sum(weights)
    return [weight / total for weight in weights]


def noul_probability(answers: dict[str, Any], question_id: str) -> float:
    answer = answers.get(question_id)
    value = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpenJevError(f"OpenJev did not answer {question_id!r}.")
    return float(value)


def confident(probability: float, confidence: float) -> bool | None:
    """Yes, no, or ``None`` when the engine is not sure enough to be believed."""
    if probability >= confidence:
        return True
    if probability <= 1.0 - confidence:
        return False
    return None


def chosen(answers: dict[str, Any], question_id: str) -> tuple[str, float]:
    """The winning option of a choice and its probability."""
    answer = answers.get(question_id)
    value = answer.get("choice") if isinstance(answer, dict) else None
    if not isinstance(value, str):
        raise OpenJevError(f"OpenJev did not answer {question_id!r}.")
    probabilities = answer.get("probabilities")
    probability = probabilities.get(value) if isinstance(probabilities, dict) else None
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise OpenJevError(f"OpenJev gave no probability for {question_id!r}.")
    return value, float(probability)
