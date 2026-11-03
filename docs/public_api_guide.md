# ResumeAI Public API: Integration Guide

As of 2026-11-03.

## Overview

HTTPS endpoints turn a candidate's resume into structured data and a branded MinistryHub PDF. You identify each resume with your own `candidate_id` and `resume_id`; ResumeAI stores the extracted data under that pair.

1. **Extract:** `POST /api/public/resumes` with the resume file and your two IDs. You get the AI-extracted data back as JSON.
2. **Format:** `GET /api/public/resumes/{candidate_id}/{resume_id}/pdf?template_id=1`. You get the branded resume as a PDF.
3. **Resume to PDF link in one call:** `POST /api/public/pdf` with the resume file (no IDs). You get JSON with a permanent link to the branded PDF and the extracted data (see section 3).

Step 2 only works for a pair that step 1 has stored. Sending the same pair to step 1 again replaces its stored data, so the next PDF reflects the new resume. Only the extracted data is kept; the original file is not stored.

## Base URL and authentication

Every request goes to the base URL below and must carry your API key in the `X-API-Key` header.

| Item | Value |
| --- | --- |
| Base URL | `https://ai-resume-data-extractor.onrender.com` |
| Auth header | `X-API-Key: <your key>` |
| Interactive docs | `https://ai-resume-data-extractor.onrender.com/docs` (use the Authorize button to set the key) |

The key is issued by the ResumeAI team. Keep it on your server only; never put it in a browser or mobile app. A missing or wrong key gets `401` with `{"detail": "Invalid or missing API key."}`.

## 1. Extract resume data

`POST /api/public/resumes`, sent as `multipart/form-data`.

| Field | Type | Rules |
| --- | --- | --- |
| `file` | file | PDF, DOCX, DOC or TXT; at most 4 pages; scanned PDFs are read by OCR |
| `candidate_id` | text | 1–100 characters; no `/` and no control characters |
| `resume_id` | text | Same rules as `candidate_id` |

The file type is detected from its content, not its name. The call usually takes 5–30 seconds (longer for a scanned PDF), so set your client timeout to at least 120 seconds.

**Response `200`** (`application/json`):

```json
{
  "candidate_id": "cand-123",
  "resume_id": "res-456",
  "data": {
    "name": "Priya Raman",
    "email": "priya.raman@example.com",
    "phone": "+91 98765 43210",
    "location": "Pune, India",
    "summary": "Backend engineer with 6 years of experience building logistics APIs in Python.",
    "links": ["https://github.com/priya-raman-example"],
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "inferred_skills": [
      {"name": "Docker", "evidence": "containerised the routing service with Docker"}
    ],
    "experience": [
      {
        "company": "Nimbus Logistics",
        "title": "Senior Backend Engineer",
        "start_date": "Jan 2021",
        "end_date": "Present",
        "location": "Pune",
        "description": "Built a shipment-tracking API serving 2M requests a day\nCut p95 latency by 40% by adding Redis caching"
      }
    ],
    "education": [
      {
        "institution": "College of Engineering Pune",
        "degree": "B.Tech",
        "field_of_study": "Computer Science",
        "start_date": "2013",
        "end_date": "2017"
      }
    ],
    "projects": [],
    "certificates": [
      { "name": "AWS Certified Developer - Associate", "issuer": null, "date": "2022" }
    ]
  }
}
```

**curl:**

```bash
curl -X POST "https://ai-resume-data-extractor.onrender.com/api/public/resumes" \
  -H "X-API-Key: $RESUMEAI_API_KEY" \
  -F "candidate_id=cand-123" \
  -F "resume_id=res-456" \
  -F "file=@resume.pdf"
```

**Python (`requests`):**

```python
import os
import requests

BASE = "https://ai-resume-data-extractor.onrender.com/api/public/resumes"
HEADERS = {"X-API-Key": os.environ["RESUMEAI_API_KEY"]}

with open("resume.pdf", "rb") as f:
    response = requests.post(
        BASE,
        headers=HEADERS,
        data={"candidate_id": "cand-123", "resume_id": "res-456"},
        files={"file": ("resume.pdf", f)},
        timeout=120,
    )
response.raise_for_status()
data = response.json()["data"]
```

## 2. Get the branded PDF

`GET /api/public/resumes/{candidate_id}/{resume_id}/pdf?template_id=1`

| Parameter | Where | Rules |
| --- | --- | --- |
| `candidate_id` | path | The ID sent to the extract call (URL-encode it) |
| `resume_id` | path | The ID sent to the extract call (URL-encode it) |
| `template_id` | query | Optional, default `1` (MinistryHub). `1` is the only template today; more will get new IDs later. |

**Response `200`:** the PDF bytes, `Content-Type: application/pdf`, `Content-Disposition: inline; filename*=UTF-8''MinistryHub%20-%20<name>.pdf`, `Cache-Control: no-store`. The PDF is rendered on each request (about 2 seconds locally, about 10 seconds on the hosted server) and is not stored, so save your copy.

**curl:**

```bash
curl -o resume-branded.pdf \
  -H "X-API-Key: $RESUMEAI_API_KEY" \
  "https://ai-resume-data-extractor.onrender.com/api/public/resumes/cand-123/res-456/pdf?template_id=1"
```

**Python (`requests`):**

```python
from urllib.parse import quote

url = f"{BASE}/{quote('cand-123', safe='')}/{quote('res-456', safe='')}/pdf"
response = requests.get(url, headers=HEADERS, params={"template_id": 1}, timeout=120)
response.raise_for_status()
with open("resume-branded.pdf", "wb") as f:
    f.write(response.content)
```

## 3. Resume to branded PDF link in one call

`POST /api/public/pdf`, sent as `multipart/form-data`.

| Field | Type | Rules |
| --- | --- | --- |
| `file` | file | PDF, DOCX, DOC or TXT; at most 4 pages; scanned PDFs are read by OCR |
| `template_id` | integer | Optional, default `1` (MinistryHub, the only template). Any other number is `404` before the resume is processed. |

**Response `200`** (`application/json`):

```json
{
  "url": "https://<storage-host>/public-resume/candidates/3f2c9a1e-7b4d-4c1a-9e2f-5d8b6a0c4e17/resume.pdf",
  "data": { "name": "Priya Raman", "email": "priya.raman@example.com", "...": "same fields as section 1" }
}
```

- `url` is a **permanent public link** to the branded PDF; anyone with it can open it, so treat it like the resume itself. It has a random, unguessable path. Download it or store the link.
- `data` is the extracted data the PDF was made from, in the same shape as section 1's `data`.

ResumeAI also keeps the original file and the PDF, and lists the candidate on its candidate page. If storing them fails you get `502` and no link; retry.

The call usually takes 5-30 seconds, but can take several minutes in the worst case (a scanned resume, a slow AI answer and storage retries), so **set your client timeout to 10 minutes**.

**curl:**

```bash
curl -X POST \
  -H "X-API-Key: $RESUMEAI_API_KEY" \
  -F "template_id=1" \
  -F "file=@resume.pdf" \
  "https://ai-resume-data-extractor.onrender.com/api/public/pdf"
```

**Python (`requests`):**

```python
with open("resume.pdf", "rb") as f:
    response = requests.post(
        "https://ai-resume-data-extractor.onrender.com/api/public/pdf",
        headers=HEADERS,
        data={"template_id": 1},
        files={"file": ("resume.pdf", f)},
        timeout=600,
    )
response.raise_for_status()
result = response.json()
pdf_url, data = result["url"], result["data"]
```

## Errors

Every error body is JSON with a `detail` field. For our own errors `detail` is a sentence you can log or show; for request-format errors (missing field, bad ID) it is FastAPI's list of field problems.

| Status | Endpoint | Meaning | What to do |
| --- | --- | --- | --- |
| `401` | all | Missing or wrong `X-API-Key` | Check the key |
| `402` | extract, one-call PDF | The service's AI spending limit is reached; uploads are paused | Contact the ResumeAI team; stored PDFs still work |
| `404` | PDF, one-call PDF | `Template not found.` or `Resume not found.` (pair never extracted) | Use `template_id=1`; extract first |
| `413` | extract, one-call PDF | Request larger than 10 MB | Send a smaller file |
| `415` | extract, one-call PDF | Not a PDF, DOCX, DOC or TXT file | Convert the file |
| `422` | extract, one-call PDF | Bad IDs, a non-integer `template_id` or missing field; or the CV is unusable: over 4 pages, password-protected, damaged, empty, over 50,000 characters, Word 6/95 `.doc`, or no name or email found | Fix the request or the file; don't retry unchanged |
| `429` | all | Rate limit hit (see Limits) | Wait the number of seconds in the `Retry-After` header |
| `500` | PDF, one-call PDF link | The PDF couldn't be rendered | Retry later; report if it persists |
| `502` | extract, one-call PDF | The AI step failed (`AI extraction failed, please try again.`), or storing the files failed (`Could not store the resume files. Try again.`) | Retry after a short wait |
| `503` | all | `The database is unavailable. Try again later.` | Retry after a short wait |

## Limits

| Limit | Value |
| --- | --- |
| Requests to the API | 120 per minute per IP address |
| Extract and one-call PDF calls | 30 per hour per IP address together (on top of the above) |
| Request size | 10 MB |
| Resume length | 4 pages; 50,000 characters of text |
| ID length | 1–100 characters each |

Over a rate limit you get `429` and a `Retry-After` header in seconds. The limits are counted in memory and reset when the service restarts.

## Data reference

All fields are always present. Text fields that the resume doesn't contain are `null`; list fields are `[]`. Dates are kept as written in the resume (for example `Jan 2021`, `2017`, `Present`), not converted, except that an ongoing role's `end_date` is `Present`. `summary` and `inferred_skills` were added on 2026-11-03; existing fields are unchanged.

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | Always set |
| `email` | string | Always set |
| `phone` | string or null | |
| `location` | string or null | The candidate's location |
| `summary` | string or null | The resume's own summary, profile or about section as written; when it has none, a 2–3 sentence summary written by the AI from the resume's facts. `null` for resumes stored before 2026-11-03, or when the resume has too little to summarise |
| `links` | list of strings | Web addresses in the resume, including PDF link targets |
| `skills` | list of strings | The skills the resume lists |
| `inferred_skills` | list | Skills the resume shows the candidate used but doesn't list. Each: `name`, `evidence` (a short quote from the resume that shows it; a skill whose quote isn't in the resume text is dropped). `[]` for resumes stored before 2026-11-03 |
| `experience` | list | Each: `company`, `title`, `start_date`, `end_date`, `location`, `description` (bullets joined by newlines) |
| `education` | list | Each: `institution`, `degree`, `field_of_study`, `start_date`, `end_date` |
| `projects` | list | Each: `name`, `description`, `link`, `technologies` (list of strings) |
| `certificates` | list | Each: `name`, `issuer`, `date` |

The data is extracted by AI, which can make mistakes; check important values against the original resume.
