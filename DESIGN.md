# 巡天御风工作台设计系统

## 1. Product and users

This is a dense internal operations workstation for team members, project managers, and administrators. Preserve the current calm navy/blue visual language. The primary tasks are scanning work, finding ownership, updating a task without losing concurrent changes, and maintaining team catalogs.

## 2. Foundations

- Typeface: system UI, `Segoe UI`, `Microsoft YaHei`, sans-serif. Body 14px/1.5; supporting text 12–13px; section title 16–17px; page title 23–31px.
- Color tokens live in `static/style.css`: `--bg`, `--card`, `--text`, `--muted`, `--border`, `--primary`, semantic blue/green/red/orange/gray/dark. Workspace chrome uses navy `#15263f` and existing pale-blue surfaces.
- Spacing follows a 4px base. Normal control gaps are 8–12px; panel padding is 16–24px; page gutters are 16px mobile and 32px desktop.
- Depth uses one quiet shadow (`--shadow`) plus borders. Dialogs use the existing stronger modal shadow.

## 3. Layout and responsiveness

- Desktop (1280): fixed 242px workspace sidebar, sticky top bar, content-owned scrolling, multi-column boards.
- Tablet (768): sidebar drawer, one or two board columns, controls wrap without horizontal page overflow.
- Mobile (375): one content column, full-width primary controls, tables retain their local horizontal scroller, dialogs use the viewport with a 12px inset.
- Touch targets are at least 38px; dense table actions remain keyboard reachable.

## 4. Content and state

- Labels use direct Chinese nouns and verbs. Helper text explains consequences, not implementation.
- Loading, empty, error, conflict, warning, and success states are visible in the surface and announced through live regions.
- Dependency warnings never change dates automatically. Suggested dates are advisory copy beside the date controls.
- Filter counts count unique tasks. Tree context ancestors are dimmed and labelled “上下文”.

## 5. Reusable primitives

- `.btn`: default, primary, danger, ghost, small, disabled, focus-visible.
- `.panel` / `.lane` / `.task`: bordered white surfaces with existing restrained elevation; task has hover, keyboard focus, overdue, warning, and context states.
- `.badge`: semantic status/priority/count treatment.
- `.toolbar` / `.seg`: labelled filter groups and view switching; selected segment exposes `aria-pressed`.
- `.choice-grid`: labelled checkbox collections for people, groups, and dependencies with selected, focus, disabled, and historical/inactive states.
- `.modal`: focus-managed dialog with close, scrollable body, action footer, loading and conflict states.
- `.tree-list`: nested task rows, disclosure button, context treatment, and cycle-safe warning row.
- `.revision` and `.attachment-card`: audit difference and image evidence surfaces with explicit actions.
- `.catalog-grid`: group/project forms and item lists for management pages.

## 6. Interaction and motion

- Motion is limited to existing hover/focus feedback and the sidebar transform, 150–200ms. No decorative animation.
- Dialog opening moves focus inside; Tab is trapped; Escape closes; focus returns to the opener.
- Tree expansion is explicit and preserved across polling in memory.
- Upload is a deliberate submit action with one request, disabled controls while pending, and no retry loop.
- `prefers-reduced-motion` disables transitions.

## 7. Accessibility constraints

- Every search/select/checkbox group has a visible label or accessible name.
- Color is paired with text. Context ancestors, inactive people, errors, warnings, and permissions are stated in words.
- Multiple selection uses native checkboxes in fieldsets, not an opaque custom listbox.
- Images have descriptive alt text and open through keyboard-operable links.
- Dynamic status uses `role="status"`; blocking failures use `role="alert"`.
- Avoid inline event handlers and escape all untrusted strings before HTML insertion.

## 8. Accepted debt and handoff

- Legacy pages still contain raw colors predating this contract; collaboration additions reuse existing tokens and introduce no new visual direction.
- The task table remains horizontally scrollable on narrow screens because all columns are operationally meaningful.
- Browser screenshot and interaction QA is a separate final verification lane after backend integration; this implementation must still pass static syntax and server render checks first.
