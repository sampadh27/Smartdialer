# GitHub Mermaid diagrams

These are three separate Mermaid blocks. Keep each opening and closing fence exactly as shown when pasting into a Markdown file such as `README.md`. The `.mmd` companion files contain only raw Mermaid source and should be opened/rendered individually, not pasted inside another Mermaid fence.

## Architecture

```mermaid
flowchart LR
  Q[Campaign borrower queue] --> P[Progressive dialer or predictive pacer]
  P -->|proposal only| S[Safety controller]
  S -->|approve or clamp| A[Atomic allocator and durable outbox]
  A -->|idempotency key: call ID| T[Provider interface]
  T --> FA[Fast reliable mock]
  T --> SN[Slow noisy mock]
  FA --> E[Authenticated event intake]
  SN --> E
  E --> D[Deduplicate and project monotonic state]
  D --> DB[(Canonical call and agent state)]
  A --> DB
```

## Agent lifecycle

```mermaid
stateDiagram-v2
  [*] --> OFFLINE
  OFFLINE --> AVAILABLE: login
  AVAILABLE --> RESERVED: atomic claim
  RESERVED --> DIALING: initiate call
  DIALING --> CONNECTED: answer on reserved slot
  DIALING --> AVAILABLE: definitive failure or cancel
  CONNECTED --> WRAP_UP: call complete
  WRAP_UP --> AVAILABLE: wrap-up elapsed
  AVAILABLE --> PAUSED: agent pause
  PAUSED --> AVAILABLE: resume
  AVAILABLE --> OFFLINE: logout
  DIALING --> DIALING: timeout; hold slot and reconcile
```

## Call lifecycle

```mermaid
stateDiagram-v2
  [*] --> QUEUED
  QUEUED --> RESERVED
  RESERVED --> INITIATED
  INITIATED --> RINGING
  RINGING --> ANSWERED
  ANSWERED --> CONNECTED
  CONNECTED --> COMPLETED
  QUEUED --> CANCELLED
  RESERVED --> FAILED
  INITIATED --> FAILED
  RINGING --> FAILED
  ANSWERED --> FAILED: reservation lost or safety stop
  INITIATED --> CANCELLED
  RINGING --> CANCELLED
  COMPLETED --> [*]
  FAILED --> [*]
  CANCELLED --> [*]
```
