# Frontend workspace conventions

`src/api/` owns typed calls to FastAPI; components must not call `fetch` directly.
`src/features/` owns scenario-specific UI, hooks and styles. `src/components/` is for reusable,
business-agnostic UI. Keep route composition inside `src/pages/` and `src/app/`.

Use TypeScript, semantic HTML and CSS Modules or the shared token styles. Do not add mock business
data, expose secrets, log access tokens or persist API credentials. Every async surface needs an
explicit loading, empty, success and error state.

Name tests `*.test.ts` or `*.test.tsx`. Generated `dist/`, `coverage/`, `playwright-report/`,
`test-results/` and `node_modules/` are local artifacts and must not be committed.
