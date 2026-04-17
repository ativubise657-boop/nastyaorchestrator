// Модальное окно выбора папки при загрузке файлов
import { InlineEdit } from './InlineEdit'
import { type FolderNode } from './types'

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

interface FolderPickerProps {
  pendingFiles: File[]
  rootFolders: FolderNode[]
  pickerNewFolder: boolean
  onSelectFolder: (id: string | null) => void
  onCreateFolder: (name: string) => void
  onShowNewFolder: () => void
  onCancelNewFolder: () => void
  onClose: () => void
}

export function FolderPicker({
  pendingFiles,
  rootFolders,
  pickerNewFolder,
  onSelectFolder,
  onCreateFolder,
  onShowNewFolder,
  onCancelNewFolder,
  onClose,
}: FolderPickerProps) {
  return (
    <div className="folder-picker__overlay" onClick={onClose}>
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
            onClick={() => onSelectFolder(null)}
          >
            <svg viewBox="0 0 24 24" fill="none"><path d="M3 12h18M12 3v18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0" /><path d="M4 4h16v16H4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" rx="2" /></svg>
            <span>Корневая папка</span>
          </button>

          {/* Все папки (flat, с отступами) */}
          {renderPickerFolders(rootFolders, 0, onSelectFolder)}

          {/* Создать новую папку */}
          {pickerNewFolder ? (
            <div className="folder-picker__new">
              <svg viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="rgba(99, 102, 241, 0.15)" /></svg>
              <InlineEdit
                value=""
                onSave={onCreateFolder}
                onCancel={onCancelNewFolder}
              />
            </div>
          ) : (
            <button
              className="folder-picker__item folder-picker__item--create"
              onClick={onShowNewFolder}
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
          onClick={onClose}
        >
          Отмена
        </button>
      </div>
    </div>
  )
}
