# Phase 7 Design System

Phase 7 consolidates the previously ad-hoc component styling into a single,
tokens-driven design system. This document records the source of truth, the
component classes, and the migration rules so the styling stays consistent and
does not drift back into per-template one-offs.

## Where it lives

- **Design tokens** — `frontend/src/main.css` `:root` / `.dark` blocks (colors,
  radius, elevation) and the base `:focus-visible` / `::selection` /
  `prefers-reduced-motion` rules.
- **Component classes** — `@layer components` in the same file. Tailwind v4
  treats these as first-class utilities so templates can use `class="btn-primary"`.
- **Shared partials** — `templates/components/` for cross-cutting markup
  (messages/toasts) so a single template owns the accessible markup.

## Tokens (source of truth)

| Token | Purpose |
| --- | --- |
| `--color-background/foreground` | Page background + text |
| `--color-card/card-foreground` | Raised surfaces |
| `--color-muted/muted-foreground` | Quiet surfaces; text on them |
| `--color-primary/primary-foreground` | Brand/CTAs (flame) |
| `--color-accent/accent-foreground` | Secondary emphasis, active states |
| `--color-border/input/ring` | Borders + focus ring |
| `--color-danger/warning/success` + `*-light` | Semantic status |
| `--color-selection` | Text selection background |
| `--color-canvas/canvas-grid` | Whiteboard surface + grid |
| `--color-surface-hover` | Hover layer |
| `--shadow-1/2/3`, `--shadow-focus` | Elevation scale |
| `--radius-sm/md/lg/xl/2xl` | Corner scale |
| `--color-popover/popover-foreground` | Menus/dropdowns |

### Tokens that changed in Phase 7

- `muted-foreground` light mode darkened `#737373` → `#52525b` for a contrast
  ratio over 4.5:1 (was a marginal ~4:1).
- `--color-canvas` added so the whiteboard no longer hard-codes a dark color in
  the template (`dark:bg-[#1c1c1e]` removed).
- Semantic status backgrounds introduced so status badges/toast copy use
  `success-light` / `warning-light` / `danger-light` instead of overloaded
  accent/danger.

## Component classes

Use these in templates instead of repeating Tailwind utilities:

| Class | Use for |
| --- | --- |
| `btn-primary` | Primary call-to-action |
| `btn-outline` | Secondary action with border |
| `btn-ghost` | Quiet tertiary action |
| `btn-danger` | Destructive primary action |
| `btn-danger-outline` | Destructive secondary action |
| `btn-icon` | Square 40px icon-only button |
| `card` / `card-pad` | Surface container (+ padding) |
| `input` | Text/select input |
| `label` | Form field label |
| `field-error` | Inline field validation error |
| `badge` | Small status label |
| `avatar` | Initials circle |

> **Tailwind v4 constraint:** a component class cannot `@apply` another custom
> component class inside `@layer components` (Tailwind errors with "Cannot apply
> unknown utility class"). Each class is therefore fully expanded from primitive
> utilities. Do not reintroduce `@apply`-chaining of component classes.

## Theme source of truth

There is exactly one `dark` class source of truth:

1. `base.html` inline script (in `<head>`) reads `theme` from localStorage (or
   OS `prefers-color-scheme`) and sets/removes `.dark` on `<html>` **before
   first paint**. It also keeps `theme-color` in sync.
2. The shell's toolbar toggle flips the class + writes localStorage.

Rule: never read theme state anywhere else. Do not create a second mechanism
that toggles `.dark` (the pre-Phase-7 shell had its own duplicated Alpine
`darkMode` source that could drift).

## Accessibility rules baked in

- Every interactive control has a visible `:focus-visible` ring (`ring-ring`).
- Focus never depends on hover; the mobile drawer and account menu move focus in
  on open and return it on close (Escape).
- Destructive controls carry a red-tinted foreground; semantic text uses the
  token, not an overloaded color.
- `prefers-reduced-motion` disables decorative animation.

## Migration checklist

When touching a template:

- Prefer a component class over an inline utility cluster.
- Run `cmd /c "npx vite build"` after CSS changes and confirm the CSS artifact
  size and that no `@apply`-of-component error is raised.
- Do not add emoji/icons to text content; lucide icons ship as real DOM elements
  with `aria-hidden="true"` + an `aria-label` on the control.