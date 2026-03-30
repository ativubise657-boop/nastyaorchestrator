import { Component, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// ErrorBoundary для отлова ошибок рендеринга
class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: '#ef4444', background: '#140d1e', minHeight: '100vh', fontFamily: 'monospace' }}>
          <h1>Ошибка рендеринга</h1>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: 16 }}>
            {this.state.error.message}
            {'\n\n'}
            {this.state.error.stack}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('Root element not found')

createRoot(rootElement).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
)
