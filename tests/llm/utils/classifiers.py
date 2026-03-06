import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Union

import openai
from autoevals import LLMClassifier, init
from braintrust import Span, SpanTypeAttribute
from braintrust.oai import wrap_openai
from braintrust_core.score import Score

from tests.llm.utils.test_case_utils import _model_list_exists, create_eval_llm
from tests.llm.utils.test_env_vars import (
    AZURE_API_BASE,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    CLASSIFIER_MODEL,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY,
)


@dataclass
class ClassifierModelParams:
    model: str
    api_key: Optional[str]
    api_base: Optional[str]
    api_version: Optional[str]

    @property
    def is_azure(self) -> bool:
        return bool(self.api_base and self.api_version)


def get_classifier_model_params() -> ClassifierModelParams:
    """Get classifier model parameters from model list or environment variables."""
    if _model_list_exists():
        llm = create_eval_llm(CLASSIFIER_MODEL)
        model_for_api = llm.model
        client_api_key = llm.api_key
        client_base_url = llm.api_base
        client_api_version = llm.api_version
    else:
        if not OPENAI_API_KEY and not AZURE_API_KEY and not OPENROUTER_API_KEY:
            raise ValueError(
                "No API key found (AZURE_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY)"
            )
        model_for_api = CLASSIFIER_MODEL
        if AZURE_API_BASE:
            client_api_key = AZURE_API_KEY
            client_base_url = AZURE_API_BASE
        elif OPENAI_API_KEY:
            client_api_key = OPENAI_API_KEY
            client_base_url = OPENAI_API_BASE
        elif OPENROUTER_API_KEY:
            client_api_key = OPENROUTER_API_KEY
            client_base_url = OPENROUTER_API_BASE or "https://openrouter.ai/api/v1"
        else:
            raise ValueError(
                "No API key found (AZURE_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY)"
            )
        client_api_version = AZURE_API_VERSION

        # Strip provider prefixes for API calls
        if AZURE_API_BASE and CLASSIFIER_MODEL.startswith("azure/"):
            if len(CLASSIFIER_MODEL.split("/")) != 2:
                raise ValueError(
                    f"Current classifier model '{CLASSIFIER_MODEL}' does not meet the pattern 'azure/<deployment-name>' when using Azure OpenAI."
                )
            model_for_api = CLASSIFIER_MODEL.split("/", 1)[1]
        elif CLASSIFIER_MODEL.startswith("openrouter/"):
            # Strip "openrouter/" prefix - OpenRouter expects "openai/gpt-4.1" not "openrouter/openai/gpt-4.1"
            model_for_api = CLASSIFIER_MODEL.split("/", 1)[1]

    return ClassifierModelParams(
        model=model_for_api,
        api_key=client_api_key,
        api_base=client_base_url,
        api_version=client_api_version,
    )


class TextBasedClassifier:
    """Simple text-based classifier that doesn't use tool calling.

    This works better with non-OpenAI providers like OpenRouter/Claude that
    may not handle function calling correctly.
    """

    TEXT_PROMPT_SUFFIX = """
Think step by step, then give your final answer.

CRITICAL: You MUST end your response with EXACTLY this format on the last line:
Final Answer: [A or B]

Do NOT end with any other text after the Final Answer line.
"""

    def __init__(
        self,
        name,
        prompt_template,
        choice_scores,
        model="gpt-4o",
        use_cot=True,
        max_tokens=512,
        temperature=0,
        api_key=None,
        base_url=None,
        **extra_render_args,
    ):
        self.name = name
        self.prompt_template = prompt_template
        self.choice_scores = choice_scores
        self.model = model
        self.use_cot = use_cot
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.api_key = api_key
        self.base_url = base_url
        self.extra_render_args = extra_render_args
        self.choice_strings = list(choice_scores.keys())

    def _render_prompt(self, output, expected, **kwargs):
        import chevron

        render_args = {
            "output": output,
            "expected": expected,
            "__choices": self.choice_strings,
            **self.extra_render_args,
            **kwargs,
        }
        prompt = self.prompt_template
        if self.use_cot:
            prompt += self.TEXT_PROMPT_SUFFIX.format(
                choices=", ".join(self.choice_strings)
            )
        return chevron.render(prompt, render_args, warn=True)

    def _extract_choice(self, content):
        """Extract choice from text content."""
        if not content:
            return None, None

        # Look for "Final Answer: X" pattern first (most reliable)
        final_answer_match = re.search(
            r"final\s*answer[:\s]+([A-Z])\b", content, re.IGNORECASE
        )
        if final_answer_match:
            choice = final_answer_match.group(1).upper()
            if choice in self.choice_strings:
                return choice, content

        # Look for choice at the end of the response
        lines = content.strip().split("\n")
        for line in reversed(lines[-5:]):  # Check last 5 lines
            line = line.strip()
            for choice in self.choice_strings:
                if line == choice or line.endswith(f": {choice}") or line.endswith(f":{choice}"):
                    return choice, content
                # Match patterns like "A." or "A:" or "A -" at start of line
                if re.match(rf"^{re.escape(choice)}[\s.:\-]", line):
                    return choice, content

        # Look for explicit patterns anywhere
        for choice in self.choice_strings:
            patterns = [
                rf"\bchoice[:\s]+{re.escape(choice)}\b",
                rf"\bselect[:\s]+{re.escape(choice)}\b",
                rf"\banswer[:\s]+{re.escape(choice)}\b",
                rf"\b{re.escape(choice)}\b\s*$",
            ]
            for pattern in patterns:
                if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
                    return choice, content

        # For A/B choices, look for semantic indicators
        if set(self.choice_strings) == {"A", "B"}:
            # Check for "all elements present" indicators -> A
            all_present_patterns = [
                r"all\s+(?:elements?\s+)?(?:are\s+)?present",
                r"all\s+(?:the\s+)?(?:expected\s+)?(?:elements?\s+)?(?:are\s+)?(?:found|included|covered)",
                r"(?:fully|completely)\s+match",
                r"matches?\s+(?:all|the)\s+expected",
            ]
            for pattern in all_present_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    return "A", content

            # Check for "missing/some elements" indicators -> B
            missing_patterns = [
                r"(?:some|not all)\s+elements?\s+(?:are\s+)?(?:missing|absent|not present)",
                r"(?:missing|lacks?|doesn't have)\s+(?:some|the following)",
                r"only\s+(?:some|partial)",
            ]
            for pattern in missing_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    return "B", content

        # Count occurrences as last resort for binary choices
        if len(self.choice_strings) == 2:
            counts = {}
            for choice in self.choice_strings:
                counts[choice] = len(
                    re.findall(rf"\b{re.escape(choice)}\b", content, re.IGNORECASE)
                )
            if counts[self.choice_strings[0]] != counts[self.choice_strings[1]]:
                winner = max(counts, key=counts.get)
                return winner, content

        return None, content

    def eval(self, output, expected, **kwargs):
        """Evaluate the output against expected."""
        return self._run_eval_sync(output, expected, **kwargs)

    def __call__(self, output, expected, **kwargs):
        """Make the classifier callable."""
        return self.eval(output=output, expected=expected, **kwargs)

    def _run_eval_sync(self, output, expected, **kwargs):
        """Run evaluation synchronously."""
        prompt = self._render_prompt(output=output, expected=expected, **kwargs)

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

        content = response.choices[0].message.content or ""
        choice, rationale = self._extract_choice(content)

        if choice is None:
            raise ValueError(
                f"Could not extract choice from response. "
                f"Expected one of {self.choice_strings}. Response: {content[:500]}"
            )

        metadata = {"choice": choice, "rationale": rationale}
        score = self.choice_scores.get(choice, 0)
        return Score(name=self.name, score=score, metadata=metadata)


classifier_model = CLASSIFIER_MODEL


def create_llm_client():
    """Create OpenAI/Azure client with same logic used by tests"""
    params = get_classifier_model_params()

    if params.is_azure:
        deployment = (
            params.model.split("/", 1)[1] if "/" in params.model else params.model
        )
        if not params.api_key:
            raise ValueError("No AZURE_API_KEY")
        client = openai.AzureOpenAI(
            azure_endpoint=params.api_base,
            azure_deployment=deployment,
            api_version=params.api_version,
            api_key=params.api_key,
        )
        model_for_api = deployment
    else:
        if not params.api_key:
            raise ValueError("No OPENAI_API_KEY or OPENROUTER_API_KEY")
        client = openai.OpenAI(api_key=params.api_key, base_url=params.api_base)
        model_for_api = params.model

    return client, model_for_api


# Register client with autoevals
try:
    client, _ = create_llm_client()
    params = get_classifier_model_params()
    if params.is_azure:
        wrapped = wrap_openai(client)
        init(wrapped)  # type: ignore
except Exception:
    # If client creation fails, individual tests will be skipped due to the fixture, so client = None is OK
    client = None


def evaluate_correctness(
    expected_elements: Union[str, List[str]],
    output: Optional[str],
    parent_span: Optional[Span],
    caplog,
    evaluation_type: str = "strict",
):
    expected_elements_str = "\n- ".join(expected_elements)

    caplog.set_level("INFO", logger="classifier")
    logger = logging.getLogger("classifier")

    if isinstance(expected_elements, str):
        expected_elements = [expected_elements]
    expected_elements_str = "\n- ".join(expected_elements)

    prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The correctness is defined by the presence of EXPECTED ELEMENTS in the OUTPUT.
Make a judgement call whether each ELEMENT sufficiently matches the OUTPUT. ELEMENTS do
not need to appear verbatim or be a perfect match but their essence should be
present in the whole OUTPUT, even if it spans multiple sentences.

# EXPECTED ELEMENTS

- {{expected}}

# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED ELEMENTS present in the OUTPUT.
Possible choices:
- A: All elements are presents
- B: Either no element is present or only some but not all elements are present
"""

    if evaluation_type == "loose":
        prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The correctness is defined by the presence of EXPECTED in the OUTPUT.
Make a judgement call whether each ELEMENT sufficiently matches the OUTPUT. ELEMENTS do
not need to appear verbatim or be a perfect match but their essence should be
present in the whole OUTPUT, even if it spans multiple sentences.

# EXPECTED

{{expected}}

# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED presence in the OUTPUT.
Possible choices:
- A: The OUTPUT reasonably matches the EXPECTED content
- B: The OUTPUT does not match the EXPECTED content
"""
    params = get_classifier_model_params()
    if params.is_azure:
        logger.info(
            f"Evaluating correctness with Azure OpenAI; base_url={params.api_base}, api_version={params.api_version}, model={params.model}, api_key ending with: {params.api_key[-4:] if params.api_key else None}"
        )
        logger.info(
            "To use OpenAI instead, unset the environment variable AZURE_API_BASE"
        )
    else:
        logger.info(
            f"Evaluating correctness with OpenAI; model={params.model}, api_key ending with: {params.api_key[-4:] if params.api_key else None}"
        )
        logger.info(
            "To use Azure OpenAI instead, set the environment variables AZURE_API_BASE, AZURE_API_VERSION, and AZURE_API_KEY"
        )

    classifier = TextBasedClassifier(
        name="Correctness",
        prompt_template=prompt_prefix,
        choice_scores={"A": 1, "B": 0},
        use_cot=True,
        model=params.model,
        max_tokens=1024,
        api_key=params.api_key if not params.is_azure else None,
        base_url=params.api_base if not params.is_azure else None,
    )
    if parent_span:
        with parent_span.start_span(
            name="Correctness", type=SpanTypeAttribute.SCORE
        ) as span:
            correctness_eval = classifier(
                input=prompt_prefix, output=output, expected=expected_elements_str
            )

            span.log(
                input=prompt_prefix,
                output=correctness_eval.metadata.get("rationale", ""),
                expected=expected_elements_str,
                scores={
                    "correctness": correctness_eval.score,
                },
                metadata=correctness_eval.metadata,
            )
            return correctness_eval
    else:
        return classifier(
            input=prompt_prefix, output=output, expected=expected_elements_str
        )


def evaluate_sections(
    sections: dict[str, bool], output: Optional[str], parent_span: Optional[Span]
):
    expected_sections = [section for section, expected in sections.items() if expected]
    expected_sections_str = "\n".join([f"- {section}" for section in expected_sections])
    if not expected_sections_str:
        expected_sections_str = "<No section is expected>"

    unexpected_sections = [
        section for section, expected in sections.items() if not expected
    ]
    unexpected_sections_str = "\n".join(
        [f"- {section}" for section in unexpected_sections]
    )
    if not unexpected_sections_str:
        unexpected_sections_str = "<No element>"

    prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.

The LLM output is expected to be split into sections. Typically each section is represented by a markdown title `# <section title>`.
Some sections are expected and should be populated in the output. Some sections are unexpected and should not be present in the outpout
(i.e. there is no such title: `# <unexpected section`)

If there are <No element> in EXPECTED SECTIONS assume the OUTPUT has all appropriate EXPECTED SECTIONS.
If there are <No element> in UNEXPECTED SECTIONS assume the OUTPUT has no UNEXPECTED SECTIONS.


# EXPECTED SECTIONS

{{expected}}


# UNEXPECTED SECTIONS

{{input}}


# OUTPUT

{{output}}


Return a choice based on the number of EXPECTED ELEMENTS present in the OUTPUT.
Possible choices:
A. One or more of the EXPECTED SECTIONS is missing and one or more of the UNEXPECTED SECTIONS is present
B. All EXPECTED SECTIONS are present in the OUTPUT and no UNEXPECTED SECTIONS is present in the output
"""

    classifier = LLMClassifier(
        name="sections",
        prompt_template=prompt_prefix,
        choice_scores={"A": 0, "B": 1},
        use_cot=True,
        model=classifier_model,
    )
    if parent_span:
        with parent_span.start_span(
            name="Sections", type=SpanTypeAttribute.SCORE
        ) as span:
            correctness_eval = classifier(
                input=unexpected_sections_str,
                output=output,
                expected=expected_sections_str,
            )

            span.log(
                input=prompt_prefix,
                output=correctness_eval.metadata.get("rationale", ""),
                expected=expected_sections_str,
                scores={
                    "sections": correctness_eval.score,
                },
                metadata=correctness_eval.metadata,
            )

            return correctness_eval
    else:
        return classifier(
            input=unexpected_sections_str, output=output, expected=expected_sections_str
        )
