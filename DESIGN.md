# 巡天御风工作台设计系统

## 1. Product and users

This is an internal operations workstation for team members, project managers, and administrators. Use an Apple-inspired light visual language: neutral surfaces, typography-led hierarchy, generous spacing, soft corners, and blue accents. The primary tasks are scanning work, finding ownership, updating a task without losing concurrent changes, and maintaining team catalogs.

## 2. Foundations

- Typeface: `-apple-system`, `BlinkMacSystemFont`, `SF Pro Text`, `Segoe UI`, `PingFang SC`, `Microsoft YaHei`, sans-serif. Use locally available system fonts without a network font dependency. Body 14px/1.6; supporting text 12–13px; section title 18px; page title 28–36px.
- The final color tokens live in `static/apple.css`, loaded after legacy styles on base, funds, and invoice templates. Both `--ux-*` and collaboration tokens map to this theme. Background `#f5f5f7`, surface white, text `#1d1d1f`, secondary text `#68686d`, primary `#0071e3`. Sidebar and top bar use translucent neutral surfaces with solid-color fallbacks.
- Spacing follows a 4px base. Normal control gaps are 12px; panel padding is 20–28px; page gutters are 16px mobile and 40px desktop. Cards use 22px corners, controls 12px, and sign-in cards 28px.
- Depth uses one quiet shadow (`--shadow`) plus borders. Dialogs use a stronger shadow and blurred scrim. Avoid decorative gradients and hover movement.

## 3. Layout and responsiveness

- Desktop (1280): fixed 248px workspace sidebar, sticky top bar, content-owned scrolling, multi-column boards.
- Tablet (768): sidebar drawer, one or two board columns, controls wrap without horizontal page overflow.
- Mobile (375): one content column, full-width primary controls, tables retain their local horizontal scroller, dialogs use the viewport with a 12px inset.
- Main controls and mobile actions are at least 44px; desktop table actions may use 36px controls and remain keyboard reachable.

## 4. Content and state

- Labels use direct Chinese nouns and verbs. Helper text explains consequences, not implementation.
- Loading, empty, error, conflict, warning, and success states are visible in the surface and announced through live regions.
- Dependency warnings never change dates automatically. Suggested dates are advisory copy beside the date controls.
- Manual vehicle-group scheduling is a separate preview/apply action: mechanical → electrical → vision, parallel within each stage, duration-preserving date shifts, and an atomic apply guarded by task and owner-membership snapshots. Keep stage choices and before/after dates visible in the preview; tables scroll locally on mobile.
- Filter counts count unique tasks. Tree context ancestors are dimmed and labelled “上下文”.

## 5. Reusable primitives

- `.btn`: default, primary, danger, ghost, small, disabled, focus-visible.
- `.panel` / `.lane` / `.task`: white cards on neutral inset lanes with restrained elevation; task has hover, keyboard focus, overdue, warning, and context states.
- `.badge`: semantic status/priority/count treatment.
- `.toolbar` / `.seg`: labelled filter groups and view switching; selected segment exposes `aria-pressed`.
- `.choice-grid`: labelled checkbox collections for people, groups, and dependencies with selected, focus, disabled, and historical/inactive states.
- `.modal`: focus-managed dialog with close, scrollable body, action footer, loading and conflict states.
- `.tree-list`: nested task rows, disclosure button, context treatment, and cycle-safe warning row.
- `.revision` and `.attachment-card`: audit difference and image evidence surfaces with explicit actions.
- `.catalog-grid`: group/project forms and item lists for management pages.

## 6. Interaction and motion

- Motion is limited to background/border/focus feedback and the sidebar transform, 200ms. Hover feedback keeps layout bounds stable. No decorative animation.
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

- Legacy styles retain structural rules; `static/apple.css` is the final shared visual layer. New visual changes belong in that layer and reuse its semantic tokens. The finance Bootstrap and invoice modal containers keep their original layout behavior.
- The task table remains horizontally scrollable on narrow screens because all columns are operationally meaningful.
- Browser screenshot and interaction QA is a separate final verification lane after backend integration; this implementation must still pass static syntax and server render checks first.
