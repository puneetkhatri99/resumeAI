type BrandedPdfLinkProps = {
  /** The stored intake whose AI-extracted details are rendered into the PDF. */
  intakeId: number
}

/**
 * "Open branded PDF": opens the intake's MinistryHub-branded resume in a new tab. A plain link, with no JS: the
 * backend renders the PDF on demand (`GET /api/resume_generation/{id}`), the browser's PDF viewer shows and saves
 * it, and an error shows as plain JSON in that tab.
 */
export function BrandedPdfLink({ intakeId }: BrandedPdfLinkProps) {
  return (
    <a className="button button-outline" href={`/api/resume_generation/${intakeId}`} target="_blank" rel="noopener noreferrer">
      Open branded PDF
    </a>
  )
}
