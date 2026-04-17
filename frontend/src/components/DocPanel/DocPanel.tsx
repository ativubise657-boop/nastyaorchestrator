// DocPanel — правый сайдбар с документами, папками и ссылками.
// Оркестрирует загрузку, drag-drop, поиск, ссылки, folder-picker.
import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import {
  useStore,
  useDocuments,
  useSelectedProjectId,
  useDocPanelOpen,
  useFolders,
  useDocViewMode,
  useLinks,
  useLinkRefsSelected,
  type Link,
} from '../../stores'
import { FolderItem } from './FolderItem'
import { DocItem } from './DocItem'
import { FolderPicker } from './FolderPicker'
import { InlineEdit } from './InlineEdit'
import { buildTree } from './types'
import '../DocPanel.css'

const GLOBAL_FILE_DROP_EVENT = 'nastyaorc:global-file-drop'

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

  // Глобальное сворачивание/разворачивание папок
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
  // Состояние модалки выбора папки при загрузке
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null)
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [pickerNewFolder, setPickerNewFolder] = useState(false)
  const [pickerParentId, setPickerParentId] = useState<string | null>(null)
  // Прогресс загрузки папки целиком
  const [folderProgress, setFolderProgress] = useState<{ current: number; total: number } | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // Фильтрация документов по поиску
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

  // Слушаем глобальный дроп из App
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

  const effectiveProjectId = selectedProjectId || '__common__'

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

  // Создать папку из picker и загрузить туда
  const handlePickerCreateFolder = useCallback(async (name: string) => {
    await createFolder(effectiveProjectId, name, pickerParentId)
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

    const validFiles = Array.from(files).filter((f) => f.webkitRelativePath)
    if (validFiles.length === 0) return

    const folderPaths = new Set<string>()
    for (const file of validFiles) {
      const parts = file.webkitRelativePath.split('/')
      for (let i = 1; i < parts.length; i++) {
        folderPaths.add(parts.slice(0, i).join('/'))
      }
    }

    const sortedPaths = Array.from(folderPaths).sort((a, b) => {
      const depthA = a.split('/').length
      const depthB = b.split('/').length
      return depthA - depthB || a.localeCompare(b)
    })

    const pathToFolderId = new Map<string, string>()
    setFolderProgress({ current: 0, total: validFiles.length })

    try {
      for (const path of sortedPaths) {
        const parts = path.split('/')
        const folderName = parts[parts.length - 1]
        const parentPath = parts.slice(0, -1).join('/')
        const parentId = parentPath ? (pathToFolderId.get(parentPath) || null) : null

        const currentFolders = useStore.getState().folders
        const existing = currentFolders.find(
          (f) => f.name === folderName && f.parent_id === parentId && f.project_id === effectiveProjectId,
        )

        if (existing) {
          pathToFolderId.set(path, existing.id)
        } else {
          await createFolder(effectiveProjectId, folderName, parentId)
          const updated = useStore.getState().folders
          const newFolder = updated.find(
            (f) => f.name === folderName && f.parent_id === parentId && f.project_id === effectiveProjectId,
          )
          if (newFolder) {
            pathToFolderId.set(path, newFolder.id)
          }
        }
      }

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
    if (e.dataTransfer.files.length > 0) {
      handleFilesSelected(e.dataTransfer.files)
      return
    }
    const docId = e.dataTransfer.getData('application/x-doc-id')
    if (docId) {
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

          {/* Основной контент с drag-drop зоной */}
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
            <FolderPicker
              pendingFiles={pendingFiles}
              rootFolders={rootFolders}
              pickerNewFolder={pickerNewFolder}
              onSelectFolder={handleUploadToFolder}
              onCreateFolder={handlePickerCreateFolder}
              onShowNewFolder={() => setPickerNewFolder(true)}
              onCancelNewFolder={() => setPickerNewFolder(false)}
              onClose={() => { setShowFolderPicker(false); setPendingFiles(null) }}
            />
          )}
        </div>
      )}
    </aside>
  )
}
