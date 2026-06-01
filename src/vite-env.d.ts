/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OPENWEBUI_API_KEY?: string;
  readonly VITE_OPENWEBUI_MODEL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}