/// <reference types="vite/client" />

/** Build-time configuration injected by Vite. */
interface ImportMetaEnv {
  /** Base URL of the API. Empty means same origin (the nginx deployment). */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
