// Узел папки — раскрытие/сворачивание, drag-drop target, rename, delete, create-subfolder
import { useState, useMemo, useRef, useEffect } from 'react'
import { useStore } from '../../stores'
import { InlineEdit } from './InlineEdit'
import { DocItem } from './DocItem'
import { collectDocIds, type FolderNode } from './types'

interface FolderItemProps {
  node: FolderNode
  projectId: string
  docIndexStart: number
  showProject?: boolean
  globalExpandedKey?: number
  globalExpanded?: boolean
}

export function FolderItem({
  node,
  projectId,
  docIndexStart,
  showProject,
  globalExpandedKey,
  globalExpanded,
}: FolderItemProps) {
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

  // Все документы в папке (рекурсивно) для выделения чекбокса
  const allDocIds = useMemo(() => collectDocIds(node), [node])
  const allSelected = allDocIds.length > 0 && allDocIds.every((id) => docRefsSelected.has(id))
  const someSelected = !allSelected && allDocIds.some((id) => docRefsSelected.has(id))

  const handleToggleFolder = () => {
    if (allSelected) {
      for (const id of allDocIds) {
        if (docRefsSelected.has(id)) toggleDocRef(id)
      }
    } else {
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
    e.stopPropagation() // Не всплывать к корневому контейнеру
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

          {/* Рекурсивные подпапки */}
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
