import json

from code_ai.prompts import ARCHITECTURE_PRINCIPLES

# Asking a reviewer for "confidence" without saying what the numbers mean gets a
# number back, not a judgement: everything lands at 70-80 and the scale carries
# no information. Anchoring each point to a described situation is what makes the
# threshold below meaningful, and the threshold is what turns a long list of
# maybes into a short list worth reading.
REVIEW_CONFIDENCE_RUBRIC = (
    "Rate every issue you are considering from 0 to 100 for confidence:\n"
    "- 0: does not survive scrutiny, or the problem pre-dates this code.\n"
    "- 25: might be real, might be a false positive; if it is a matter of style, "
    "no project convention actually demands it.\n"
    "- 50: real but minor - a nitpick, or something that rarely happens in "
    "practice.\n"
    "- 75: checked twice and very likely to be hit in practice; it affects "
    "behaviour, or a stated project convention requires it.\n"
    "- 100: certain, with the evidence in front of you confirming it.\n"
    "Report only issues at 80 or above, and give each one its score. A short "
    "list of real problems is worth more than a long list that has to be "
    "triaged. If nothing clears the bar, say the code meets the standard and "
    "explain briefly what you checked.\n"
    "Do not report: problems that already existed outside this change; anything "
    "a compiler, linter, or type checker would flag on its own; issues that "
    "depend on inputs or state you cannot see; or preferences the project has "
    "not written down.\n"
)


def build_refutation_prompt(findings: list[dict[str, str]]) -> str:
    """Ask a second pass to disprove the first pass's findings.

    A reviewer asked to check its own work agrees with itself. Asked instead to
    argue the opposite case, it has something to actually do, and the findings
    that survive an honest attempt at refutation are the ones worth showing.

    The default is deliberately asymmetric - survival, not rejection. The point
    is to remove what cannot be defended, not to talk the reviewer out of real
    problems, and an eager refuter is as useless as a credulous reviewer.
    """

    numbered = json.dumps(
        [{"index": index, **finding} for index, finding in enumerate(findings)],
        indent=2,
        ensure_ascii=False,
    )
    return (
        "A previous review produced the candidate findings below. Your job is to "
        "argue against each one and see which survive.\n\n"
        f"{numbered}\n\n"
        "Take each finding in turn and try to refute it. Refute it only when you "
        "can point to the concrete reason, in the code you were given:\n"
        "- the problem is already handled by a check, guard, or conversion "
        "elsewhere in the same code;\n"
        "- the code it describes is not actually there, or does not do what the "
        "finding says it does;\n"
        "- the problem pre-dates this change rather than being introduced by it;\n"
        "- it is a matter of taste, or a rule no project convention states;\n"
        "- it can only happen under conditions the code makes impossible.\n"
        "Do not refute on the grounds that a problem seems unlikely or minor, and "
        "do not speculate about code you were not shown. When you cannot make a "
        "concrete case against a finding, it survives - that is the default.\n\n"
        'Reply with JSON only: {"survived": [<indices you could not refute>], '
        '"refuted": [{"index": <n>, "reason": "<the concrete case against it>"}]}'
    )


ARCHITECTURE_REVIEW_PROMPT = (
    "You are reviewing the architecture of code that was just implemented. Judge how "
    "well-structured and organized it is, independently of programming language, "
    "framework, or paradigm. Evaluate against these properties of good architecture:\n"
    + ARCHITECTURE_PRINCIPLES
    + "Respect the conventions already present in the surrounding codebase rather than "
    "imposing a foreign style. For each issue give its location, why it weakens the "
    "architecture, and a concrete, minimal change to fix it. Prioritize structural risks "
    "over cosmetic preferences."
)
CODE_REVIEW_PROMPT = (
    "Review the supplied code context for correctness, robustness, security, and missing "
    "verification. Give each finding its location and what goes wrong, concretely enough "
    "that someone can check whether you are right.\n" + REVIEW_CONFIDENCE_RUBRIC
)
BUILD_REVIEW_PROMPT = (
    "Review the supplied build or test output and identify actionable build failures."
)
TEST_REVIEW_PROMPT = (
    "You are reviewing test cases to judge whether they are well constructed, "
    "independently of programming language or test framework. Evaluate against these "
    "properties of good tests:\n"
    "- Single intent: each test verifies one behavior and its name states that behavior "
    "clearly; a failure points directly at what broke.\n"
    "- No unnecessary steps: setup, actions, and assertions are the minimum needed to "
    "exercise the behavior. Flag redundant setup, irrelevant assertions, dead steps, "
    "copy-pasted boilerplate, and over-mocking that tests the mock instead of the code.\n"
    "- Clear structure: arrange/act/assert (or given/when/then) is easy to follow.\n"
    "- Meaningful assertions: assert on observable behavior and outcomes, not on internal "
    "implementation details that make the test brittle.\n"
    "- Determinism and isolation: tests are independent of execution order and shared "
    "mutable state, and control time, randomness, network, and I/O so they cannot flake.\n"
    "- Coverage of what matters: happy path, meaningful edge cases, error and failure "
    "conditions, and boundary values — without redundant near-duplicate cases.\n"
    "- Readability: a reader understands what is guaranteed without running the test.\n"
    "Device and hardware tests deserve special attention: they must cover real device "
    "states and lifecycle (startup, sleep, resume, shutdown), permissions, connectivity "
    "and offline/interrupted scenarios, varying device configurations (screen sizes, "
    "capabilities, OS/firmware versions), resource acquisition and cleanup (no leaked "
    "handles, sockets, or sensors), and timing/concurrency on the device. Flag device "
    "tests that only check the happy path or assume an always-available, ideal device.\n"
    "For each issue give the test's location, what is wrong, and a concrete way to "
    "simplify or strengthen it. Note tests that are missing for important behavior."
)
DOCUMENTATION_PROMPT = (
    "You are producing documentation for the supplied code or design context. Write it to "
    "be genuinely useful, independently of programming language or framework, following "
    "these principles:\n"
    "- Lead with purpose: state what the thing is and why it exists before how it works.\n"
    "- Be accurate to the supplied material: describe only behavior, parameters, return "
    "values, and side effects that are actually present. Never invent APIs, options, or "
    "guarantees; if something is unknown, say so explicitly.\n"
    "- Structure for scanning: a short overview, then usage with a concrete example, then "
    "details (parameters, return values, errors, edge cases, and important constraints).\n"
    "- Be concise and concrete: prefer precise examples over vague prose; avoid repeating "
    "what the code already makes obvious.\n"
    "- Match the audience: enough context for a newcomer, enough precision for a "
    "maintainer.\n"
    "Return the documentation itself as clean Markdown, ready to be saved to a file. Do "
    "not include meta-commentary about the review process."
)
