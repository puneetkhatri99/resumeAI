"""AI extraction of structured details from a CV's text, through OpenAI's structured outputs.

CV text goes to OpenAI with store=False and is never logged; only exception types are.
Other features import ResumeData and the errors from this module (rule 7).
"""

import logging

import openai
import pydantic
from openai import OpenAI

from app.core.config import settings
from app.features.resume_structuring.schemas import ResumeData

logger = logging.getLogger(__name__)

# OpenAI removes gpt-4.1-mini from the API on 2027-01-14 (it recommends gpt-6-luna).
# The user chose to keep it for now: it must be switched before that date.
OPENAI_MODEL = "gpt-4.1-mini"
MAX_CV_TEXT_CHARS = 50_000
INSTRUCTIONS = (
    "Extract the candidate's details from the CV text into the given schema. "
    "Copy text exactly as it is written in the CV. Don't rewrite, summarise, translate or "
    "invent anything. Keep dates exactly as written. "
    "When something is missing from the CV, use null or an empty list. "
    "When the CV doesn't state the candidate's name or email, use an empty string for it. "
    "For location, give the candidate's current location as written in the CV, or null if it "
    "isn't stated."
)

TOO_LONG_MESSAGE = f"The CV text is too long (over {MAX_CV_TEXT_CHARS:,} characters)."
MISSING_CONTACT_MESSAGE = "Couldn't find the candidate's name or email in the CV."
AI_FAILED_MESSAGE = "AI extraction failed, please try again."

# timeout: the output is about as long as the CV, so a dense 4-page CV takes ~35-70 s; the SDK
# default is 600 s. max_retries=0: a retry spends a paid request; the recruiter resubmits a 502.
client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=120, max_retries=0)


class ResumeStructuringError(Exception):
    """The CV text can't be structured: it is too long, or has no name or email (HTTP 422)."""


class AiExtractionError(Exception):
    """The OpenAI call failed, was refused or returned unusable output (HTTP 502)."""


def extract_resume_data(cv_text: str) -> ResumeData:
    """Have OpenAI extract the structured details from a CV's text.

    Raises ResumeStructuringError when the text is over MAX_CV_TEXT_CHARS (before any AI call)
    or the result has a blank name or email. Raises AiExtractionError when the call fails or
    returns nothing usable.
    """
    if len(cv_text) > MAX_CV_TEXT_CHARS:
        raise ResumeStructuringError(TOO_LONG_MESSAGE)
    try:
        response = client.responses.parse(
            model=OPENAI_MODEL,
            instructions=INSTRUCTIONS,
            input=cv_text,
            text_format=ResumeData,
            temperature=0,
            store=False,
        )
    # Output cut off by the token limit or a content filter isn't valid JSON, so the SDK raises
    # ValidationError, whose message contains CV text: hence `from None` and only the type logged.
    except (openai.OpenAIError, pydantic.ValidationError) as exc:
        logger.warning("AI extraction failed: %s", type(exc).__name__)
        raise AiExtractionError(AI_FAILED_MESSAGE) from None

    data = response.output_parsed
    if data is None:
        logger.warning("AI extraction failed: no parsed output (a refusal)")
        raise AiExtractionError(AI_FAILED_MESSAGE)
    if not data.name.strip() or not data.email.strip():
        raise ResumeStructuringError(MISSING_CONTACT_MESSAGE)
    return data
