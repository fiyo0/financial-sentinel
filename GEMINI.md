# Pragmatic Engineering Guidelines

## 1. Transparent Fallbacks on Core Services
- Fallbacks and degraded modes are encouraged for system resilience, but they should never operate as "ghost fallbacks" (silently masking broken infrastructure).
- When a **primary external service, data provider, or AI model** fails and drops to a secondary fallback (e.g. regex, stale cache, heuristic approximation, or default dataset):
  - Log the event with the root-cause failure reason.
  - Surface an explicit indicator (e.g. UI badge, status tag, or response field like `classifier_mode="REGEX_FALLBACK"`) so operators and downstream systems know the secondary path is active.
  - In chat conversations, proactively give a quick 1-sentence heads-up: *"Primary service [X] failed ([Reason]); gracefully running on [Fallback Y]."*
- *(Do NOT apply this to trivial internal code defaults, dictionary lookups, or standard utility functions—this strictly governs major external services and feature engines).*

## 2. Sanity-Check Live Endpoints
- Passing unit tests with mocks prove internal *logic flow*, but they do NOT guarantee that an external model name, third-party API, or cloud credential actually exists or works in reality.
- Whenever configuring, renaming, or upgrading an external model, third-party API endpoint, or remote service, run a quick live sanity probe (or un-mocked verification check) to confirm the resource is active and responsive before declaring the task verified.
