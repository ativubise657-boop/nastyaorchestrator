import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import {
  useStore,
  useDocuments,
  useSelectedDocId,
  useSelectedProjectId,
  useDocPanelOpen,
  useFolders,
  useDocViewMode,
  useProjects,
  useLinks,
  useLinkRefsSelected,
  type Document,
  type Folder,
  type Link,
} from '../stores'
import { renderMarkdown } from '../hooks/useChat'
import './DocPanel.css'

const GLOBAL_FILE_DROP_EVENT = 'nastyaorc:global-file-drop'

// ===== Утилиты =====

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

function isImage(filename: string): boolean {
  return /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(filename)
}

function isMarkdownLike(filename: string): boolean {
  return /\.(md|markdown|txt)$/i.test(filename)
}

function getDocIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'md': case 'markdown': return '📝'
    case 'txt': return '📄'
    case 'pdf': return '📕'
    case 'csv': return '📊'
    case 'json': return '🔧'
    case 'yaml': case 'yml': return '⚙️'
    case 'docx': case 'doc': return '📃'
    case 'xlsx': case 'xls': return '📊'
    case 'pptx': return '📽️'
    case 'png': case 'jpg': case 'jpeg': case 'gif': case 'webp': return '🖼️'
    default: return '📎'
  }
}

// ===== Построение дерева папок =====

interface FolderNode {
  folder: Folder
  children: FolderNode[]
  documents: Document[]
}

function buildTree(folders: Folder[], documents: Document[]): {
  rootFolders: FolderNode[]
  rootDocuments: Document[]
} {
  const folderMap = new Map<string, FolderNode>()
  for (const f of folders) {
    folderMap.set(f.id, { folder: f, children: [], documents: [] })
  }

  const rootFolders: FolderNode[] = []
  for (const node of folderMap.values()) {
    if (node.folder.parent_id && folderMap.has(node.folder.parent_id)) {
      folderMap.get(node.folder.parent_id)!.children.push(node)
    } else {
      rootFolders.push(node)
    }
  }

  // Сортировка по имени
  const sortNodes = (nodes: FolderNode[]) => {
    nodes.sort((a, b) => a.folder.name.localeCompare(b.folder.name, 'ru'))
    nodes.forEach((n) => sortNodes(n.children))
  }
  sortNodes(rootFolders)

  // Документы по папкам
  const rootDocuments: Document[] = []
  for (const doc of documents) {
    if (doc.folder_id && folderMap.has(doc.folder_id)) {
      folderMap.get(doc.folder_id)!.documents.push(doc)
    } else {
      rootDocuments.push(doc)
    }
  }

  return { rootFolders, rootDocuments }
}

// ===== Inline-редактирование =====

function InlineEdit({
  value,
  onSave,
  onCancel,
}: {
  value: string
  onSave: (v: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState(value)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <input
      ref={inputRef}
      className="doc-panel__inline-edit"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && text.trim()) onSave(text.trim())
        if (e.key === 'Escape') onCancel()
      }}
      onBlur={() => { if (text.trim() && text.trim() !== value) onSave(text.trim()); else onCancel() }}
      autoFocus
    />
  )
}

// ===== Бейдж проекта (для режима "все документы") =====

function ProjectBadge({ projectId }: { projectId: string }) {
  const projects = useProjects()
  const project = projects.find((p) => p.id === projectId)
  const name = project?.name || (projectId === '__common__' ? 'Общие' : projectId.slice(0, 8))
  return <span className="doc-item__project-badge" title={project?.name || projectId}> · {name}</span>
}

// ===== Элемент документа =====

function DocItem({
  doc,
  index,
  projectId,
  showProject,
}: {
  doc: Document
  index: number
  projectId: string
  showProject?: boolean
}) {
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

// ===== Утилита: все document id из папки (рекурсивно) =====

function collectDocIds(node: FolderNode): string[] {
  const ids = node.documents.map((d) => d.id)
  for (const child of node.children) {
    ids.push(...collectDocIds(child))
  }
  return ids
}

// ===== Узел папки (рекурсивный) =====

function FolderItem({
  node,
  projectId,
  docIndexStart,
  showProject,
  globalExpandedKey,
  globalExpanded,
}: {
  node: FolderNode
  projectId: string
  docIndexStart: number
  showProject?: boolean
  globalExpandedKey?: number
  globalExpanded?: boolean
}) {
  const [open, setOpen] = useState(true)

  // Синхронизация с глобальным сворачиванием/разворачиванием
  useEffect(() => {
    if (globalExpandedKey !== undefined && globalExpandedKey > 0) {
      setOpen(!!globalExpanded)
    }
  }, [globalExpandedKey, globalExpanded])
  const [renaming, setRenaming] = useState(false)
  const [creatingSubfolder, setCreatingSubfolder] = useState(false)
  const [dropTarget, setDropTarget] = useState(false)

  const renameFolder = useStore((s) => s.renameFolder)
  const deleteFolder = useStore((s) => s.deleteFolder)
  const createFolder = useStore((s) => s.createFolder)
  const uploadDocument = useStore((s) => s.uploadDocument)
  const moveDocument = useStore((s) => s.moveDocument)
  const docRefsSelected = useStore((s) => s.docRefsSelected)
  const toggleDocRef = useStore((s) => s.toggleDocRef)

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Все документы в этой папке (рекурсивно)
  const allDocIds = useMemo(() => collectDocIds(node), [node])
  const allSelected = allDocIds.length > 0 && allDocIds.every((id) => docRefsSelected.has(id))
  const someSelected = !allSelected && allDocIds.some((id) => docRefsSelected.has(id))

  const handleToggleFolder = () => {
    if (allSelected) {
      // Снять все
      for (const id of allDocIds) {
        if (docRefsSelected.has(id)) toggleDocRef(id)
      }
    } else {
      // Выбрать все
      for (const id of allDocIds) {
        if (!docRefsSelected.has(id)) toggleDocRef(id)
      }
    }
  }

  const totalDocs = node.documents.length +
    node.children.reduce((sum, c) => sum + c.documents.length, 0)

  const handleRename = async (newName: string) => {
    await renameFolder(projectId, node.folder.id, newName)
    setRenaming(false)
  }

  const handleDelete = async () => {
    if (!confirm(`Удалить папку "${node.folder.name}"? Содержимое переместится в корень.`)) return
    await deleteFolder(projectId, node.folder.id)
  }

  const handleCreateSubfolder = async (name: string) => {
    await createFolder(projectId, name, node.folder.id)
    setCreatingSubfolder(false)
  }

  const handleUploadToFolder = async (files: FileList | null) => {
    if (!files) return
    for (const file of Array.from(files)) {
      await uploadDocument(projectId, file, node.folder.id)
    }
  }

  // Drop target — принимаем документы
  const handleFolderDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/x-doc-id')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setDropTarget(true)
    }
  }

  const handleFolderDragLeave = () => setDropTarget(false)

  const handleFolderDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation() // Не всплывать к корневому контейнеру (иначе moveDocument(null) перенесёт в корень)
    setDropTarget(false)
    const docId = e.dataTransfer.getData('application/x-doc-id')
    if (docId) {
      await moveDocument(projectId, docId, node.folder.id)
      setOpen(true)
    }
  }

  let docIdx = docIndexStart

  return (
    <div className="folder-item">
      <div
        className={`folder-item__header ${dropTarget ? 'folder-item__header--drop-target' : ''}`}
        onClick={() => setOpen(!open)}
        onDragOver={handleFolderDragOver}
        onDragLeave={handleFolderDragLeave}
        onDrop={handleFolderDrop}
      >
        {/* Чекбокс — выбрать все документы в папке */}
        <label className="folder-item__checkbox" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => { if (el) el.indeterminate = someSelected }}
            onChange={handleToggleFolder}
          />
        </label>

        <span className={`folder-item__arrow ${open ? 'folder-item__arrow--open' : ''}`}>
          <svg viewBox="0 0 12 12" fill="none"><path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </span>
        <span className="folder-item__icon">
          <svg viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="rgba(99, 102, 241, 0.15)" /></svg>
        </span>

        {renaming ? (
          <InlineEdit
            value={node.folder.name}
            onSave={handleRename}
            onCancel={() => setRenaming(false)}
          />
        ) : (
          <span className="folder-item__name">{node.folder.name}</span>
        )}

        <span className="folder-item__count">{totalDocs}</span>

        <div className="folder-item__actions">
          <button
            className="folder-item__action-btn"
            onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click() }}
            title="Загрузить в папку"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M10 14V4M7 7l3-3 3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /><path d="M3 16h14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
          </button>
          <button
            className="folder-item__action-btn"
            onClick={(e) => { e.stopPropagation(); setCreatingSubfolder(true) }}
            title="Создать подпапку"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M2 5a1.5 1.5 0 011.5-1.5H7l1.5 1.5H17A1.5 1.5 0 0118.5 6.5v9A1.5 1.5 0 0117 17H3.5A1.5 1.5 0 012 15.5V5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /><path d="M10 8.5v5M7.5 11h5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
          </button>
          <button
            className="folder-item__action-btn"
            onClick={(e) => { e.stopPropagation(); setRenaming(true) }}
            title="Переименовать"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M13.5 3.5l3 3L7 16H4v-3L13.5 3.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
          </button>
          <button
            className="folder-item__action-btn folder-item__action-btn--delete"
            onClick={(e) => { e.stopPropagation(); handleDelete() }}
            title="Удалить папку"
          >
            <svg viewBox="0 0 20 20" fill="none"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
          </button>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="doc-panel__hidden-input"
          onChange={(e) => handleUploadToFolder(e.target.files)}
        />
      </div>

      {open && (
        <div className="folder-item__content">
          {/* Создание подпапки */}
          {creatingSubfolder && (
            <div className="folder-item__new-subfolder">
              <InlineEdit
                value=""
                onSave={handleCreateSubfolder}
                onCancel={() => setCreatingSubfolder(false)}
              />
            </div>
          )}

          {/* Подпапки */}
          {node.children.map((child) => {
            const start = docIdx
            docIdx += child.documents.length
            return (
              <FolderItem
                key={child.folder.id}
                node={child}
                projectId={projectId}
                docIndexStart={start}
                showProject={showProject}
                globalExpandedKey={globalExpandedKey}
                globalExpanded={globalExpanded}
              />
            )
          })}

          {/* Документы */}
          {node.documents.map((doc) => (
            <DocItem
              key={doc.id}
              doc={doc}
              index={docIdx++}
              projectId={doc.project_id}
              showProject={showProject}
            />
          ))}

          {node.children.length === 0 && node.documents.length === 0 && (
            <div className="folder-item__empty">Пусто</div>
          )}
        </div>
      )}
    </div>
  )
}

// ===== DocPanel — правый сайдбар =====

export function DocPanel() {
  const docPanelOpen = useDocPanelOpen()
  const documents = useDocuments()
  const folders = useFolders()
  const selectedProjectId = useSelectedProjectId()
  const docViewMode = useDocViewMode()
  const documentsLoading = useStore((s) => s.documentsLoading)
  const uploadDocument = useStore((s) => s.uploadDocument)
  const moveDocument = useStore((s) => s.moveDocument)
  const createFolder = useStore((s) => s.createFolder)
  const toggleDocPanel = useStore((s) => s.toggleDocPanel)
  const setDocPanelOpen = useStore((s) => s.setDocPanelOpen)
  const setDocViewMode = useStore((s) => s.setDocViewMode)
  const docRefsSelected = useStore((s) => s.docRefsSelected)
  const clearDocRefs = useStore((s) => s.clearDocRefs)
  const isAllMode = docViewMode === 'all'

  // Ссылки
  const links = useLinks()
  const addLink = useStore((s) => s.addLink)
  const updateLink = useStore((s) => s.updateLink)
  const deleteLink = useStore((s) => s.deleteLink)
  const linkRefsSelected = useLinkRefsSelected()
  const toggleLinkRef = useStore((s) => s.toggleLinkRef)

  const [showLinkForm, setShowLinkForm] = useState(false)
  const [linkUrl, setLinkUrl] = useState('')
  const [linkTitle, setLinkTitle] = useState('')
  const [linkDesc, setLinkDesc] = useState('')
  const [linkSaving, setLinkSaving] = useState(false)
  const [editingLinkId, setEditingLinkId] = useState<string | null>(null)
  const [deletingLinkId, setDeletingLinkId] = useState<string | null>(null)

  // Глобальное сворачивание/разворачивание папок (счётчик для force-sync)
  const [foldersExpandedKey, setFoldersExpandedKey] = useState(0)
  const [foldersExpanded, setFoldersExpanded] = useState(true)

  const toggleAllFolders = useCallback(() => {
    setFoldersExpanded((prev) => !prev)
    setFoldersExpandedKey((k) => k + 1)
  }, [])

  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [creatingFolder, setCreatingFolder] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  // Модалка выбора папки при загрузке
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null)
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [pickerNewFolder, setPickerNewFolder] = useState(false)
  const [pickerParentId, setPickerParentId] = useState<string | null>(null)
  // Загрузка папки целиком
  const [folderProgress, setFolderProgress] = useState<{ current: number; total: number } | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // Фильтрация документов по поисковому запросу
  const filteredDocuments = useMemo(() => {
    if (!searchQuery.trim()) return documents
    const q = searchQuery.toLowerCase().trim()
    return documents.filter((d) => d.filename.toLowerCase().includes(q))
  }, [documents, searchQuery])

  const { rootFolders, rootDocuments } = useMemo(
    () => buildTree(folders, filteredDocuments),
    [folders, filteredDocuments],
  )

  let globalDocIndex = 0

  // При выборе файлов — показать модалку выбора папки
  const handleFilesSelected = useCallback((files: FileList | File[] | null) => {
    if (!files) return
    const incoming = Array.isArray(files) ? files : Array.from(files)
    if (incoming.length === 0) return
    setPendingFiles(incoming)
    setShowFolderPicker(true)
    setPickerNewFolder(false)
    setPickerParentId(null)
  }, [])

  useEffect(() => {
    const handleGlobalDrop = (event: Event) => {
      const files = (event as CustomEvent<File[]>).detail
      if (!files || files.length === 0) return
      setDocPanelOpen(true)
      handleFilesSelected(files)
    }

    window.addEventListener(GLOBAL_FILE_DROP_EVENT, handleGlobalDrop as EventListener)
    return () => {
      window.removeEventListener(GLOBAL_FILE_DROP_EVENT, handleGlobalDrop as EventListener)
    }
  }, [handleFilesSelected, setDocPanelOpen])

  // Загрузить файлы в выбранную папку
  const handleUploadToFolder = useCallback(async (folderId: string | null) => {
    if (!pendingFiles) return
    const pid = selectedProjectId || '__common__'
    setShowFolderPicker(false)
    setUploadError(null)
    setUploading(true)
    try {
      for (const file of pendingFiles) {
        await uploadDocument(pid, file, folderId)
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Ошибка загрузки')
    } finally {
      setUploading(false)
      setPendingFiles(null)
    }
  }, [pendingFiles, selectedProjectId, uploadDocument])

  // effectiveProjectId для операций — выбранный проект или __common__
  const effectiveProjectId = selectedProjectId || '__common__'

  // Создать папку из picker и загрузить туда
  const handlePickerCreateFolder = useCallback(async (name: string) => {
    await createFolder(effectiveProjectId, name, pickerParentId)
    // Найти только что созданную папку
    const updated = useStore.getState().folders
    const newFolder = updated.find((f) => f.name === name && f.parent_id === pickerParentId)
    if (newFolder) {
      await handleUploadToFolder(newFolder.id)
    } else {
      await handleUploadToFolder(pickerParentId)
    }
    setPickerNewFolder(false)
  }, [effectiveProjectId, createFolder, pickerParentId, handleUploadToFolder])

  // Загрузка папки целиком с сохранением структуры подпапок
  const handleFolderUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploadError(null)

    // Фильтруем файлы — берём только те, у которых есть webkitRelativePath
    const validFiles = Array.from(files).filter((f) => f.webkitRelativePath)
    if (validFiles.length === 0) return

    // Собираем уникальные пути папок из webkitRelativePath
    const folderPaths = new Set<string>()
    for (const file of validFiles) {
      const parts = file.webkitRelativePath.split('/')
      // Все части кроме последней (имя файла) — это папки
      for (let i = 1; i < parts.length; i++) {
        folderPaths.add(parts.slice(0, i).join('/'))
      }
    }

    // Сортируем по глубине (сначала родители)
    const sortedPaths = Array.from(folderPaths).sort((a, b) => {
      const depthA = a.split('/').length
      const depthB = b.split('/').length
      return depthA - depthB || a.localeCompare(b)
    })

    // Маппинг путь → folder id
    const pathToFolderId = new Map<string, string>()

    setFolderProgress({ current: 0, total: validFiles.length })

    try {
      // Создаём папки (с проверкой дублей)
      for (const path of sortedPaths) {
        const parts = path.split('/')
        const folderName = parts[parts.length - 1]
        const parentPath = parts.slice(0, -1).join('/')
        const parentId = parentPath ? (pathToFolderId.get(parentPath) || null) : null

        // Проверяем существующие папки в store по name + parent_id
        const currentFolders = useStore.getState().folders
        const existing = currentFolders.find(
          (f) => f.name === folderName && f.parent_id === parentId && f.project_id === effectiveProjectId,
        )

        if (existing) {
          pathToFolderId.set(path, existing.id)
        } else {
          await createFolder(effectiveProjectId, folderName, parentId)
          // Находим только что созданную папку
          const updated = useStore.getState().folders
          const newFolder = updated.find(
            (f) => f.name === folderName && f.parent_id === parentId && f.project_id === effectiveProjectId,
          )
          if (newFolder) {
            pathToFolderId.set(path, newFolder.id)
          }
        }
      }

      // Загружаем файлы последовательно с прогрессом
      for (let i = 0; i < validFiles.length; i++) {
        const file = validFiles[i]
        const parts = file.webkitRelativePath.split('/')
        const folderPath = parts.slice(0, -1).join('/')
        const folderId = pathToFolderId.get(folderPath) || null

        await uploadDocument(effectiveProjectId, file, folderId)
        setFolderProgress({ current: i + 1, total: validFiles.length })
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Ошибка загрузки папки')
    } finally {
      setFolderProgress(null)
    }
  }, [effectiveProjectId, createFolder, uploadDocument])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    // Если есть файлы с компьютера — показать picker
    if (e.dataTransfer.files.length > 0) {
      handleFilesSelected(e.dataTransfer.files)
      return
    }
    // Drag документа внутри панели обрабатывается в FolderItem/root-drop
    const docId = e.dataTransfer.getData('application/x-doc-id')
    if (docId) {
      // Находим project_id документа для корректного API-вызова
      const doc = documents.find((d) => d.id === docId)
      const pid = doc?.project_id || effectiveProjectId
      moveDocument(pid, docId, null)
    }
  }

  const handleCreateFolder = async (name: string) => {
    await createFolder(effectiveProjectId, name)
    setCreatingFolder(false)
  }

  // === Ссылки ===

  const resetLinkForm = () => {
    setShowLinkForm(false)
    setLinkUrl('')
    setLinkTitle('')
    setLinkDesc('')
    setEditingLinkId(null)
  }

  const handleAddLink = async () => {
    if (!linkUrl.trim()) return
    setLinkSaving(true)
    try {
      if (editingLinkId) {
        await updateLink(effectiveProjectId, editingLinkId, {
          title: linkTitle.trim() || linkUrl.trim(),
          url: linkUrl.trim(),
          description: linkDesc.trim(),
        })
      } else {
        await addLink(effectiveProjectId, linkTitle.trim() || linkUrl.trim(), linkUrl.trim(), linkDesc.trim())
      }
      resetLinkForm()
    } catch (err) {
      console.error('Ошибка сохранения ссылки:', err)
    } finally {
      setLinkSaving(false)
    }
  }

  const handleDeleteLink = async (link: Link) => {
    if (!confirm(`Удалить ссылку "${link.title}"?`)) return
    setDeletingLinkId(link.id)
    try {
      await deleteLink(effectiveProjectId, link.id)
    } finally {
      setDeletingLinkId(null)
    }
  }

  const handleEditLink = (link: Link) => {
    setLinkUrl(link.url)
    setLinkTitle(link.title)
    setLinkDesc(link.description || '')
    setEditingLinkId(link.id)
    setShowLinkForm(true)
  }

  const handleLinkFormKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAddLink()
    }
    if (e.key === 'Escape') {
      resetLinkForm()
    }
  }

  // DocPanel доступна если есть проект ИЛИ в режиме "все"
  if (!selectedProjectId && !isAllMode) return null

  return (
    <aside className={`doc-panel ${docPanelOpen ? 'doc-panel--open' : 'doc-panel--closed'}`}>
      {/* Кнопка-вкладка для открытия/закрытия */}
      <button
        className="doc-panel__toggle"
        onClick={toggleDocPanel}
        title={docPanelOpen ? 'Скрыть документы' : 'Показать документы'}
      >
        <svg viewBox="0 0 16 16" fill="none" className={docPanelOpen ? 'doc-panel__toggle-icon--open' : ''}>
          <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {!docPanelOpen && (
          <span className="doc-panel__toggle-badge">
            {documents.length}
          </span>
        )}
      </button>

      {docPanelOpen && (
        <div className="doc-panel__inner">
          {/* Заголовок */}
          <div className="doc-panel__header">
            <h3 className="doc-panel__title">Документы</h3>

            {/* Переключатель Проект / Все */}
            <div className="doc-panel__view-toggle">
              <button
                className={`doc-panel__view-btn ${!isAllMode ? 'doc-panel__view-btn--active' : ''}`}
                onClick={() => setDocViewMode('project')}
                disabled={!selectedProjectId}
              >
                Проект
              </button>
              <button
                className={`doc-panel__view-btn ${isAllMode ? 'doc-panel__view-btn--active' : ''}`}
                onClick={() => setDocViewMode('all')}
              >
                Все
              </button>
            </div>

            <div className="doc-panel__header-actions">
              {rootFolders.length > 0 && (
                <button
                  className="doc-panel__btn"
                  onClick={toggleAllFolders}
                  title={foldersExpanded ? 'Свернуть все папки' : 'Развернуть все папки'}
                >
                  <svg viewBox="0 0 24 24" fill="none">
                    {foldersExpanded ? (
                      <><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><path d="M18 9l-2 3 2 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></>
                    ) : (
                      <><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><path d="M16 9l2 3-2 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></>
                    )}
                  </svg>
                </button>
              )}
              <button
                className="doc-panel__btn"
                onClick={() => setShowLinkForm(!showLinkForm)}
                title="Добавить ссылку"
              >
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <button
                className="doc-panel__btn"
                onClick={() => setCreatingFolder(true)}
                title="Создать папку"
              >
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                  <path d="M12 10v6M9 13h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
              <button
                className="doc-panel__btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                title="Загрузить файл"
              >
                {uploading ? (
                  <span className="doc-panel__spinner" />
                ) : (
                  <svg viewBox="0 0 24 24" fill="none">
                    <path d="M12 16V4M8 8l4-4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M4 18h16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                )}
              </button>
              <button
                className="doc-panel__btn"
                onClick={() => folderInputRef.current?.click()}
                disabled={!!folderProgress || uploading}
                title="Загрузить папку"
              >
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                  <path d="M12 14V9M9.5 11.5L12 9l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="doc-panel__hidden-input"
              onChange={(e) => { handleFilesSelected(e.target.files); e.target.value = '' }}
              accept=".txt,.md,.pdf,.docx,.csv,.json,.yaml,.yml,.png,.jpg,.jpeg,.gif,.webp,.xlsx,.xls,.pptx"
            />
            <input
              ref={folderInputRef}
              type="file"
              // @ts-expect-error — webkitdirectory нестандартный атрибут
              webkitdirectory=""
              multiple
              className="doc-panel__hidden-input"
              onChange={(e) => { handleFolderUpload(e.target.files); e.target.value = '' }}
            />
          </div>

          {/* Форма добавления/редактирования ссылки */}
          {showLinkForm && (
            <div className="doc-panel__link-form">
              <input
                className="doc-panel__link-input"
                type="url"
                placeholder="URL ссылки *"
                value={linkUrl}
                onChange={(e) => setLinkUrl(e.target.value)}
                onKeyDown={handleLinkFormKeyDown}
                autoFocus
              />
              <input
                className="doc-panel__link-input"
                type="text"
                placeholder="Название (необязательно)"
                value={linkTitle}
                onChange={(e) => setLinkTitle(e.target.value)}
                onKeyDown={handleLinkFormKeyDown}
              />
              <input
                className="doc-panel__link-input"
                type="text"
                placeholder="Описание — для чего"
                value={linkDesc}
                onChange={(e) => setLinkDesc(e.target.value)}
                onKeyDown={handleLinkFormKeyDown}
              />
              <div className="doc-panel__link-form-actions">
                <button className="doc-panel__link-btn doc-panel__link-btn--cancel" onClick={resetLinkForm}>
                  Отмена
                </button>
                <button
                  className="doc-panel__link-btn doc-panel__link-btn--save"
                  onClick={handleAddLink}
                  disabled={!linkUrl.trim() || linkSaving}
                >
                  {linkSaving ? '...' : editingLinkId ? 'Сохранить' : 'Добавить'}
                </button>
              </div>
            </div>
          )}

          {/* Прогресс загрузки папки */}
          {folderProgress && (
            <div className="doc-panel__folder-progress">
              <div className="doc-panel__folder-progress-bar" style={{ width: `${Math.round((folderProgress.current / folderProgress.total) * 100)}%` }} />
              <span className="doc-panel__folder-progress-label">
                Загрузка папки: {folderProgress.current} / {folderProgress.total}
              </span>
            </div>
          )}

          {/* Поиск */}
          <div className="doc-panel__search">
            <svg className="doc-panel__search-icon" viewBox="0 0 20 20" fill="none">
              <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.4" />
              <path d="M13.5 13.5L17 17" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <input
              className="doc-panel__search-input"
              type="text"
              placeholder="Поиск документов..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                className="doc-panel__search-clear"
                onClick={() => setSearchQuery('')}
              >
                <svg viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>

          {/* Выделенные документы для вставки в чат */}
          {docRefsSelected.size > 0 && (
            <div className="doc-panel__refs-bar">
              <span>Выбрано: {docRefsSelected.size}</span>
              <button className="doc-panel__refs-clear" onClick={clearDocRefs}>Снять</button>
            </div>
          )}

          {uploadError && (
            <div className="doc-panel__error">{uploadError}</div>
          )}

          {/* Содержимое */}
          <div
            className={`doc-panel__content ${dragOver ? 'doc-panel__content--dragover' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            {documentsLoading && documents.length === 0 && (
              <div className="doc-panel__loading">
                <span className="doc-panel__spinner" />
              </div>
            )}

            {!documentsLoading && documents.length === 0 && folders.length === 0 && (
              <div className="doc-panel__empty">
                <p>Нет документов</p>
                <span>Перетащите файлы сюда или нажмите ⬆</span>
              </div>
            )}

            {!documentsLoading && searchQuery && filteredDocuments.length === 0 && documents.length > 0 && (
              <div className="doc-panel__empty">
                <p>Ничего не найдено</p>
                <span>по запросу «{searchQuery}»</span>
              </div>
            )}

            {/* Создание папки в корне */}
            {creatingFolder && (
              <div className="doc-panel__new-folder">
                <svg viewBox="0 0 24 24" fill="none" width="20" height="20"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="rgba(99, 102, 241, 0.15)" /></svg>
                <InlineEdit
                  value=""
                  onSave={handleCreateFolder}
                  onCancel={() => setCreatingFolder(false)}
                />
              </div>
            )}

            {/* Дерево папок */}
            {rootFolders.map((node) => {
              const start = globalDocIndex
              globalDocIndex += node.documents.length
              return (
                <FolderItem
                  key={node.folder.id}
                  node={node}
                  projectId={node.folder.project_id}
                  docIndexStart={start}
                  showProject={isAllMode}
                  globalExpandedKey={foldersExpandedKey}
                  globalExpanded={foldersExpanded}
                />
              )
            })}

            {/* Документы в корне */}
            {rootDocuments.map((doc) => (
              <DocItem
                key={doc.id}
                doc={doc}
                index={globalDocIndex++}
                projectId={doc.project_id}
                showProject={isAllMode}
              />
            ))}

            {/* Ссылки */}
            {links.length > 0 && (
              <div className="doc-panel__links-section">
                <div className="doc-panel__links-header">
                  <svg viewBox="0 0 24 24" fill="none" width="16" height="16">
                    <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span>Ссылки</span>
                </div>
                {links.map((link) => {
                  const linkChecked = linkRefsSelected.has(link.id)
                  return (
                  <div key={link.id} className={`doc-panel__link-item ${linkChecked ? 'doc-panel__link-item--checked' : ''} ${deletingLinkId === link.id ? 'doc-panel__link-item--deleting' : ''}`}>
                    <label className="doc-panel__link-checkbox" onClick={(e) => e.stopPropagation()} title="Отметить ссылку для анализа">
                      <input
                        type="checkbox"
                        checked={linkChecked}
                        onChange={() => toggleLinkRef(link.id)}
                      />
                    </label>
                    <div className="doc-panel__link-info">
                      <a className="doc-panel__link-title" href={link.url} target="_blank" rel="noopener noreferrer" title={link.url}>
                        {link.title}
                      </a>
                      {link.description && <span className="doc-panel__link-desc">{link.description}</span>}
                    </div>
                    <div className="doc-panel__link-item-actions">
                      <button className="doc-item__action-btn" onClick={() => handleEditLink(link)} title="Редактировать">
                        <svg viewBox="0 0 20 20" fill="none"><path d="M13.5 3.5l3 3L7 16H4v-3L13.5 3.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
                      </button>
                      <button className="doc-item__action-btn doc-item__action-btn--delete" onClick={() => handleDeleteLink(link)} title="Удалить">
                        <svg viewBox="0 0 20 20" fill="none"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
                      </button>
                    </div>
                  </div>
                  )
                })}
              </div>
            )}

          </div>

          {/* Модалка выбора папки при загрузке */}
          {showFolderPicker && pendingFiles && (
            <div className="folder-picker__overlay" onClick={() => { setShowFolderPicker(false); setPendingFiles(null) }}>
              <div className="folder-picker" onClick={(e) => e.stopPropagation()}>
                <div className="folder-picker__header">
                  <h4>Куда сохранить?</h4>
                  <span className="folder-picker__file-count">
                    {pendingFiles.length} {pendingFiles.length === 1 ? 'файл' : 'файлов'}
                  </span>
                </div>

                <div className="folder-picker__list">
                  {/* В корень */}
                  <button
                    className="folder-picker__item"
                    onClick={() => handleUploadToFolder(null)}
                  >
                    <svg viewBox="0 0 24 24" fill="none"><path d="M3 12h18M12 3v18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0" /><path d="M4 4h16v16H4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" rx="2" /></svg>
                    <span>Корневая папка</span>
                  </button>

                  {/* Все папки (flat, с отступами) */}
                  {renderPickerFolders(rootFolders, 0, handleUploadToFolder)}

                  {/* Создать новую папку */}
                  {pickerNewFolder ? (
                    <div className="folder-picker__new">
                      <svg viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="rgba(99, 102, 241, 0.15)" /></svg>
                      <InlineEdit
                        value=""
                        onSave={handlePickerCreateFolder}
                        onCancel={() => setPickerNewFolder(false)}
                      />
                    </div>
                  ) : (
                    <button
                      className="folder-picker__item folder-picker__item--create"
                      onClick={() => setPickerNewFolder(true)}
                    >
                      <svg viewBox="0 0 24 24" fill="none">
                        <path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                        <path d="M12 10v6M9 13h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                      </svg>
                      <span>Создать новую папку</span>
                    </button>
                  )}
                </div>

                <button
                  className="folder-picker__cancel"
                  onClick={() => { setShowFolderPicker(false); setPendingFiles(null) }}
                >
                  Отмена
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  )
}

// Рекурсивный рендер папок в picker
function renderPickerFolders(
  nodes: FolderNode[],
  depth: number,
  onSelect: (id: string) => void,
): React.ReactNode[] {
  const result: React.ReactNode[] = []
  for (const node of nodes) {
    result.push(
      <button
        key={node.folder.id}
        className="folder-picker__item"
        style={{ paddingLeft: `${20 + depth * 24}px` }}
        onClick={() => onSelect(node.folder.id)}
      >
        <svg viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="rgba(99, 102, 241, 0.15)" /></svg>
        <span>{node.folder.name}</span>
        {node.documents.length > 0 && (
          <span className="folder-picker__count">{node.documents.length}</span>
        )}
      </button>,
    )
    if (node.children.length > 0) {
      result.push(...renderPickerFolders(node.children, depth + 1, onSelect))
    }
  }
  return result
}
