/**
 * `ChatPanel` — the conversation: messages, agent trace, charts and the composer.
 *
 * # TODO(R08): every message is mounted at once. Above a few hundred turns the
 * list should be virtualised.
 */

import { useEffect, useRef, useState } from 'react'

import { AgentTrace } from '@/components/AgentTrace'
import { ChartRenderer } from '@/components/ChartRenderer'
import type { LiveMessage } from '@/types'

import './ChatPanel.css'

const STOP_REASON_LABEL: Record<string, string> = {
  max_iterations: "L'agent a atteint sa limite d'appels d'outils.",
  timeout: "L'agent a dépassé le temps imparti.",
}

export interface ChatPanelProps {
  messages: LiveMessage[]
  isStreaming: boolean
  disabled: boolean
  suggestions: string[]
  onSend: (text: string) => void
  onCancel: () => void
}

export function ChatPanel({
  messages,
  isStreaming,
  disabled,
  suggestions,
  onSend,
  onCancel,
}: ChatPanelProps): JSX.Element {
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  const submit = (text: string): void => {
    if (disabled || isStreaming) return
    const trimmed = text.trim()
    if (trimmed === '') return
    onSend(trimmed)
    setDraft('')
  }

  return (
    <section className="chat panel" aria-label="Conversation">
      <div className="chat__scroll">
        {messages.length === 0 ? (
          <Welcome suggestions={suggestions} disabled={disabled || isStreaming} onPick={submit} />
        ) : (
          messages.map((message) => <MessageBubble key={message.id} message={message} />)
        )}
        <div ref={bottomRef} />
      </div>

      <form
        className="chat__composer"
        onSubmit={(event) => {
          event.preventDefault()
          submit(draft)
        }}
      >
        <label className="visually-hidden" htmlFor="chat-input">
          Votre question
        </label>
        <textarea
          id="chat-input"
          className="chat__input"
          rows={2}
          placeholder="Posez une question sur le jeu de données…"
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit(draft)
            }
          }}
        />
        {isStreaming ? (
          <button type="button" className="chat__button chat__button--stop" onClick={onCancel}>
            Interrompre
          </button>
        ) : (
          <button type="submit" className="chat__button" disabled={disabled || draft.trim() === ''}>
            Envoyer
          </button>
        )}
      </form>
    </section>
  )
}

function Welcome({
  suggestions,
  disabled,
  onPick,
}: {
  suggestions: string[]
  disabled: boolean
  onPick: (text: string) => void
}): JSX.Element {
  return (
    <div className="chat__welcome">
      <h2>Posez une question à l’agent</h2>
      <p>
        Chaque chiffre affiché vient d’un outil Python déterministe. Le modèle choisit les outils
        et rédige la réponse ; il ne calcule rien lui-même.
      </p>
      <ul className="chat__suggestions">
        {suggestions.map((suggestion) => (
          <li key={suggestion}>
            <button
              type="button"
              className="chat__suggestion"
              disabled={disabled}
              onClick={() => onPick(suggestion)}
            >
              {suggestion}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function MessageBubble({ message }: { message: LiveMessage }): JSX.Element {
  const isUser = message.role === 'user'
  const notice =
    message.stoppedReason !== undefined ? STOP_REASON_LABEL[message.stoppedReason] : undefined

  return (
    <article className={`message message--${message.role}`} data-testid={`message-${message.role}`}>
      <header className="message__role">{isUser ? 'Vous' : 'DataPilot'}</header>

      {!isUser ? (
        <AgentTrace invocations={message.toolInvocations} status={message.status} />
      ) : null}

      {message.content !== '' ? (
        <div className="message__content">
          {message.content.split('\n').map((line, index) => (
            <p key={index}>{line}</p>
          ))}
        </div>
      ) : null}

      {message.charts.map((chart, index) => (
        <ChartRenderer key={`${chart.title}-${index}`} spec={chart} />
      ))}

      {notice !== undefined ? <p className="message__notice">{notice}</p> : null}

      {message.error !== undefined ? (
        <p className="message__error" role="alert">
          {message.error.message}
          {message.error.detail !== null && message.error.detail !== undefined
            ? ` ${message.error.detail}`
            : ''}
        </p>
      ) : null}
    </article>
  )
}
