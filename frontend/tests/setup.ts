import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

/**
 * Recharts measures its container with `ResizeObserver`, which jsdom does not
 * implement. A minimal stub is enough: the charts are asserted on their SVG
 * structure, not on pixel dimensions.
 */
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver ??= ResizeObserverStub

/** jsdom implements no layout, so scrolling is a no-op rather than a crash. */
Element.prototype.scrollIntoView ??= function scrollIntoView(): void {}

afterEach(() => {
  cleanup()
})
