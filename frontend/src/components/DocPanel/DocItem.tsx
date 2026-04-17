// Один документ — клик, переименование, удаление, drag-source, превью
import { useState } from 'react'
import { useStore, useSelectedDocId, useFolders, type Document } from '../../stores'
import { renderMarkdown } from '../../hooks/useChat'
import { InlineEdit } from './InlineEdit'
import { formatSize, formatDate, isImage, isMarkdownLike, getDocIcon } from './types'
import { useProjects } from '../../stores'

// Бейдж проекта (для режима "все документы")
function ProjectBadge({ projectId }: { projectId: string }) {
  const projects = useProjects()
  const project = projects.find((p) => p.id === projectId)
  const name = project?.name || (projectId === '__common__' ? 'Общие' : projectId.slice(0, 8))
  return <span className="doc-item__project-badge" title={project?.name || projectId}> · {name}</span>
}

interface DocItemProps {
  doc: Document
  index: number
  projectId: string
  showProject?: boolean
}

export function DocItem({ doc, index, projectId, showProject }: DocItemProps) {
  const selectedDocId = useSelectedDocId()
  const selectDocument = useStore((s) => s.selectDocument)
  const deleteDocument = useStore((s) => s.deleteDocument)
  const renameDocument = useStore((s) => s.renameDocument)
  const moveDocument = useStore((s) => s.moveDocument)
  const folders = useFolders()
  const docRefsSelected = useStore((s) => s.docRefsSelected)
  const toggleDocRef = useStore((s) => s.toggleDocRef)

  const [renaming, setRenaming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [moving, setMoving] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [docContent, setDocContent] = useState<string | null>(null)
  const [docLoading, setDocLoading] = useState(false)

  const isImg = isImage(doc.filename)
  const isSelected = docRefsSelected.has(doc.id)
  const isActive = selectedDocId === doc.id

  const handleClick = async () => {
    if (isImg) {
      setPreviewOpen(!previewOpen)
      return
    }
    if (isActive) {
      selectDocument(null)
      setDocContent(null)
      setPreviewOpen(false)
      return
    }
    selectDocument(doc.id)
    setDocLoading(true)
    try {
      const content = await useStore.getState().loadDocumentContent(projectId, doc.id)
      setDocContent(content)
      setPreviewOpen(true)
    } catch {
      setDocContent('⚠️ Не удалось загрузить')
    } finally {
      setDocLoading(false)
    }
  }

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm(`Удалить "${doc.filename}"?`)) return
    setDeleting(true)
    try { await deleteDocument(projectId, doc.id) } finally { setDeleting(false) }
  }

  const handleRename = async (newName: string) => {
    await renameDocument(projectId, doc.id, newName)
    setRenaming(false)
  }

  const handleMove = async (folderId: string | null) => {
    await moveDocument(projectId, doc.id, folderId)
    setMoving(false)
  }

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('application/x-doc-id', doc.id)
    e.dataTransfer.effectAllowed = 'move'
  }

  return (
    <>
      <div
        className={`doc-item ${isActive ? 'doc-item--active' : ''} ${deleting ? 'doc-item--deleting' : ''}`}
        onClick={handleClick}
        draggable
        onDragStart={handleDragStart}
        title={doc.filename}
      >
        {/* Чекбокс для ссылки в чат */}
        <label className="doc-item__checkbox" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => toggleDocRef(doc.id)}
          />
        </label>

        <span className="doc-item__index">#{index + 1}</span>

        {/* Превью-иконка или thumbnail */}
        <span className="doc-item__icon">
          {isImg ? (
            <img
              className="doc-item__thumb"
              src={`/api/documents/${projectId}/${doc.id}`}
              alt={doc.filename}
              loading="lazy"
            />
          ) : (
            getDocIcon(doc.filename)
          )}
        </span>

        {/* Имя + мета */}
        <div className="doc-item__info">
          {renaming ? (
            <InlineEdit
              value={doc.filename}
              onSave={handleRename}
              onCancel={() => setRenaming(false)}
            />
          ) : (
            <span className="doc-item__name" title={doc.filename}>
              {doc.filename}
              {doc.parse_status === 'pending' && (
                <span
                  className="doc-item__parse-pending"
                  title="Парсим содержимое в фоне — обновлю когда готово"
                  aria-label="Парсинг в процессе"
                >
                  ⏳
                </span>
              )}
              {doc.parse_status === 'failed' && (
                <span
                  className="doc-item__parse-warn"
                  title={doc.parse_error || 'Не удалось извлечь текст из файла — ассистент увидит только имя и размер'}
                  aria-label="Парсинг не удался"
                >
                  ⚠
                </span>
              )}
              {doc.parse_status === 'parsed' && doc.parse_method === 'aitunnel_gemini' && (
                <span
                  className="doc-item__parse-gemini"
                  title="Распарсил Gemini 2.5 Flash (AITunnel) — OCR/описание картинки"
                  aria-label="Распарсил Gemini Flash"
                >
                  ✨
                </span>
              )}
            </span>
          )}
          <span className="doc-item__meta">
            {formatSize(doc.size)} · {formatDate(doc.created_at)}
            {showProject && doc.project_id && <ProjectBadge projectId={doc.project_id} />}
          </span>
        </div>

        {/* Действия */}
        <div className="doc-item__actions">
          <button
            className="doc-item__action-btn"
            onClick={(e) => { e.stopPropagation(); setMoving(!moving) }}
            title="Переместить в папку"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M2 5a1.5 1.5 0 011.5-1.5H7l1.5 1.5H17A1.5 1.5 0 0118.5 6.5v9A1.5 1.5 0 0117 17H3.5A1.5 1.5 0 012 15.5V5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /><path d="M8 12l2 2 2-2M10 8v6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
          <button
            className="doc-item__action-btn"
            onClick={(e) => { e.stopPropagation(); setRenaming(true) }}
            title="Переименовать"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M13.5 3.5l3 3L7 16H4v-3L13.5 3.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
          </button>
          <button
            className="doc-item__action-btn doc-item__action-btn--delete"
            onClick={handleDelete}
            title="Удалить"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
          </button>
        </div>
      </div>

      {/* Dropdown перемещения в папку */}
      {moving && (
        <div className="doc-item__move-dropdown" onClick={(e) => e.stopPropagation()}>
          {doc.folder_id && (
            <button className="doc-item__move-option" onClick={() => handleMove(null)}>
              📁 В корень
            </button>
          )}
          {folders
            .filter((f) => f.id !== doc.folder_id)
            .map((f) => (
              <button key={f.id} className="doc-item__move-option" onClick={() => handleMove(f.id)}>
                📁 {f.name}
              </button>
            ))
          }
          {folders.length === 0 && !doc.folder_id && (
            <span className="doc-item__move-empty">Нет папок</span>
          )}
        </div>
      )}

      {/* Превью изображения */}
      {isImg && previewOpen && (
        <div className="doc-item__image-preview">
          <img
            src={`/api/documents/${projectId}/${doc.id}`}
            alt={doc.filename}
          />
        </div>
      )}

      {/* Превью текста */}
      {!isImg && previewOpen && (
        <div className="doc-item__text-preview">
          {docLoading ? (
            <span className="doc-panel__spinner" />
          ) : docContent !== null ? (
            isMarkdownLike(doc.filename) ? (
              <div
                className="doc-item__preview-md"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(docContent) }}
              />
            ) : (
              <pre className="doc-item__preview-text">{docContent}</pre>
            )
          ) : null}
        </div>
      )}
    </>
  )
}
