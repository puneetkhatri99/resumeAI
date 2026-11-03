import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CvDropzone } from './CvDropzone.tsx'

describe('CvDropzone', () => {
  it('refuses a drop of several files with an alert, which clears once a file is chosen', () => {
    render(<CvDropzone />)
    const input = screen.getByLabelText<HTMLInputElement>('CV')

    // jsdom has no DataTransfer; Testing Library attaches this plain object as the event's dataTransfer.
    fireEvent.drop(screen.getByText('Drag the CV here'), {
      dataTransfer: { files: [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')] },
    })

    expect(screen.getByRole('alert')).toHaveTextContent('Drop one CV at a time.')
    expect(input.files).toHaveLength(0)

    fireEvent.change(input)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('hides the "never the file" rule only when the flow stores the file', () => {
    const { rerender } = render(<CvDropzone />)
    expect(screen.getByText('Only the text is saved, never the file')).toBeInTheDocument()

    rerender(<CvDropzone storesFile />)
    expect(screen.queryByText('Only the text is saved, never the file')).not.toBeInTheDocument()
  })
})
