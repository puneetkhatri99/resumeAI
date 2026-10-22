import { useId, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import './resumeIntake.css'

const MULTIPLE_FILES_MESSAGE = 'Drop one CV at a time.'

/** "PDF · 184 KB" for the chosen file's card. */
function describeFile(file: File): string {
  const extension = file.name.includes('.') ? file.name.split('.').pop()!.toUpperCase() : 'File'
  const size = file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / (1024 * 1024)).toFixed(1)} MB`
  return `${extension} · ${size}`
}

const FILE_ICON = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </svg>
)

/**
 * The CV card (step 1): a drop zone with the real file input stretched invisibly over it, so a click anywhere
 * opens the picker, a dropped file goes into the same input, and the form submits it exactly like a picked one.
 * Keyboard use and the browser's "required" check keep working without extra code.
 * Once a file is chosen the zone shows its name, type and size. Dropping several files at once is refused with
 * an alert, and any CV already chosen is kept.
 */
export function CvDropzone() {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [dropError, setDropError] = useState<string | null>(null)
  const [chosenFile, setChosenFile] = useState<File | null>(null)

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    // Without this the browser refuses the drop and opens the file in the tab instead.
    event.preventDefault()
    setIsDragOver(true)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragOver(false)
    const { files } = event.dataTransfer
    if (files.length > 1) {
      setDropError(MULTIPLE_FILES_MESSAGE)
    } else if (files.length === 1 && inputRef.current) {
      // A drop with no files (e.g. selected text) falls through, so it can't clear the chosen CV.
      inputRef.current.files = files
      setChosenFile(files[0])
      setDropError(null)
    }
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    setChosenFile(event.currentTarget.files?.[0] ?? null)
    setDropError(null)
  }

  return (
    // No aria-labelledby: the card would share the input's "CV" label.
    <section className="card intake-card">
      <div className="card-header">
        <span className="icon-tile tile-blue" aria-hidden="true">
          {FILE_ICON}
        </span>
        <div>
          <span className="step-label text-blue">Step 1</span>
          <h3>
            <label htmlFor={inputId}>CV</label>
          </h3>
        </div>
      </div>
      <div
        className={['cv-dropzone', chosenFile && 'cv-dropzone-chosen', isDragOver && 'cv-dropzone-active'].filter(Boolean).join(' ')}
        onDragOver={handleDragOver}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        {chosenFile ? (
          <>
            <span className="cv-file-icon" aria-hidden="true">
              {FILE_ICON}
            </span>
            <span className="cv-file-text">
              <span className="cv-file-name">{chosenFile.name}</span>
              <span className="cv-file-detail">{describeFile(chosenFile)}</span>
            </span>
            <span className="button button-outline" aria-hidden="true">
              Replace
            </span>
          </>
        ) : (
          <>
            <span className="cv-upload-tile" aria-hidden="true">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 15V4" />
                <path d="m7 9 5-5 5 5" />
                <path d="M5 20h14" />
              </svg>
            </span>
            <span className="cv-dropzone-title">Drag the CV here</span>
            <span className="cv-dropzone-or">or</span>
            <span className="button button-outline" aria-hidden="true">
              Choose file
            </span>
          </>
        )}
        <input
          id={inputId}
          ref={inputRef}
          className="cv-dropzone-input"
          type="file"
          name="file"
          required
          accept=".pdf,.doc,.docx,.txt"
          onChange={handleChange}
        />
      </div>
      {dropError && (
        <p role="alert" className="alert alert-error">
          {dropError}
        </p>
      )}
      <ul className="cv-rules">
        <li>PDF, DOCX, DOC or TXT</li>
        <li>Up to 4 pages and 10 MB</li>
        <li>Scanned PDFs can't be read yet</li>
        <li>Only the text is saved, never the file</li>
      </ul>
    </section>
  )
}
