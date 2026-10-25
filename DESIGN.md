# 巡天御风工作台设计系统

## 1. Product and users

This is an internal operations workstation for team members, project managers, and administrators. Use the eight approved L1–L4 / D1–D4 palettes with a sidebar and project timeline, synchronized per account. The primary tasks are scanning work, finding ownership, updating a task without losing concurrent changes, and maintaining team catalogs.

## 2. Foundations

- Typeface: `-apple-system`, `BlinkMacSystemFont`, `SF Pro Text`, `Segoe UI`, `PingFang SC`, `Microsoft YaHei`, sans-serif. Use locally available system fonts without a network font dependency. Body 14px/1.6; supporting text 12–13px; section title 18px; page title 28–36px.
- The final palette tokens live in `static/themes.css`, loaded after `static/apple.css` on base, funds, and invoice templates. Both `--ux-*` and collaboration tokens map to this theme. Background `#f5f5f7`, surface white, text `#1d1d1f`, secondary text `#68686d`, primary `#0071e3`. Sidebar and top bar use translucent neutral surfaces with solid-color fallbacks.
- The black theme uses background `#0b0b0c`, surface `#1c1c1e`, text `#f5f5f7`, secondary text `#a1a1a6`, and accent `#0a84ff`. Status surfaces, control boundaries and focus states have separate dark values. Bright blue primary buttons use dark text for contrast in this theme.
- Spacing follows a 4px base. Normal control gaps are 12px; panel padding is 20–28px; page gutters are 16px mobile and 40px desktop. Cards use 16px corners and controls 10px.
- Depth uses one quiet shadow (`--shadow`) plus borders. Dialogs use a stronger shadow and blurred scrim. Use restrained, static gradients only for the approved RM armor and soft pink glass themes; avoid hover movement.

## 3. Layout and responsiveness

- Desktop (1280): fixed 248px workspace sidebar, sticky top bar, content-owned scrolling, multi-column boards.
- Tablet (768): sidebar drawer, one or two board columns, controls wrap without horizontal page overflow.
- Mobile (375): one content column, full-width primary controls, tables retain their local horizontal scroller, dialogs use the viewport with a 12px inset.
- Main controls and mobile actions are at least 44px; desktop table actions may use 36px controls and remain keyboard reachable.
- Selecting one vehicle group defaults to its specialty timeline: mechanical → electrical → vision, then unclassified. Each task has its own row. Coordinates use the complete group's valid dates and stay stable under row filtering. Same-day tasks have a marker; incomplete/invalid dates have text instead of invented bars. Under 768px, use vertical stage lists with full dates. Management retains its table.

## 4. Content and state

- Labels use direct Chinese nouns and verbs. Helper text explains consequences, not implementation.
- Loading, empty, error, conflict, warning, and success states are visible in the surface and announced through live regions.
- New unsaved tasks default to the main predecessor deadline minus two calendar days. Manual edits stop draft substitution; first save freezes dates. An explicit reset copies the current suggestion once. Stored `start_date_origin` records provenance, never enables cascading writes. Other predecessors do not drive this default.
- Manual vehicle-group scheduling is a separate preview/apply action: mechanical → electrical → vision, parallel within each stage, duration-preserving date shifts, and an atomic apply guarded by task and owner-membership snapshots. Keep stage choices and before/after dates visible in the preview; tables scroll locally on mobile.
- Filter counts count unique tasks. Tree context ancestors are dimmed and labelled “上下文”.

## 5. Reusable primitives

- `.btn`: default, primary, danger, ghost, small, disabled, focus-visible.
- `.panel` / `.lane` / `.task`: white cards on neutral inset lanes with restrained elevation; task has hover, keyboard focus, overdue, warning, and context states.
- Task card backgrounds follow the current theme. Blocked task cards and specialty timeline rows have a complete red frame, alongside their written status. Invoice secondary actions use the theme surface and readable text; accent and warning actions retain semantic colors.
- Member creation groups name/username, password/role, specialty/vehicle choices, then submit. Desktop identity fields use two aligned columns; each choice section spans the form. Single-member and batch-import forms have separate headings and stack under 1150px.
- Task creation places description and submit together beside prerequisites, with both columns aligned at the bottom; narrow screens stack these sections. Finance plots use transparent PNG surfaces over the page background, with theme-aware labels, axes and legends.
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
- Account → Appearance and the sidebar user-row shortcut share the same theme control logic. Topbar and account settings offer all eight palettes plus light/dark/system mode; light and dark selections are remembered separately. Selection applies immediately and persists through `/api/account/preferences`; failure restores the full original preference and offers explicit retry. The sidebar toggle switches to the remembered opposite palette and exits system mode. The sidebar uses a 44px sun/moon button beside, not inside, the account link. Server-rendered theme attributes and synchronous `theme-init.js` resolve system mode before stylesheet paint. Other devices receive the choice on their next page load. Anonymous login uses silver; initial-password accounts must change their password before saving preferences.
- Account appearance sits below the personal information card in the left column. The person board includes administrator lanes when they own or participate in work; unassigned tasks occupy the first full-width row below filters, use horizontal cards and disappear when there are no matching unassigned tasks.

## 7. Accessibility constraints

- Every search/select/checkbox group has a visible label or accessible name.
- Color is paired with text. Context ancestors, inactive people, errors, warnings, and permissions are stated in words.
- Multiple selection uses native checkboxes in fieldsets, not an opaque custom listbox.
- Images have descriptive alt text and open through keyboard-operable links.
- Dynamic status uses `role="status"`; blocking failures use `role="alert"`.
- Business requests have bounded waiting time. Disable repeat submission while pending, restore controls on failure, and retain editable inputs. A failed optimistic theme/status change restores the prior display. Uncertain writes after a connection loss require checking the saved result before retrying; never automatically replay them. Session expiry offers a separate login tab so the current draft stays open. Failed reads must not render successful empty states.
- Avoid inline event handlers and escape all untrusted strings before HTML insertion.

## 8. Accepted debt and handoff

- Daily projects appear under the task center’s independent daily tab, with one card per shared goal; their tables, APIs, ownership and histories remain separate. Old daily URLs redirect to canonical task-center URLs. Explicit group ownership controls editing; mechanical, electrical and vision panels retain independent drafts. Mobile panels stack vertically. The dashboard daily-project summary is separate from vehicle-task metrics. Email configuration appears in account settings; delivery state is stated in text, and SMTP acceptance must never be labelled inbox delivery.
- Task-center person lanes initially show one task, with a bottom-right button to reveal remaining tasks. Expansion survives refresh polling in page memory, independently of tree expansion. Compact cards show a single-line note only when nonempty, using the latest note author and content; cards without notes omit the note section, and full history and screenshots remain in details. Authorized administrators/managers create tasks from the task center; the management page retains catalogs, scheduling and existing-task operations. All active members, including administrators, may own or participate in vehicle tasks; inactive members cannot receive new assignments.
- Collaboration is a local topic board. Desktop uses list/conversation columns; below 768px use list/detail navigation with an explicit back button. Retain the eight theme tokens, 44px touch targets, safe text rendering, inline file previews and upload progress. Preserve per-topic drafts on failure and switching; uncertain submissions are reconciled by ID before retry. Polling must preserve reading position and keyboard focus; only actually visible messages advance read position. Withdrawals use tombstones and never expose originals through the UI.

- Legacy styles retain structural rules; `static/apple.css` retains shared component styling; `static/themes.css` is the final palette/decorative layer and reuses the semantic tokens. Finance retains Bootstrap 5.3.3, served locally through a resource whitelist. Invoice dialogs use native dialog focus and keyboard behavior.
- The task table remains horizontally scrollable on narrow screens because all columns are operationally meaningful.
- Browser screenshot and interaction QA is a separate final verification lane after backend integration; this implementation must still pass static syntax and server render checks first.


## 9. Approved theme collection (2026-10-16)

| Mode | ID | Name | Visual identity |
| --- | --- | --- | --- |
| Light | silver | Mistral 暖橙 | Warm ivory surfaces, orange actions |
| Light | rm-light | RM 机甲红蓝 | Broad red sidebar, blue banner, light metal cards, armor seams/rivets |
| Light | mist | 云雾绿 | Mist-white background, restrained green accent |
| Light | paw | 奶霜猫爪粉 | Cream/blush surfaces, pink controls, furry cat-paw details |
| Dark | space | GitHub 暗黑 | Black surfaces, restrained neutral borders |
| Dark | rm-dark | 黑白极简 | Black and white minimal surfaces |
| Dark | graphite | 石墨灰金 | Graphite surfaces, warm gold accent, no forest green |
| Dark | xueli | 雪梨星语 | Translucent dark rose, pink controls, white-haired Xueli mascots, blue/mint eyes and star details |

Decorations are noninteractive and `aria-hidden`, with reserved layout space. The image atlas is local in `static/theme-assets`, loaded only for the two illustrated palettes. Keep labels, numbers, navigation and actions clear. Main text, secondary text and primary-button labels must meet 4.5:1 contrast. Danger/success retain semantic tokens. Finance plots refresh their resolved palette without letting older requests overwrite them.

The homepage countdown is a single admin-configured target, not a task. Calculate by Beijing calendar date; today and elapsed dates have explicit neutral wording. Clear both title and date to hide. Conflicts retain input and require explicit latest-setting reload. The target updates on minute/visibility events without overwriting an open draft.

## 10. Review repairs (2026-10-22)

Bootstrap styles, scripts, icons and licenses are local and checksum-verified against the client vendor lock; clients keep dist/vendor without duplicate assets. Invoice dialogs have labelled headings, named close controls, Escape handling and focus return. Mobile finance and invoice controls have 44px touch targets. Hidden fields use the HTML hidden contract. Xueli uses a local WebP atlas with the original PNG fallback; palette IDs remain stable. Funds pagination, sidebar positioning, task-modal layout redesign and asset versioning remain deferred (C2).
