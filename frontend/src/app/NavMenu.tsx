import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

type NavMenuProps = {
  /** The menu's name: the trigger's text (or its accessible name when `trigger` is given) and the phone group name. */
  label: string
  /** Shown in the trigger instead of `label`, e.g. the account avatar. */
  trigger?: ReactNode
  /** Marks the trigger as current when the page shown is one of the menu's links. */
  isCurrent?: boolean
  /** Opens the panel from the trigger's right edge (for menus at the end of the bar). */
  alignEnd?: boolean
  children: ReactNode
}

/**
 * A top-bar dropdown (a disclosure button and a panel of links). It closes on a click outside, on Escape (focus goes
 * back to the trigger) and on a click on any link or button inside. The panel is always in the page and hidden by
 * app.css while closed, so it can fade in; on a phone the menu sheet shows every panel open under its group name.
 */
export function NavMenu({ label, trigger, isCurrent = false, alignEnd = false, children }: NavMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  return (
    <div ref={rootRef} className={`nav-menu${alignEnd ? ' nav-menu-end' : ''}`} data-open={open}>
      <button
        ref={triggerRef}
        type="button"
        className={`nav-link nav-menu-trigger${isCurrent ? ' is-current' : ''}${trigger ? ' avatar-trigger' : ''}`}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={trigger ? label : undefined}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        {trigger ?? label}
        <svg className="nav-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      <span className="nav-group-label" aria-hidden="true">
        {label}
      </span>
      <div
        id={panelId}
        className="nav-menu-panel"
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('a, button')) setOpen(false)
        }}
      >
        {children}
      </div>
    </div>
  )
}
