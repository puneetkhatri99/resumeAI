# MinistryHub branded resume: client feedback status

As of 2026-11-03. Everything marked done is live on the Render site (commit `3aaf6a8`). Design: [features/resume_upgrade.md](features/resume_upgrade.md).

## Summary

| # | Request | Status |
|---|---|---|
| 1 | Links in groups | ✅ Done |
| 2 | Current roles show "Start – Present" | ✅ Done |
| 3 | Current roles on top, then newest to oldest | ✅ Done |
| 4 | Phone format (XXX) XXX-XXXX | ✅ Done (candidate's phone; references aren't built yet) |
| 5 | Education adapts to one or many degrees, with graduation dates | ✅ Done |
| 6 | Don't miss bullet points | 🟡 Partly done: prompt rule only, waiting for the client's example CVs |
| 7 | Light cleanup of grammar, tense and run-on lines | ⏳ Not started |
| 8 | Skills taken from experience, not made up | ✅ Done |
| 9 | Short professional summary | ✅ Done |
| 10 | Optional testimony / faith journey | ⏳ Not started |
| 11 | References with contact info and reference-check notes | ⏳ Not started |
| 12 | Better design and brand colours | ✅ Done (brand guide still welcome) |
| 13 | Automatic run from Loxo ("Endorsed" stage) | ⏳ Not started: needs Loxo API access |

## Implemented

### 1. Links in groups
Links are sorted by web address into three labelled groups in the PDF:
- **Social Media:** LinkedIn, Facebook pages and profiles, Instagram, TikTok, X/Twitter, Threads.
- **Teaching & Worship Videos:** YouTube, Vimeo, and Facebook videos, reels and watch links.
- **Other Links:** everything else, such as personal websites, portfolios, podcasts and sermon pages on a church website. Shown only when there are any.

The sorting is done by code, not AI, so it is predictable and also works for resumes stored before this change.

### 2. Current roles show "Start – Present"
When the CV shows a role is ongoing ("Present", "Current", "Now", "since 2020", or an open range like "2020 –"), the end date becomes **Present**. The PDF shows it as a highlighted yellow chip.

### 3. Ordering
Current roles always come first, the most recently started on top. Past roles follow from the most recent end date to the oldest. Jobs with no readable dates go last, in the CV's order.

### 4. Phone format
US numbers (10 digits, or 11 starting with 1 / "+1") show as **(XXX) XXX-XXXX** in the PDF. Numbers from other countries, extensions and fields with two numbers stay exactly as written, so nothing is formatted wrongly.

### 5. Education
Every degree gets its own entry, even when several are from the same school. Each shows its graduation date, or **In progress** for a degree not yet finished.

### 8. Skills from experience
Besides the skills the CV lists, the AI adds skills, tools and abilities the candidate's experience shows, for example "Preaching" from "preached weekly sermons" or a software tool named in a job.
- **Not made up:** each added skill must come with a quote from the CV, and code checks that the quote really is in the CV. If it isn't, the skill is dropped.
- **Kept short:** at most 10 added skills, broad ones ("Facilities management"), not one per bullet. The skills the CV lists itself are always kept.
- In the PDF, listed and added skills appear together under "Skills".

### 9. Professional summary
- If the CV has its own summary, profile or about section, it is copied **as written**.
- Otherwise the AI writes a short, general one: 2–3 sentences, at most 60 words, only facts from the CV, no pronouns, no praise the CV doesn't support, and not aimed at any particular job. An "Objective" doesn't count as a summary.

### 12. Design
The PDF uses MinistryHub's yellow and navy: navy name and headings, uppercase section titles with yellow markers, yellow dividers and bullet markers, and the yellow "Present" chip. The header and footer are the existing MinistryHub images. The look was approved on a sample PDF.

### Also changed
- `POST /api/public/pdf` now returns JSON with a permanent link to the branded PDF and the extracted data, instead of the PDF file. See [public_api_guide.md](public_api_guide.md).
- Resumes stored before this change still open; they get the new layout but no summary or added skills.

## Left to do

### 6. Missing bullet points (partly done)
The AI is now told to copy every line under each job, word for word, joining bullets that wrap onto two lines. In testing, a CV with 24 bullets across 3 jobs came through complete.

There is no automatic check yet. A check that compares the CV's bullets with the output was considered and left out for now: ministry CVs often have bulleted sections with no place in the resume (volunteering, hobbies, references), so it would misfire often and add AI cost.

**Needed from the client:** 1–2 example CVs where lines went missing, and whether that happened with our tool or the one they used before. A check will be built if the examples show it's still needed.

### 7. Light cleanup of wording
Not started (left out by choice for now). Today the AI copies the CV's text exactly. Cleanup would mean fixing grammar, tenses, inconsistent dates and long run-on lines without rewording. The risk is the AI rewriting too much, so the original text should be kept for comparison.

### 10. Testimony / faith journey
Not started. It needs a new optional section per candidate and a recruiter screen to fill it in: the candidate's own words, a recruiter's summary of what they shared, or a link to a Google Doc.

**Questions for the client:** who writes it, from what source, and which roles should show it.

### 11. References
Not started. It needs a references section (name, relationship, contact details, phones in the same format) plus space for MinistryHub's reference-check notes. The notes are typed by a recruiter, so this needs the same recruiter edit screen as item 10.

**Questions for the client:** do references usually come from the CV or from the recruiter, and who writes the reference-check notes?

### 13. Automatic run from Loxo
Not started. Candidates in the "Endorsed" stage would get the branded resume automatically on a schedule, plus a button for recruiters to push someone through right away. This needs reading the stage and the CV from Loxo and sending the PDF back, plus a scheduled job.

**Needed from the client:** Loxo API access (key and documentation), and how the PDF should go back (attached to the candidate, or replacing the original).

## Open items for the client
1. Example CVs where bullet points went missing (item 6).
2. A brand guide (colours, fonts, logo files), if they have one (item 12; the current look uses colours from their existing resume).
3. Who writes the testimony, and for which roles (item 10).
4. Where references come from, and who writes the reference-check notes (item 11).
5. Loxo API access, and how the PDF should go back (item 13).
