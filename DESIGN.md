---
version: alpha
name: candlebench
description: A dense local research dashboard for examining candlestick evidence and independent validation.
colors:
  bg: "#11131a"
  panel: "#181b24"
  panel-2: "#1e222d"
  line: "#2a2f3c"
  text: "#e4e7ee"
  muted: "#8b93a7"
  accent: "#6aa9ff"
  edge: "#46d18a"
  noise: "#8b93a7"
  negative: "#e2685f"
  insufficient: "#5a6176"
typography:
  body:
    fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "15px"
    lineHeight: "1.5"
  mono:
    fontFamily: 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace'
rounded:
  panel: "10px"
  button: "7px"
  input: "6px"
  group: "8px"
  verdict: "4px"
  chip: "999px"
spacing:
  page-max: "1180px"
  page-inline: "1.5rem"
  page-top: "2rem"
  page-bottom: "4rem"
  panel-gap: "1.1rem"
omitted:
  - section: components
    reason: Existing shared CSS recipes remain canonical; component mapping and states are documented below.
---

# candlebench design context

## Overview

The reference is a quantitative research workbench: a compact parameter panel,
sortable evidence tables, and annotated plots. The signature is the connection
between a pattern's aggregate evidence and the actual trades behind it.
Retain the established dark surfaces, blue actions, and monospaced numbers.
Evidence clarity takes priority over decoration.

This is a product dashboard for people investigating intraday candlestick
patterns using cached US equity sessions. The dashboard and local-server scope
are described in [README.md](README.md).
The current document language is English (`index.html` uses `lang="en"`).
No separate Japan-market or translated-interface requirements are established.
Desktop supports dense inspection; narrow layouts must retain access to the
same evidence through wrapping controls and horizontally scrolling tables.

Runtime ownership is **Model B**: [app.css](candlebench/web/static/app.css) owns
the values. This file mirrors accepted values and explains their use; it does
not generate CSS. [index.html](candlebench/web/static/index.html) and
[app.js](candlebench/web/static/app.js) consume the shared styles. No theme
adapter, framework component library, or token export pipeline exists.
When a durable value changes, update its CSS owner and this mirror together.

## Colors

`bg`, `panel`, and `panel-2` separate the document, grouped content, and nested
statistics. `line` supplies quiet borders; `text` and `muted` establish text
hierarchy. `accent` identifies primary actions, selected patterns, and the
pattern's equity line. The control uses a muted dashed line.

Verdicts use `edge`, `noise`, `negative`, and `insufficient` with their explicit
text labels. Missing evidence is unavailable, not a measured zero. A green
badge is reserved for confirmed EDGE under the current inference contract.
Discovery candidates, unsuccessful validation, disabled validation, and legacy
unvalidated results must remain distinguishable in text.

The existing theme is dark only. Browser high-contrast behavior must remain
operable. This document records the current palette; it does not certify its
contrast. In particular, small insufficient/unavailable text needs contrast
verification rather than assuming a subdued color is readable.

## Typography

Body copy uses the system stack at 15px with 1.5 line height. Numerical fields,
tables, chart labels, and run identifiers use `--mono`. The main heading is
1.5rem; section headings are 0.95rem with restrained uppercase styling.
Tables use 0.82rem text and 0.72rem headers. Keep explanations in normal prose;
use explicit R units, sample counts, session counts, and percentage labels.
No web fonts or decorative icon family are introduced.

## Layout

The document is centered at a maximum width of 1180px, with 2rem top, 1.5rem
inline, and 4rem bottom padding. Panels retain natural document height and
1.1rem vertical separation. Parameter controls use an auto-fitting grid with
150px minimum columns; actions, chips, and tabs wrap. Charts use their existing
responsive SVG view boxes; avoid introducing an oversized minimum chart width
that causes document overflow on phones.

Each `.table-wrap` owns horizontal overflow. The drilldown trade table also
owns vertical overflow with a 26rem maximum height. Do not constrain the
document or parameter panel to a fixed viewport height to size a table.
The leaderboard is bounded by the configured report rows; individual trades
use server pagination and visible Previous/Next controls.

## Elevation & Depth

Hierarchy comes from tonal surfaces and one-pixel borders. Static panels have
no drop shadow. The selected row's inset blue marker is a selection cue.
Keep validation evidence within the existing summary, leaderboard, and
drilldown surfaces instead of adding floating or modal containers.

## Shapes

Panels use 10px corners, buttons 7px, inputs 6px, groups/statistics 8px, and
verdict badges 4px. Pills belong to pattern/timeframe chips and progress bars.
Retain these component-specific values rather than imposing a new radius scale.

## Components

### Canonical ownership and runtime mapping

| Capability or token | Runtime owner and consumers |
|---|---|
| `colors.*` | Corresponding `:root --*` variables in `app.css`; panels, controls, verdicts, SVG plots |
| `typography.body` / `typography.mono` | `body` declaration / `--mono`; prose versus data and chart labels |
| `rounded.*` / `spacing.*` | Existing component selectors / `body` and `.panel`; values above mirror those declarations |
| Select/Listbox | Native HTML `<select>` in `.controls`; browser owns popup geometry and keyboard behavior |
| Parameters | Labeled numeric inputs and shared config parsing/submission in `app.js`; API validates values |
| Status and errors | Shared `#banner`, `.banner`, and `#progress`; persistent explanation and retry through the owning action |
| Tables and row inspection | Shared leaderboard/trade rendering and `.table-wrap`; bounded report versus paged trade variants |
| Scrollbars | Browser-native scrolling currently; a global tokenized baseline is not established |

Native selects are an intentional reuse choice for this existing local tool;
there is no authored popup to copy. Dataset row inspection is distinct from
bulk selection, which is absent. Date-picking, CRUD, deletion, authentication,
and toast/modal systems are outside the present dashboard workflow.

### Visual states and evidence

Buttons use shared borders and an accent hover border; the primary Run action
uses a solid accent surface. Disabled actions dim and stop responding. Chips
and tabs express selection through `aria-pressed`/`aria-selected` and shared
surface variants. Focus, busy, error, and keyboard behavior must be verified
in the runtime; existing declarations alone are not proof of compliance.

Use the existing progress bar for measurable run/fetch work. Its 0.3s width
transition conveys progress; reduced-motion behavior belongs in the shared
stylesheet. Loading and retry copy should preserve the user's configuration.
No skeleton, ornamental animation, or new overlay is needed for evidence work.

Display date-clustered pointwise 95% intervals separately from corrected
significance. Pair the pattern-minus-control interval with its control and
session count. Show discovery and validation explicitly; drilldowns begin
with discovery and allow an explicit sample change. Keep the original discovery
verdict visible when final confirmation differs. Warn about repeated holdout
inspection, and label zero-holdout runs exploratory.

The equity plot includes starting equity zero. Its drawdown shade must span
the same peak and trough used by the server, including an initial losing trade.
An unavailable value remains an unavailable label in tables, summaries, and
comparisons. Statistical tables provide the numerical alternative to plots.

## Do's and Don'ts

- **Do:** extend shared styles and rendering helpers for new evidence fields.
- **Do:** keep control direction, sample phase, and unavailable evidence explicit.
- **Do:** test sorting, paging, retry, keyboard access, and narrow layouts together.
- **Don't:** turn an indicator score or exploratory candidate into a success probability.
- **Don't:** replace this research workbench with a marketing hero, decorative charts,
  or a new visual identity for one feature.
- **Don't:** describe the static premium audit as proof of browser accessibility.
