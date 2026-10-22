# resume_structuring

**Purpose:** use AI (OpenAI) to pull the important details out of a CV's text: name, email, phone, current location, links, skills, and lists of experience, education, projects and certificates. It runs inside the `resume_intake` upload, after the CV text is read; there is no endpoint of its own. Design: [docs/features/resume_structuring.md](../../../../docs/features/resume_structuring.md).

## API
No router. Other features call the service (rule 7):

`extract_resume_data(cv_text: str) -> ResumeData`
1. Text over `MAX_CV_TEXT_CHARS` (50,000) raises `ResumeStructuringError` before any AI call.
2. Calls `client.responses.parse(model=OPENAI_MODEL, instructions=INSTRUCTIONS, input=cv_text, text_format=ResumeData, temperature=0, store=False)`.
3. `openai.OpenAIError` (timeout, bad key, outage, rate limit) and `pydantic.ValidationError` (output cut off by the token limit or a content filter isn't valid JSON) become `AiExtractionError`, raised `from None` because the ValidationError's message contains CV text. An empty `output_parsed` (a refusal) is also an `AiExtractionError`.
4. A blank name or email raises `ResumeStructuringError`.

| Error | HTTP status (mapped by the caller) | Message |
|---|---|---|
| `ResumeStructuringError` | 422 | "The CV text is too long (over 50,000 characters)." / "Couldn't find the candidate's name or email in the CV." |
| `AiExtractionError` | 502 | "AI extraction failed, please try again." |

`ResumeData` is imported into `service.py`, so other features import it, the errors and the function all from `service`.

**OpenAI settings:**
- **Model:** `OPENAI_MODEL = "gpt-4.1-mini"`. OpenAI removes it from the API on **2027-01-14** (it recommends `gpt-6-luna`). The user chose to keep it for now; it **must be switched before that date**.
- **Client:** one module-level `OpenAI(api_key=settings.openai_api_key, timeout=120, max_retries=0)`. The SDK's default timeout is 600 s; a dense 4-page CV takes about 35–70 s. No retries: a retry spends a paid request, and the recruiter can resubmit after a 502.
- **Key:** `OPENAI_API_KEY` in `backend/.env` is required; the app won't start without it.
- **Instructions:** copy text exactly as written; don't rewrite, summarise, translate or invent; keep dates as written; null or an empty list when something is missing; an empty string when the CV doesn't state the name or email; `location` is the candidate's current location as written, or null.

## Data
No table. `schemas.py` holds the Pydantic models, which are also the strict structured-output schema sent to OpenAI. Every field is required with no default (strict mode needs that); "missing" is `null` or `[]`. Dates are strings, as written in the CV.

| Model | Fields |
|---|---|
| `Experience` | `company`, `title`, `start_date`, `end_date`, `location`, `description`: `str \| None` |
| `Education` | `institution`, `degree`, `field_of_study`, `start_date`, `end_date`: `str \| None` |
| `Project` | `name`, `description`, `link`: `str \| None`; `technologies`: `list[str]` |
| `Certificate` | `name`, `issuer`, `date`: `str \| None` |
| `ResumeData` | `name`: `str`; `email`: `str`; `phone`: `str \| None`; `location`: `str \| None` (the candidate's current location as written, added for `resume_generation`); `links`, `skills`: `list[str]`; `experience`, `education`, `projects`, `certificates`: lists of the models above |

`resume_intake` stores the result as JSONB in `resume_intakes.structured_data`. Rows stored before `location` existed got `"location": null` from the data migration `2349c371bb8a`, so every stored object validates as `ResumeData`.

**Privacy:** `store=False`, so OpenAI doesn't keep the response for later retrieval (OpenAI may still keep API data for up to 30 days for abuse monitoring unless the account has Zero Data Retention; open with the client: their data rules). CV text is never logged; only exception types are.

## Tests
`backend/tests/features/resume_structuring/test_service.py`: the canned result is returned and the call is sent with `store=False`, the model, `text_format` and `temperature=0`; a blank name or email; the 50,000-character limit (at the limit is sent, over it is rejected before any AI call); a timeout, a `ValidationError` (with no CV text in the log) and a refusal each become `AiExtractionError`.

**No test calls OpenAI.** The autouse `fake_openai` fixture in `backend/tests/conftest.py` replaces `service.client` in every test. It records each call's kwargs and returns a canned `ResumeData`; a test can set another `result` or an `error` to raise.
