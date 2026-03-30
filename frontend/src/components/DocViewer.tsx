import { useState, useRef, useCallback } from 'react'
import { useStore, useDocuments, useLinks, useSelectedDocId, useSelectedProjectId, type Document, type Link } from '../stores'
import { renderMarkdown } from '../hooks/useChat'
import './DocViewer.css'

// Форматирование размера файла
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DocViewer() {
  const documents = useDocuments()
  const links = useLinks()
  const selectedDocId = useSelectedDocId()
  const selectedProjectId = useSelectedProjectId()
  const documentsLoading = useStore((s) => s.documentsLoading)
  const uploadDocument = useStore((s) => s.uploadDocument)
  const deleteDocument = useStore((s) => s.deleteDocument)
  const selectDocument = useStore((s) => s.selectDocument)
  const loadDocumentContent = useStore((s) => s.loadDocumentContent)
  const addLink = useStore((s) => s.addLink)
  const deleteLink = useStore((s) => s.deleteLink)

  const [docContent, setDocContent] = useState<string | null>(null)
  const [docLoading, setDocLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  // Форма добавления ссылки
  const [showLinkForm, setShowLinkForm] = useState(false)
  const [linkUrl, setLinkUrl] = useState('')
  const [linkTitle, setLinkTitle] = useState('')
  const [linkDesc, setLinkDesc] = useState('')
  const [linkSaving, setLinkSaving] = useState(false)
  const [deletingLinkId, setDeletingLinkId] = useState<string | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const createFolder = useStore((s) => s.createFolder)

  // Прогресс при загрузке папки: {current, total} или null
  const [folderProgress, setFolderProgress] = useState<{ current: number; total: number } | null>(null)

  const isMarkdown = (filename: string) =>
    /\.(md|markdown|txt)$/i.test(filename)

  const handleDocClick = async (doc: Document) => {
    if (selectedDocId === doc.id) {
      // Повторный клик — закрыть просмотр
      selectDocument(null)
      setDocContent(null)
      return
    }

    selectDocument(doc.id)
    if (!selectedProjectId) return

    setDocLoading(true)
    setDocContent(null)
    try {
      const content = await loadDocumentContent(selectedProjectId, doc.id)
      setDocContent(content)
    } catch {
      setDocContent('⚠️ Не удалось загрузить содержимое файла')
    } finally {
      setDocLoading(false)
    }
  }

  const handleUpload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || !selectedProjectId) return
      setUploadError(null)
      setUploading(true)

      const uploadPromises = Array.from(files).map((file) =>
        uploadDocument(selectedProjectId, file).catch((err) => {
          setUploadError(err instanceof Error ? err.message : 'Ошибка загрузки')
        }),
      )
      await Promise.all(uploadPromises)
      setUploading(false)
    },
    [selectedProjectId, uploadDocument],
  )

  // Загрузка папки целиком с сохранением структуры подпапок
  const handleFolderUpload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || !selectedProjectId) return
      setUploadError(null)

      const fileList = Array.from(files)
      // Фильтруем только реальные файлы (не директории)
      const realFiles = fileList.filter((f) => f.size > 0 || f.type !== '')

      if (realFiles.length === 0) return

      setFolderProgress({ current: 0, total: realFiles.length })

      try {
        // Шаг 1: Собираем уникальные пути папок из webkitRelativePath
        const folderPaths = new Set<string>()
        for (const file of realFiles) {
          const parts = file.webkitRelativePath.split('/')
          // parts[0..n-2] — папки, parts[n-1] — файл
          for (let depth = 1; depth < parts.length; depth++) {
            folderPaths.add(parts.slice(0, depth).join('/'))
          }
        }

        // Шаг 2: Сортируем по глубине — сначала создаём родительские папки
        const sortedPaths = [...folderPaths].sort(
          (a, b) => a.split('/').length - b.split('/').length
        )

        // Шаг 3: Создаём виртуальные папки в системе, запоминаем path → id
        const pathToFolderId = new Map<string, string>()
        for (const folderPath of sortedPaths) {
          const segments = folderPath.split('/')
          const folderName = segments[segments.length - 1]
          const parentPath = segments.slice(0, -1).join('/')
          const parentId = parentPath ? (pathToFolderId.get(parentPath) ?? null) : null

          const folder = await createFolder(selectedProjectId, folderName, parentId)
          pathToFolderId.set(folderPath, folder.id)
        }

        // Шаг 4: Загружаем файлы в соответствующие папки
        let uploaded = 0
        for (const file of realFiles) {
          const parts = file.webkitRelativePath.split('/')
          const dirPath = parts.slice(0, -1).join('/')
          const folderId = pathToFolderId.get(dirPath) ?? null

          await uploadDocument(selectedProjectId, file, folderId).catch((err) => {
            setUploadError(err instanceof Error ? err.message : 'Ошибка загрузки файла')
          })

          uploaded++
          setFolderProgress({ current: uploaded, total: realFiles.length })
        }
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Ошибка загрузки папки')
      } finally {
        setFolderProgress(null)
      }
    },
    [selectedProjectId, uploadDocument, createFolder],
  )

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    handleUpload(e.dataTransfer.files)
  }

  const handleDelete = async (e: React.MouseEvent, doc: Document) => {
    e.stopPropagation()
    if (!selectedProjectId) return
    if (!confirm(`Удалить файл "${doc.filename}"?`)) return

    setDeletingId(doc.id)
    try {
      await deleteDocument(selectedProjectId, doc.id)
      if (selectedDocId === doc.id) {
        setDocContent(null)
      }
    } finally {
      setDeletingId(null)
    }
  }

  // Сохранить ссылку
  const handleAddLink = useCallback(async () => {
    if (!linkUrl.trim() || !selectedProjectId) return
    setLinkSaving(true)
    try {
      await addLink(selectedProjectId, linkTitle.trim(), linkUrl.trim(), linkDesc.trim())
      setLinkUrl('')
      setLinkTitle('')
      setLinkDesc('')
      setShowLinkForm(false)
    } catch {
      // ошибка — оставляем форму открытой
    } finally {
      setLinkSaving(false)
    }
  }, [selectedProjectId, linkUrl, linkTitle, linkDesc, addLink])

  const handleDeleteLink = async (e: React.MouseEvent, link: Link) => {
    e.stopPropagation()
    if (!selectedProjectId) return
    if (!confirm(`Удалить ссылку "${link.title}"?`)) return
    setDeletingLinkId(link.id)
    try {
      await deleteLink(selectedProjectId, link.id)
    } finally {
      setDeletingLinkId(null)
    }
  }

  const selectedDoc = documents.find((d) => d.id === selectedDocId)

  return (
    <div className="doc-viewer">
      <div className="doc-viewer__header">
        <span className="doc-viewer__label">Документы</span>
        <div className="doc-viewer__header-actions">
          {/* Кнопка добавления ссылки */}
          <button
            className={`doc-viewer__upload-btn ${showLinkForm ? 'doc-viewer__upload-btn--active' : ''}`}
            onClick={() => setShowLinkForm((v) => !v)}
            disabled={!selectedProjectId}
            title="Добавить ссылку"
            aria-label="Добавить ссылку"
          >
            <svg viewBox="0 0 16 16" fill="none">
              <path d="M6.5 9.5a3.5 3.5 0 005 0l2-2a3.5 3.5 0 00-5-5l-1 1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
              <path d="M9.5 6.5a3.5 3.5 0 00-5 0l-2 2a3.5 3.5 0 005 5l1-1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            </svg>
          </button>

          {/* Кнопка загрузки папки */}
          <button
            className="doc-viewer__upload-btn"
            onClick={() => folderInputRef.current?.click()}
            disabled={!!folderProgress || uploading || !selectedProjectId}
            title="Загрузить папку целиком"
            aria-label="Загрузить папку"
          >
            {folderProgress ? (
              <span className="doc-viewer__spinner" />
            ) : (
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M2 4.5A1.5 1.5 0 013.5 3H6l1.5 2H13a1.5 1.5 0 011.5 1.5v5A1.5 1.5 0 0113 13H3.5A1.5 1.5 0 012 11.5v-7z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
                <path d="M8 7.5v3M6.5 9l1.5 1.5L9.5 9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
          </button>

          {/* Кнопка загрузки файла */}
          <button
            className="doc-viewer__upload-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || !!folderProgress || !selectedProjectId}
            title="Загрузить файл"
            aria-label="Загрузить документ"
          >
            {uploading ? (
              <span className="doc-viewer__spinner" />
            ) : (
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M8 11V5M5 8l3-3 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M3 13h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            )}
          </button>
        </div>

        {/* Скрытый input для выбора папки (webkitdirectory) */}
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error — webkitdirectory нестандартный атрибут, но работает во всех браузерах
          webkitdirectory=""
          multiple
          className="doc-viewer__file-input"
          onChange={(e) => {
            handleFolderUpload(e.target.files)
            // Сбрасываем значение чтобы можно было загрузить ту же папку повторно
            e.target.value = ''
          }}
          aria-hidden="true"
        />

        {/* Скрытый input для выбора отдельных файлов */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="doc-viewer__file-input"
          onChange={(e) => handleUpload(e.target.files)}
          accept=".txt,.md,.pdf,.docx,.csv,.json,.yaml,.yml"
          aria-hidden="true"
        />
      </div>

      {uploadError && (
        <div className="doc-viewer__error">{uploadError}</div>
      )}

      {/* Форма добавления ссылки */}
      {showLinkForm && (
        <div className="link-form">
          <input
            className="link-form__input"
            type="url"
            placeholder="https://..."
            value={linkUrl}
            onChange={(e) => setLinkUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleAddLink()}
            autoFocus
          />
          <input
            className="link-form__input"
            type="text"
            placeholder="Название (необязательно)"
            value={linkTitle}
            onChange={(e) => setLinkTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleAddLink()}
          />
          <input
            className="link-form__input"
            type="text"
            placeholder="Для чего эта ссылка?"
            value={linkDesc}
            onChange={(e) => setLinkDesc(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleAddLink()}
          />
          <div className="link-form__actions">
            <button
              className="link-form__btn link-form__btn--cancel"
              onClick={() => { setShowLinkForm(false); setLinkUrl(''); setLinkTitle(''); setLinkDesc('') }}
              disabled={linkSaving}
            >
              Отмена
            </button>
            <button
              className="link-form__btn link-form__btn--save"
              onClick={handleAddLink}
              disabled={!linkUrl.trim() || linkSaving}
            >
              {linkSaving ? <span className="doc-viewer__spinner" style={{ width: 10, height: 10 }} /> : 'Добавить'}
            </button>
          </div>
        </div>
      )}

      {/* Прогресс загрузки папки */}
      {folderProgress && (
        <div className="doc-viewer__folder-progress">
          <div
            className="doc-viewer__folder-progress-bar"
            style={{ width: `${Math.round((folderProgress.current / folderProgress.total) * 100)}%` }}
          />
          <span className="doc-viewer__folder-progress-label">
            Загрузка папки: {folderProgress.current} / {folderProgress.total}
          </span>
        </div>
      )}

      {/* Зона Drag & Drop */}
      <div
        className={`doc-viewer__dropzone ${dragOver ? 'doc-viewer__dropzone--active' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        {documentsLoading && documents.length === 0 && (
          <div className="doc-viewer__loading">
            <span className="doc-viewer__spinner" />
          </div>
        )}

        {!documentsLoading && documents.length === 0 && (
          <div className="doc-viewer__empty">
            <p>Нет документов</p>
            <span className="doc-viewer__empty-hint">
              Перетащите файлы сюда
            </span>
          </div>
        )}

        {documents.map((doc, index) => (
          <button
            key={doc.id}
            className={`doc-item ${selectedDocId === doc.id ? 'doc-item--active' : ''} ${deletingId === doc.id ? 'doc-item--deleting' : ''}`}
            onClick={() => handleDocClick(doc)}
            title={`#${index + 1} — ${doc.filename}`}
          >
            <span className="doc-item__index" aria-hidden="true">#{index + 1}</span>
            <span className="doc-item__icon" aria-hidden="true">
              {getDocIcon(doc.filename)}
            </span>
            <div className="doc-item__content">
              <span className="doc-item__name">{doc.filename}</span>
              <span className="doc-item__meta">{formatSize(doc.size)}</span>
            </div>
            <button
              className="doc-item__delete"
              onClick={(e) => handleDelete(e, doc)}
              title="Удалить файл"
              aria-label={`Удалить ${doc.filename}`}
            >
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
            </button>
          </button>
        ))}
      </div>

      {/* Секция ссылок */}
      {links.length > 0 && (
        <div className="link-section">
          <div className="link-section__header">
            <span className="link-section__label">Ссылки</span>
          </div>
          {links.map((link) => (
            <div
              key={link.id}
              className={`link-item ${deletingLinkId === link.id ? 'link-item--deleting' : ''}`}
            >
              <span className="link-item__icon">🔗</span>
              <div className="link-item__content">
                <a
                  className="link-item__title"
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={link.url}
                >
                  {link.title}
                </a>
                {link.description && (
                  <span className="link-item__desc">{link.description}</span>
                )}
              </div>
              <button
                className="doc-item__delete"
                onClick={(e) => handleDeleteLink(e, link)}
                title="Удалить ссылку"
                aria-label={`Удалить ${link.title}`}
              >
                <svg viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Просмотр содержимого документа */}
      {selectedDocId && selectedDoc && (
        <div className="doc-viewer__preview">
          <div className="doc-viewer__preview-header">
            <span className="doc-viewer__preview-name">{selectedDoc.filename}</span>
            <button
              className="doc-viewer__preview-close"
              onClick={() => { selectDocument(null); setDocContent(null) }}
              aria-label="Закрыть просмотр"
            >
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="doc-viewer__preview-body">
            {docLoading && (
              <div className="doc-viewer__preview-loading">
                <span className="doc-viewer__spinner" />
              </div>
            )}
            {!docLoading && docContent !== null && (
              isMarkdown(selectedDoc.filename) ? (
                <div
                  className="doc-viewer__preview-markdown"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(docContent) }}
                />
              ) : (
                <pre className="doc-viewer__preview-text">{docContent}</pre>
              )
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// Иконка по расширению файла
function getDocIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'md':
    case 'markdown': return '📝'
    case 'txt': return '📄'
    case 'pdf': return '📕'
    case 'csv': return '📊'
    case 'json': return '🔧'
    case 'yaml':
    case 'yml': return '⚙️'
    case 'docx':
    case 'doc': return '📃'
    default: return '📎'
  }
}
