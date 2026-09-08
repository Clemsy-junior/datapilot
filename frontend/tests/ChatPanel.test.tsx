import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/ChatPanel'
import type { LiveMessage } from '@/types'

const SUGGESTIONS = ['Quelle région performe le mieux ?', 'Y a-t-il des prix aberrants ?']

function renderPanel(overrides: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const onSend = vi.fn()
  const onCancel = vi.fn()
  render(
    <ChatPanel
      messages={[]}
      isStreaming={false}
      disabled={false}
      suggestions={SUGGESTIONS}
      onSend={onSend}
      onCancel={onCancel}
      {...overrides}
    />,
  )
  return { onSend, onCancel }
}

const answer: LiveMessage = {
  id: 'msg_1',
  role: 'assistant',
  content: 'APAC domine.\nEMEA suit.',
  toolInvocations: [
    {
      id: 'c1',
      name: 'aggregate',
      arguments: { group_by: ['region'] },
      duration_ms: 2,
      ok: true,
      result: { rows: [{ region: 'APAC', value: 5130 }] },
      error: null,
    },
  ],
  charts: [],
  pending: false,
}

describe('ChatPanel', () => {
  it('offers the starter questions on an empty conversation', () => {
    renderPanel()

    for (const suggestion of SUGGESTIONS) {
      expect(screen.getByRole('button', { name: suggestion })).toBeInTheDocument()
    }
  })

  it('sends a starter question when it is clicked', async () => {
    const user = userEvent.setup()
    const { onSend } = renderPanel()

    await user.click(screen.getByRole('button', { name: SUGGESTIONS[0] }))

    expect(onSend).toHaveBeenCalledWith(SUGGESTIONS[0])
  })

  it('submits the composer with Enter and clears it', async () => {
    const user = userEvent.setup()
    const { onSend } = renderPanel()
    const input = screen.getByLabelText('Votre question')

    await user.type(input, 'Combien de commandes ?{Enter}')

    expect(onSend).toHaveBeenCalledWith('Combien de commandes ?')
    expect(input).toHaveValue('')
  })

  it('inserts a newline on Shift+Enter instead of sending', async () => {
    const user = userEvent.setup()
    const { onSend } = renderPanel()
    const input = screen.getByLabelText('Votre question')

    await user.type(input, 'première ligne{Shift>}{Enter}{/Shift}seconde')

    expect(onSend).not.toHaveBeenCalled()
    expect(input).toHaveValue('première ligne\nseconde')
  })

  it('refuses to send whitespace', async () => {
    const user = userEvent.setup()
    const { onSend } = renderPanel()

    await user.type(screen.getByLabelText('Votre question'), '   {Enter}')

    expect(onSend).not.toHaveBeenCalled()
  })

  it('swaps the send button for an interrupt button while streaming', async () => {
    const user = userEvent.setup()
    const { onCancel } = renderPanel({ isStreaming: true, messages: [answer] })

    expect(screen.queryByRole('button', { name: 'Envoyer' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Interrompre' }))

    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('disables the composer without a dataset', () => {
    renderPanel({ disabled: true })

    expect(screen.getByLabelText('Votre question')).toBeDisabled()
  })

  it('renders an answer, its paragraphs and its agent trace', () => {
    renderPanel({ messages: [answer] })

    expect(screen.getByTestId('message-assistant')).toBeInTheDocument()
    expect(screen.getByText('APAC domine.')).toBeInTheDocument()
    expect(screen.getByText('EMEA suit.')).toBeInTheDocument()
    expect(screen.getByText('aggregate')).toBeInTheDocument()
  })

  it('explains why the agent stopped early', () => {
    renderPanel({ messages: [{ ...answer, stoppedReason: 'max_iterations' }] })

    expect(screen.getByText(/limite d'appels d'outils/)).toBeInTheDocument()
  })

  it('shows a stream error to the user', () => {
    renderPanel({
      messages: [
        {
          ...answer,
          error: { code: 'stream_closed', message: 'Connexion interrompue.', detail: 'Réessayez.' },
        },
      ],
    })

    expect(screen.getByRole('alert')).toHaveTextContent('Connexion interrompue. Réessayez.')
  })

  it('does not render an agent trace for the user’s own message', () => {
    renderPanel({
      messages: [
        {
          id: 'u1',
          role: 'user',
          content: 'Quelle région ?',
          toolInvocations: [],
          charts: [],
          pending: false,
        },
      ],
    })

    expect(screen.getByTestId('message-user')).toBeInTheDocument()
    expect(screen.queryByTestId('trace-step')).toBeNull()
  })
})
