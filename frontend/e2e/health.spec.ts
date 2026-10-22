import { expect, test } from '@playwright/test'

test('home page shows backend and database are healthy', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'ResumeAI' })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('Backend OK · Database OK')
})
