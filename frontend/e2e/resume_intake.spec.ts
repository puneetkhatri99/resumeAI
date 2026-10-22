// The DataTransfer callback below runs in the browser, so this file needs the DOM types.
/// <reference lib="dom" />
import { expect, test, type Page } from '@playwright/test'

// Synthetic CV only: no real person's resume in tests. It has the name and email the AI step requires, and one job.
const CV_TEXT = 'Jane Doe\njane.doe@example.com\n\nExperience\nSenior Python developer, Acme Corp, Jan 2020 - Present'

const TEXT_ANSWERS = {
  'Notice period / start date': 'One month',
  'Salary expectation': '$60k/year',
  Strengths: 'Clear communicator',
  Concerns: 'Little cloud experience',
  'Overall notes': 'Invite to the second round',
}

// Set to 1 to run the one test that sends a CV through the real backend to the live OpenAI API.
const LIVE_AI = process.env.E2E_LIVE_AI === '1'

// Live AI requests are limited and each one needs the user's permission. So unless E2E_LIVE_AI=1, an upload that
// a test doesn't answer itself (with page.route, which takes precedence) is aborted instead of reaching the backend.
test.beforeEach(async ({ page }) => {
  if (!LIVE_AI) await page.route('**/api/resume_intake', (route) => route.abort())
})

/** A text/event-stream body with these events, as the backend sends them: every field, the unused ones null. */
function eventStreamBody(...events: object[]): string {
  return events.map((event) => `data: ${JSON.stringify({ result: null, status: null, detail: null, ...event })}\n\n`).join('')
}

// What the backend streams back for a stored intake (see IntakeEvent in backend/app/features/resume_intake/schemas.py).
const STORED_INTAKE = {
  id: 1,
  original_filename: 'cv.txt',
  file_type: 'txt',
  extracted_text: CV_TEXT,
  structured_data: {
    name: 'Jane Doe',
    email: 'jane.doe@example.com',
    phone: null,
    location: null,
    links: [],
    skills: [],
    experience: [
      { company: 'Acme Corp', title: 'Senior Python developer', start_date: 'Jan 2020', end_date: 'Present', location: null, description: null },
    ],
    education: [],
    projects: [],
    certificates: [],
  },
  notice_period: 'One month',
  salary_expectation: '$60k/year',
  work_mode: 'remote',
  rating: 3,
  strengths: 'Clear communicator',
  concerns: 'Little cloud experience',
  overall_notes: 'Invite to the second round',
  created_at: '2026-10-22T10:00:00Z',
}

/** Drops synthetic text files with these names onto the CV drop zone. */
async function dropFiles(page: Page, fileNames: string[]) {
  // Playwright can't drag a file in from the OS, so build the drop's DataTransfer inside the page.
  const dataTransfer = await page.evaluateHandle(
    ({ text, names }) => {
      const transfer = new DataTransfer()
      for (const name of names) {
        transfer.items.add(new File([text], name, { type: 'text/plain' }))
      }
      return transfer
    },
    { text: CV_TEXT, names: fileNames },
  )
  // Sent to the file input that covers the drop zone; it bubbles up to the zone's drop handler.
  await page.getByLabel('CV', { exact: true }).dispatchEvent('drop', { dataTransfer })
}

// Live AI: makes 1 real OpenAI request and leaves one row in the dev database. Skipped unless E2E_LIVE_AI=1.
test('interviewer drops a CV, submits the interview and sees the extracted details, the CV text and the answers', async ({
  page,
}) => {
  test.skip(!LIVE_AI, "Calls the live OpenAI API: run with E2E_LIVE_AI=1, and only with the user's permission.")
  test.setTimeout(120_000)
  await page.goto('/')

  await dropFiles(page, ['cv.txt'])
  await expect(page.getByLabel('CV', { exact: true })).toHaveValue(/cv\.txt$/)

  for (const [label, answer] of Object.entries(TEXT_ANSWERS)) {
    await page.getByLabel(label).fill(answer)
  }
  await page.getByRole('radio', { name: 'On-site' }).check()
  await page.getByRole('radio', { name: '4', exact: true }).check()
  await page.getByRole('button', { name: 'Submit' }).click()

  // The AI step can take up to about a minute; a short timeout would waste a live request.
  await expect(page.getByText('Jane Doe', { exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByText('jane.doe@example.com', { exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByRole('heading', { name: 'Interview summary' })).toBeFocused()
  await expect(page.getByRole('heading', { name: 'Experience' })).toBeVisible()
  await expect(page.locator('pre')).toContainText('Senior Python developer')
  for (const text of [...Object.values(TEXT_ANSWERS), 'On-site', '4 / 5', 'cv.txt']) {
    await expect(page.getByText(text, { exact: true })).toBeVisible()
  }

  await page.getByRole('button', { name: 'New interview' }).click()

  await expect(page.getByLabel('CV', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('CV', { exact: true })).toBeFocused()
  for (const label of Object.keys(TEXT_ANSWERS)) {
    await expect(page.getByLabel(label)).toHaveValue('')
  }
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
})

test('dropping several files at once shows an error and keeps the CV already chosen', async ({ page }) => {
  await page.goto('/')
  const cvInput = page.getByLabel('CV', { exact: true })

  await dropFiles(page, ['cv.txt'])
  await dropFiles(page, ['a.txt', 'b.txt'])

  await expect(page.getByRole('alert')).toHaveText('Drop one CV at a time.')
  await expect(cvInput).toHaveValue(/cv\.txt$/)

  await dropFiles(page, ['other.txt'])

  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(cvInput).toHaveValue(/other\.txt$/)
})

/** Fills the five text answers and picks a work mode and a rating, except the one labelled `skip`. */
async function fillAnswers(page: Page, skip?: string) {
  for (const [label, answer] of Object.entries(TEXT_ANSWERS)) {
    if (label !== skip) await page.getByLabel(label).fill(answer)
  }
  if (skip !== 'Work mode') await page.getByRole('radio', { name: 'Remote' }).check()
  if (skip !== 'Rating') await page.getByRole('radio', { name: '3', exact: true }).check()
}

// Synthetic files only. The PDF has one page and no text layer, like a scan.
const PNG_CV = Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n'), Buffer.alloc(64)])
const BLANK_PDF = Buffer.from(
  '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' +
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R/Size 4>>\n%%EOF\n',
)
// The backend's answer for each file is canned (backend/tests/features/resume_intake/test_router.py checks the real
// one), so no upload reaches the backend.
const REJECTED_CVS = [
  {
    file: { name: 'photo.png', mimeType: 'image/png', buffer: PNG_CV },
    status: 415,
    message: 'Unsupported file type. Upload the CV as a PDF, DOCX, DOC or TXT file.',
  },
  {
    file: { name: 'scan.pdf', mimeType: 'application/pdf', buffer: BLANK_PDF },
    status: 422,
    message: "The PDF has no text layer — probably a scan; scanned resumes aren't supported yet.",
  },
]

for (const { file, status, message } of REJECTED_CVS) {
  test(`${file.name} is rejected with the backend message and the form keeps its answers`, async ({ page }) => {
    await page.route('**/api/resume_intake', (route) =>
      route.fulfill({
        contentType: 'text/event-stream',
        body: eventStreamBody({ stage: 'reading' }, { stage: 'error', status, detail: message }),
      }),
    )
    await page.goto('/')
    await page.getByLabel('CV', { exact: true }).setInputFiles(file)
    await fillAnswers(page)

    await page.getByRole('button', { name: 'Submit' }).click()

    await expect(page.getByRole('alert')).toHaveText(message)
    await expect(page.getByRole('button', { name: 'Submit' })).toBeEnabled()
    await expect(page.getByLabel('Strengths')).toHaveValue(TEXT_ANSWERS.Strengths)
  })
}

// Checked in the browser: through the Vite dev proxy the backend's early 413 arrives as a connection reset.
test('a CV over 10 MB shows "The CV is larger than 10 MB." and sends nothing', async ({ page }) => {
  let postCount = 0
  page.on('request', (request) => {
    if (request.method() === 'POST') postCount += 1
  })
  await page.goto('/')
  const bigCv = { name: 'big.txt', mimeType: 'text/plain', buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 'a') }
  await page.getByLabel('CV', { exact: true }).setInputFiles(bigCv)
  await fillAnswers(page)

  await page.getByRole('button', { name: 'Submit' }).click()

  await expect(page.getByRole('alert')).toHaveText('The CV is larger than 10 MB.')
  expect(postCount).toBe(0)
})

for (const label of ['CV', 'Notice period / start date', 'Work mode', 'Rating', 'Strengths']) {
  test(`an empty "${label}" blocks the submit and sends nothing`, async ({ page }) => {
    let postCount = 0
    page.on('request', (request) => {
      if (request.method() === 'POST') postCount += 1
    })
    await page.goto('/')
    if (label !== 'CV') {
      await page.getByLabel('CV', { exact: true }).setInputFiles({ name: 'cv.txt', mimeType: 'text/plain', buffer: Buffer.from(CV_TEXT) })
    }
    await fillAnswers(page, label)

    await page.getByRole('button', { name: 'Submit' }).click()

    // The browser focuses the first invalid control: for a radio group, its first radio.
    const firstRadio = { 'Work mode': 'Remote', Rating: '1' }[label]
    const control = firstRadio
      ? page.getByRole('radio', { name: firstRadio, exact: true })
      : page.getByLabel(label, { exact: label === 'CV' })
    await expect(control).toBeFocused()
    expect(postCount).toBe(0)
  })
}

test('a whitespace-only answer shows "Fill in Strengths.", focuses it and sends nothing', async ({ page }) => {
  let postCount = 0
  page.on('request', (request) => {
    if (request.method() === 'POST') postCount += 1
  })
  await page.goto('/')
  await page.getByLabel('CV', { exact: true }).setInputFiles({ name: 'cv.txt', mimeType: 'text/plain', buffer: Buffer.from(CV_TEXT) })
  await fillAnswers(page, 'Strengths')
  await page.getByLabel('Strengths').fill('   ')

  await page.getByRole('button', { name: 'Submit' }).click()

  await expect(page.getByRole('alert')).toHaveText('Fill in Strengths.')
  await expect(page.getByLabel('Strengths')).toBeFocused()
  expect(postCount).toBe(0)
})

test('double-clicking Submit sends the interview once', async ({ page }) => {
  let postCount = 0
  await page.route('**/api/resume_intake', async (route) => {
    postCount += 1
    await new Promise((resolve) => setTimeout(resolve, 500)) // keep the first request pending
    await route.fulfill({
      contentType: 'text/event-stream',
      body: eventStreamBody({ stage: 'reading' }, { stage: 'extracting' }, { stage: 'done', result: STORED_INTAKE }),
    })
  })
  await page.goto('/')
  await page.getByLabel('CV', { exact: true }).setInputFiles({ name: 'cv.txt', mimeType: 'text/plain', buffer: Buffer.from(CV_TEXT) })
  await fillAnswers(page)

  await page.getByRole('button', { name: 'Submit' }).dblclick()

  await expect(page.getByRole('heading', { name: 'Interview summary' })).toBeFocused()
  // The result opens at its own address, like one opened from the past-interviews page.
  await expect(page).toHaveURL(/#\/history\/1$/)
  expect(postCount).toBe(1)
})

test('past interviews lists stored intakes and opens one at its own address', async ({ page }) => {
  // Both answered here, so the test doesn't depend on what's in the dev database.
  await page.route('**/api/resume_intake', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
          json: {
            items: [
              {
                id: STORED_INTAKE.id,
                created_at: STORED_INTAKE.created_at,
                original_filename: STORED_INTAKE.original_filename,
                candidate_name: 'Jane Doe',
                candidate_email: 'jane.doe@example.com',
                rating: STORED_INTAKE.rating,
                work_mode: STORED_INTAKE.work_mode,
              },
            ],
            has_more: false,
          },
        })
      : route.abort(),
  )
  await page.route(`**/api/resume_intake/${STORED_INTAKE.id}`, (route) => route.fulfill({ json: STORED_INTAKE }))
  await page.goto('/')

  await page.getByRole('link', { name: 'Past interviews' }).click()
  await expect(page).toHaveURL(/#\/history$/)
  await page.getByRole('link', { name: /Jane Doe/ }).click()

  await expect(page).toHaveURL(/#\/history\/1$/)
  await expect(page.getByRole('heading', { name: 'Interview summary' })).toBeFocused()
  await expect(page.getByText(STORED_INTAKE.strengths, { exact: true })).toBeVisible()

  await page.goBack()
  await expect(page.getByRole('link', { name: /Jane Doe/ })).toBeVisible()
})

test('the form has no sideways scroll on a 375 px wide phone', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/')
  await expect(page.getByLabel('CV', { exact: true })).toBeVisible()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)
})
