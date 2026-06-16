# CQR Frontend

Desktop application for the CQR (Code Query & Refactor) agent system.

## Technology Stack

| Layer | Technology |
|---|---|
| Framework | React 19 + TypeScript |
| Build | Vite 8 |
| State | Zustand |
| Routing | React Router v7 |
| Editor | Monaco Editor (`@monaco-editor/react`) |
| Graph | D3 v7 (canvas-based) |
| Styling | Vanilla CSS with design tokens |

## Architecture

```
src/
├── api/client.ts              # All backend API calls (7 services)
├── components/
│   ├── index.tsx              # Shared component library
│   └── Layout/AppShell.tsx    # Global shell + command palette
├── screens/
│   ├── ProjectHub/            # §7.1 Project list
│   ├── Onboarding/            # §7.2 3-step repo connect
│   ├── IDE/                   # §7.3 Monaco + agent activity
│   ├── KGExplorer/            # §7.4 Canvas force-directed graph (HERO)
│   ├── LSMView/               # §7.5 Radial proximity canvas (HERO)
│   ├── SecurityScanner/       # §7.6 Findings table
│   ├── DeployGate/            # §7.7 Pre-deploy checklist
│   ├── Vault/                 # §7.8 Write-only secrets
│   ├── Sandbox/               # §7.9 Container status
│   ├── Connectors/            # §7.10 Integrations
│   └── Settings/              # §7.11 Preferences
├── stores/index.ts            # Zustand stores
└── styles/                    # Design tokens + global + components
```

## Development

```bash
npm install
npm run dev      # Vite dev server at http://localhost:5173
npm run build    # Production build → dist/
```
