// Вспомогательные контролы нижней панели: ModelSelector, FontSizeControl
import { useStore, type ChatModel } from '../../stores'

const MODEL_OPTIONS: Array<{ id: ChatModel; label: string }> = [
  { id: 'gpt-5.4', label: 'GPT 5' },
  { id: 'gpt-5.3-codex', label: 'GPT 5 Reasoning' },
]

export function ModelSelector({ selected }: { selected: ChatModel }) {
  return (
    <div className="model-selector">
      {MODEL_OPTIONS.map((model) => (
        <button
          key={model.id}
          className={`model-selector__btn ${model.id === selected ? 'model-selector__btn--active' : ''}`}
          onClick={() => useStore.getState().setSelectedModel(model.id)}
        >
          {model.label}
        </button>
      ))}
    </div>
  )
}

export function FontSizeControl({ size, onChange }: { size: number; onChange: (s: number) => void }) {
  return (
    <div className="font-size-control">
      <button
        className="font-size-control__btn"
        onClick={() => onChange(size - 2)}
        title="Уменьшить шрифт"
      >
        A−
      </button>
      <span className="font-size-control__value">{size}</span>
      <button
        className="font-size-control__btn"
        onClick={() => onChange(size + 2)}
        title="Увеличить шрифт"
      >
        A+
      </button>
    </div>
  )
}
