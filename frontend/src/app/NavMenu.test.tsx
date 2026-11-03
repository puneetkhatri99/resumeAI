import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { NavMenu } from './NavMenu.tsx'

function renderMenu() {
  render(
    <>
      <NavMenu label="Interview">
        <a href="#/interview">New interview</a>
      </NavMenu>
      <p>Outside</p>
    </>,
  )
  return screen.getByRole('button', { name: 'Interview' })
}

describe('NavMenu', () => {
  it('opens on click and closes on a click outside', async () => {
    const trigger = renderMenu()
    const user = userEvent.setup()

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    await user.click(screen.getByText('Outside'))
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('Escape closes it and puts focus back on the trigger', async () => {
    const trigger = renderMenu()
    const user = userEvent.setup()

    await user.click(trigger)
    await user.keyboard('{Escape}')

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveFocus()
  })

  it('a click on a link inside closes it', async () => {
    const trigger = renderMenu()
    const user = userEvent.setup()

    await user.click(trigger)
    await user.click(screen.getByRole('link', { name: 'New interview' }))

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})
