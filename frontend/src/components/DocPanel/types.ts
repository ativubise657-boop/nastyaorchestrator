// Общие типы для DocPanel и подкомпонентов
import type { Document, Folder } from '../../stores'

export interface FolderNode {
  folder: Folder
  children: FolderNode[]
  documents: Document[]
}

export function buildTree(folders: Folder[], documents: Document[]): {
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

  // Распределяем документы по папкам
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

// Собрать все document id из папки (рекурсивно)
export function collectDocIds(node: FolderNode): string[] {
  const ids = node.documents.map((d) => d.id)
  for (const child of node.children) {
    ids.push(...collectDocIds(child))
  }
  return ids
}

// Форматирование
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

export function isImage(filename: string): boolean {
  return /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(filename)
}

export function isMarkdownLike(filename: string): boolean {
  return /\.(md|markdown|txt)$/i.test(filename)
}

export function getDocIcon(filename: string): string {
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
