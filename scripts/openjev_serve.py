"""Run OpenJev's decision helper for trying out Code-AI's decision layer.

Test rig only: nothing in Code-AI imports or launches this, and the shipped
binary does not carry it. Code-AI just talks to whatever answers on the Jev
base URL set in its settings.

OpenJev (huggingface.co/openjev/openjev) is served in two parts: the model
behind vLLM, and a small helper in front of it that answers /v1/systemone.
This starts the helper with the settings its model card was measured with -
they are environment variables, which is awkward to type on Windows.

    hf download openjev/openjev --local-dir openjev          # once, ~54 GB
    python scripts/openjev_serve.py --vllm-command           # what to run on the GPU box
    python scripts/openjev_serve.py --model-dir openjev      # the helper, on :3000
    python scripts/openjev_serve.py --check                  # ask it one question

The helper needs `pip install openai transformers`. To expose it beyond this
machine pass --host 0.0.0.0 with SHIM_TOKEN set; the same token goes in
Code-AI's "Jev token" setting.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 3000
DEFAULT_BACKEND = "http://localhost:8000/v1"

# The calibration the model card's numbers were measured with. Change one and
# the probabilities stop meaning what Code-AI's thresholds assume.
_MEASURED = {
    "READOUT_T": "0.85",
    "READOUT_NOUL_T": "1.829074",
    "READOUT_NOUL_BIAS": "0",
    "READOUT_TARGETED": "1",
    "READOUT_INSTR_STYLE": "pyrepr",
    "SHIM_STAGGER": "1",
}

_VLLM_COMMAND = (
    "vllm serve {model_dir} --host 127.0.0.1 --served-model-name qwen --port 8000 "
    "--enable-prefix-caching --max-model-len 16384 --gpu-memory-utilization 0.90 "
    "--limit-mm-per-prompt '{{\"image\":1}}' --trust-remote-code --max-num-seqs 256 "
    "--max-logprobs 64 --gdn-prefill-backend triton --quantization fp8"
)

_SAMPLE = {
    "model": "openjev",
    "state": "crie um arquivo hello.py que imprime ola",
    "questions": {
        "intent": {
            "type": "choice",
            "instructions": "What is the user mainly asking a coding assistant to do?",
            "criteria": {
                "change": "Create or change files or code",
                "question": "Answer a question",
                "chat": "Small talk",
            },
        },
        "local": {
            "type": "noul",
            "instructions": "Does this require the files of the user's project?",
        },
    },
}


def serve(model_dir: Path, backend: str, host: str, port: int) -> int:
    shim = model_dir / "helper" / "shim.py"
    if not shim.is_file():
        print(
            f"{shim} not found. Download the model first:\n"
            f"    hf download openjev/openjev --local-dir {model_dir}",
            file=sys.stderr,
        )
        return 1
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("SHIM_TOKEN"):
        # The helper would start and serve anyone who can reach the port.
        print("Set SHIM_TOKEN before exposing the helper beyond loopback.", file=sys.stderr)
        return 1
    env = {**os.environ, **_MEASURED, "VLLM": backend, "TOKENIZER": str(model_dir)}
    command = [sys.executable, str(shim), "--host", host, "--port", str(port)]
    print(f"helper on http://{host}:{port} -> {backend}")
    try:
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        return 0


def check(url: str) -> int:
    endpoint = url.rstrip("/") + "/v1/systemone"
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("SHIM_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        endpoint, data=json.dumps(_SAMPLE).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"No answer from {endpoint}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(body, indent=2, ensure_ascii=False))
    answers = body.get("answers") if isinstance(body, dict) else None
    intent = (answers or {}).get("intent", {})
    if not isinstance(intent.get("choice"), str) or not isinstance(
        intent.get("probabilities"), dict
    ):
        print("Unexpected reply: answers.intent has no choice/probabilities.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", type=Path, default=Path("openjev"))
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="vLLM's OpenAI base URL")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vllm-command", action="store_true", help="print the vLLM command")
    parser.add_argument("--check", action="store_true", help="query a running helper and exit")
    parser.add_argument("--url", default="", help="helper to --check (default: this host/port)")
    args = parser.parse_args()
    if args.vllm_command:
        print(_VLLM_COMMAND.format(model_dir=args.model_dir))
        return 0
    if args.check:
        return check(args.url or f"http://127.0.0.1:{args.port}")
    return serve(args.model_dir, args.backend, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
