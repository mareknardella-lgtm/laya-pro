# Operator dashboard

This is a static, dependency-free dashboard served by the same local FastAPI process as the operator APIs. It uses the current origin and `/api/v1` base path, makes no mock-data fallback, and sends the configured `X-Laya-Approval-Token` only after the operator enters it. The token remains in memory until tab close, disconnect, or an HTTP 401; it is not stored in local or session storage.

## Run

Install the Python backend dependencies as described in the root README and start Uvicorn. Then open `http://127.0.0.1:8765/dashboard/` (or `/`, which redirects there). No Node package installation or separate frontend server is required.

Set `LAYA_APPROVAL_TOKEN` in the local environment before startup to enable operator-only pages. This shared local token is not multi-user authentication; do not expose the backend or dashboard to untrusted networks.

## Features

- Overview of authorized backend status, currently pending approvals, workflows and actual durable tool invocations.
- Authenticated project selector sourced from configured backend project IDs; selection filters operator list, detail, status and transition requests, while the backend revalidates project scope. No project paths are sent to the browser.
- Approval list/details, safe parameter previews, validation/expiration warnings and explicit approve/reject confirmation. The backend repeats all authorization checks; approval itself does not run the operation.
- Workflow list/details and confirmation-gated pause/resume/cancel. Resume uses the backend transition and approval checks.
- Execution history with identifier search, status/date filters, sorting, pagination and details. Inputs/outputs are not stored or displayed.
- System status/adapter observations and read-only deployment settings; secret configuration is never returned by the configuration endpoint.
- Responsive semantic HTML, keyboard-visible focus, loading/error/empty states, toast feedback and reduced-motion support.

## Frontend check

From the repository root, run `npm test --prefix frontend` and `npm run check --prefix frontend`. The tests use only Node's built-in test runner and a minimal DOM harness; they test endpoint wiring/rendering/confirmation behavior, not a full Chromium rendering engine. Python/API regression coverage is in `backend/tests`.
