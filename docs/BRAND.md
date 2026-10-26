# Brand system: Founder Screener dashboard

An internal tool for upGrad, styled to upGrad's visual identity. Every value below was extracted from
upgrad.com or is derived from an extracted value for accessibility; nothing is guessed.

## 1. Extraction record

Fetched 2026-10-24 with one polite request per file and a descriptive User-Agent
(`upGradFounderScreener/0.1 (internal brand-token extraction; contact …)`).

| Source | URL |
|---|---|
| Homepage HTML | https://www.upgrad.com/ (HTTP 200; `<body data-theme="red">`) |
| Stylesheet A | https://assets.upgrad.com/3692/_next/static/css/e6a9eb4b5200f4fe.css |
| Stylesheet B (global) | https://assets.upgrad.com/3692/_next/static/css/global.8129fe26.css |
| Header logo | https://assets.upgrad.com/3692/_next/static/media/upgrad-header-logo.325f003e.svg |

Findings:
- **No `<meta name="theme-color">`** on the homepage.
- **Logo SVG** contains an embedded raster pattern (`fill="url(#pattern…)"`), not vector fills, so no
  colour could be read from it. Not needed: we do not use or recreate the logo.
- **CSS custom properties**: upGrad defines a full token set; values are RGB channel triplets used as
  `rgb(var(--x) / alpha)`.

| upGrad token | Value | Hex | Source (stylesheet B, selector) |
|---|---|---|---|
| `--branding-primary` | `#ee2c3c` | `#EE2C3C` | `:root` |
| `--branding-primary-hover` | `#f15663` | `#F15663` | `:root` |
| `--branding-cta-bg` | `linear-gradient(180deg,#ee2c3c,#da202f)` | `#EE2C3C → #DA202F` | `:root` |
| `--primary-main` | `238 44 60` | `#EE2C3C` | `:root,[data-theme=red]` |
| `--primary-shade-1` | `190 35 48` | `#BE2330` | `:root,[data-theme=red]` |
| `--primary-shade-2` | `143 26 42` | `#8F1A2A` | `:root,[data-theme=red]` |
| `--primary-tint-1` | `252 213 216` | `#FCD5D8` | `:root,[data-theme=red]` |
| `--system-fill-bg-accent-1` | `252 240 241` | `#FCF0F1` | `:root,[data-theme=red]` |
| `--greyscale-1` | `51 51 51` | `#333333` | `:root,[data-theme=red]` (`.text-greyscale-1`, 206 uses on homepage) |
| `--greyscale-2` | `102 102 102` | `#666666` | `:root,[data-theme=red]` (`.text-greyscale-2`, 396 uses on homepage) |
| `--greyscale-5` | `230 230 230` | `#E6E6E6` | `:root,[data-theme=red]` |
| `--greyscale-7` | `245 245 245` | `#F5F5F5` | `:root,[data-theme=red]` |
| `--greyscale-10` | `74 85 104` | `#4A5568` | `:root,[data-theme=red]` |
| `--system-tag-green` | `10 144 68` | `#0A9044` | `:root,[data-theme=red]` |
| `--system-tag-popular` | `196 129 29` | `#C4811D` | `:root,[data-theme=red]` |
| `--primary-main` (alt theme) | `106 17 176` | `#6A11B0` | `[data-theme=dark-purple]` |

Colour frequency across both stylesheets (pure greys/black/white ignored), top entries:
`#4A5568` ×84, `#E95454` ×65, `#EE2C3C` ×62, `#1A202C` ×51, `#B4BAC4` ×47, `#7C8698` ×46, `#091E42` ×44,
`#240746` ×39, `#8F1A24` ×16, `#DA202F` ×13. The brand red `#EE2C3C` is confirmed as the signature colour.

**Fonts.** Body and headings use **Circular XX** (`__localCircularXXTTFont`, self-hosted woff2 at
weights 400 and 500; applied via the body class), with **Merriweather** for serif accents
(`--font-merriweather`). Circular XX is a commercial typeface (Lineto) and is not freely licensed.

## 2. Design tokens

### 2.1 Colours

| Token | Hex | Derivation |
|---|---|---|
| `--brand` | `#EE2C3C` | upGrad `--branding-primary`. Accents, focus ring, active nav, score-bar fill, large text. |
| `--brand-strong` | `#DA202F` | upGrad CTA gradient end. **Primary button background** (white text passes AA; `--brand` does not). |
| `--brand-hover` | `#BE2330` | upGrad `--primary-shade-1`. Hover/pressed for brand buttons; brand-coloured text on tints. |
| `--brand-subtle` | `#FCF0F1` | upGrad `--system-fill-bg-accent-1`. Badge and highlight backgrounds. |
| `--ink` | `#333333` | upGrad `--greyscale-1`. Primary text. |
| `--ink-muted` | `#666666` | upGrad `--greyscale-2`. Secondary text, labels. |
| `--surface` | `#FFFFFF` | Cards, inputs. |
| `--surface-alt` | `#F5F5F5` | upGrad `--greyscale-7`. Page background. |
| `--border` | `#E6E6E6` | upGrad `--greyscale-5`. Decorative dividers and card borders. |
| `--border-strong` | `#8C8C8C` | Derived. Input and control borders (≥3:1 non-text contrast, WCAG 1.4.11). |
| `--success` / `--success-subtle` | `#087A3A` / `#E7F5EC` | Darkened from upGrad `--system-tag-green` `#0A9044` (4.13:1, fails AA). |
| `--warning` / `--warning-subtle` | `#9A5B00` / `#FDF3E1` | Darkened from upGrad `--system-tag-popular` `#C4811D` (3.23:1, fails AA). |
| `--danger` / `--danger-subtle` | `#C2410C` / `#FDEEE6` | Burnt orange (hue ≈ 20°), deliberately a different hue from brand red (≈ 355°). Always paired with an icon. |
| `--info` / `--info-subtle` | `#4A5568` / `#EEF1F5` | upGrad `--greyscale-10` (slate). |
| `--fit-acquire` / `--fit-acquire-subtle` | `#1D4ED8` / `#E6EDFB` | Blue. |
| `--fit-partner` / `--fit-partner-subtle` | `#0F766E` / `#E3F4F2` | Teal. |
| `--fit-cobuild` / `--fit-cobuild-subtle` | `#7E22CE` / `#F3E8FD` | Violet, in the family of upGrad's `dark-purple` theme. |

Fit-type colours are cool hues (blue, teal, violet) that sit opposite the warm brand red, so none can be
mistaken for branding or for an error. Badges always show the text label too; colour is never the only cue.

Note: upGrad's own hover (`#F15663`) is *lighter* than the base and gives white text only 3.36:1, so this
dashboard darkens on hover instead.

### 2.2 Contrast (WCAG 2.2 AA: 4.5:1 normal text, 3:1 large text and UI components)

| Foreground | Background | Ratio | Use | Result |
|---|---|---|---|---|
| `--ink` #333333 | `--surface` #FFFFFF | 12.63 | Body text | AA ✓ (AAA) |
| `--ink` #333333 | `--surface-alt` #F5F5F5 | 11.59 | Text on page bg | AA ✓ |
| `--ink` #333333 | `--brand-subtle` #FCF0F1 | 11.36 | Text on brand tint | AA ✓ |
| `--ink-muted` #666666 | `--surface` | 5.74 | Secondary text | AA ✓ |
| `--ink-muted` #666666 | `--surface-alt` | 5.27 | Secondary text on page bg | AA ✓ |
| `--ink-muted` #666666 | `--brand-subtle` | 5.16 | Secondary text on tint | AA ✓ |
| #FFFFFF | `--brand-strong` #DA202F | 4.97 | Primary button label | AA ✓ |
| #FFFFFF | `--brand-hover` #BE2330 | 6.05 | Primary button hover | AA ✓ |
| #FFFFFF | `--brand` #EE2C3C | 4.15 | Large/bold text only (≥24px, or ≥18.66px bold) | AA large ✓ / normal ✗ |
| `--brand-strong` #DA202F | `--surface` | 4.97 | Brand-coloured links | AA ✓ |
| `--brand-strong` #DA202F | `--surface-alt` | 4.56 | Links on page bg | AA ✓ |
| `--brand-hover` #BE2330 | `--brand-subtle` | 5.44 | Brand badge text | AA ✓ |
| `--brand` #EE2C3C | `--surface` | 4.15 | Focus ring, score bars, icons (non-text) | 3:1 ✓ |
| `--brand` #EE2C3C | `--surface-alt` | 3.81 | Bars/icons on page bg (non-text) | 3:1 ✓ |
| `--border-strong` #8C8C8C | `--surface` | 3.36 | Input borders | 3:1 ✓ |
| `--border-strong` #8C8C8C | `--surface-alt` | 3.08 | Input borders on page bg | 3:1 ✓ |
| `--border` #E6E6E6 | `--surface` | 1.25 | Decorative only, never the sole boundary of a control | n/a |
| `--success` #087A3A | `--surface` / `--success-subtle` / `--surface-alt` | 5.45 / 4.84 / 5.00 | Status text, badges | AA ✓ |
| `--warning` #9A5B00 | `--surface` / `--warning-subtle` / `--surface-alt` | 5.43 / 4.93 / 4.98 | Status text, badges | AA ✓ |
| `--danger` #C2410C | `--surface` / `--danger-subtle` / `--surface-alt` | 5.18 / 4.57 / 4.75 | Error text, badges | AA ✓ |
| #FFFFFF | `--danger` #C2410C | 5.18 | Destructive button | AA ✓ |
| `--info` #4A5568 | `--surface` / `--info-subtle` / `--surface-alt` | 7.53 / 6.64 / 6.90 | Info text, badges | AA ✓ |
| `--fit-acquire` #1D4ED8 | `--surface` / subtle / `--surface-alt` | 6.70 / 5.70 / 6.15 | Fit badge | AA ✓ |
| `--fit-partner` #0F766E | `--surface` / subtle / `--surface-alt` | 5.47 / 4.82 / 5.02 | Fit badge | AA ✓ |
| `--fit-cobuild` #7E22CE | `--surface` / subtle / `--surface-alt` | 6.98 / 5.91 / 6.41 | Fit badge | AA ✓ |

Ratios computed with the WCAG 2.x relative-luminance formula.

### 2.3 Typography
- **Substitution:** Circular XX is commercial, so the dashboard uses **DM Sans** (Google Fonts, SIL OFL),
  a clean geometric sans close to Circular. Loaded with `next/font/google` (self-hosted at build, no
  runtime request to Google). Merriweather is not used; the dashboard has no serif text.
- Weights: 400 (body), 500 (labels, buttons), 600 (headings).
- Scale (px / line-height): `12/16`, `14/20`, `16/24` (body), `20/28`, `24/32`, `32/40`.
- **Scores and numbers:** `font-variant-numeric: tabular-nums` on every score, rank, count and currency.
  If DM Sans does not provide the `tnum` feature, score cells fall back to `ui-monospace` digits.
  Verify when the font is wired up.

### 2.4 Shape, spacing, elevation
- 8px spacing grid (Tailwind spacing steps 2/4/6/8/12/16 = 8/16/24/32/48/64 px). Use 4px only inside dense controls.
- Radius: **10px** cards, dialogs and panels; **6px** inputs, buttons and badges; full radius for status pills.
- Shadows (subtle, adapted from upGrad's card shadows):
  `--shadow-card: 0 1px 2px rgba(9,30,66,.06), 0 4px 12px rgba(9,30,66,.06)`;
  `--shadow-pop: 0 12px 28px rgba(9,30,66,.10)`.
- Generous whitespace: 24px card padding, 32px between page sections, max content width 1280px.

### 2.5 Logo
- Do **not** recreate, trace or embed upGrad's logo.
- Wordmark: **"Founder Screener"** in DM Sans 600, `--ink`, with a small **"for upGrad"** label in
  12px 500 `--ink-muted`; a 4px `--brand` bar to the left of the wordmark is the only brand mark.
- If an official logo file is added at `web/public/brand/`, use it exactly as provided (no recolouring,
  stretching or effects) and keep the text wordmark beside it.

### 2.6 Tone of UI copy
Crisp, executive, specific. Sentence case. Numbers over adjectives ("12 verified claims", not "strong
evidence"). No hype words (revolutionary, game-changing, cutting-edge, amazing, unlock, supercharge).
Empty and error states say what happened and what to do next.

## 3. Implementation (`web/app/globals.css`)

Components use tokens only. **No raw hex values in components**; lint for it. Tailwind v4 maps tokens
with `@theme inline`, so classes such as `bg-brand-strong`, `text-ink-muted`, `border-border`,
`bg-fit-acquire-subtle`, `rounded-card`, `shadow-card` are available. This block goes into
`web/app/globals.css` when `/web` is scaffolded:

```css
@import "tailwindcss";

:root {
  --brand: #EE2C3C;
  --brand-strong: #DA202F;
  --brand-hover: #BE2330;
  --brand-subtle: #FCF0F1;

  --ink: #333333;
  --ink-muted: #666666;
  --surface: #FFFFFF;
  --surface-alt: #F5F5F5;
  --border: #E6E6E6;
  --border-strong: #8C8C8C;

  --success: #087A3A;  --success-subtle: #E7F5EC;
  --warning: #9A5B00;  --warning-subtle: #FDF3E1;
  --danger:  #C2410C;  --danger-subtle:  #FDEEE6;
  --info:    #4A5568;  --info-subtle:    #EEF1F5;

  --fit-acquire: #1D4ED8;  --fit-acquire-subtle: #E6EDFB;
  --fit-partner: #0F766E;  --fit-partner-subtle: #E3F4F2;
  --fit-cobuild: #7E22CE;  --fit-cobuild-subtle: #F3E8FD;
}

/* Static theme values: defined once here (not in :root) to avoid self-referencing variables. */
@theme {
  --radius-card: 10px;
  --radius-control: 6px;
  --shadow-card: 0 1px 2px rgba(9, 30, 66, .06), 0 4px 12px rgba(9, 30, 66, .06);
  --shadow-pop: 0 12px 28px rgba(9, 30, 66, .10);
}

@theme inline {
  --color-brand: var(--brand);
  --color-brand-strong: var(--brand-strong);
  --color-brand-hover: var(--brand-hover);
  --color-brand-subtle: var(--brand-subtle);
  --color-ink: var(--ink);
  --color-ink-muted: var(--ink-muted);
  --color-surface: var(--surface);
  --color-surface-alt: var(--surface-alt);
  --color-border: var(--border);
  --color-border-strong: var(--border-strong);
  --color-success: var(--success);
  --color-success-subtle: var(--success-subtle);
  --color-warning: var(--warning);
  --color-warning-subtle: var(--warning-subtle);
  --color-danger: var(--danger);
  --color-danger-subtle: var(--danger-subtle);
  --color-info: var(--info);
  --color-info-subtle: var(--info-subtle);
  --color-fit-acquire: var(--fit-acquire);
  --color-fit-acquire-subtle: var(--fit-acquire-subtle);
  --color-fit-partner: var(--fit-partner);
  --color-fit-partner-subtle: var(--fit-partner-subtle);
  --color-fit-cobuild: var(--fit-cobuild);
  --color-fit-cobuild-subtle: var(--fit-cobuild-subtle);

  --font-sans: var(--font-dm-sans), ui-sans-serif, system-ui, sans-serif;

  --text-xs: 12px;   --text-xs--line-height: 16px;
  --text-sm: 14px;   --text-sm--line-height: 20px;
  --text-base: 16px; --text-base--line-height: 24px;
  --text-lg: 20px;   --text-lg--line-height: 28px;
  --text-xl: 24px;   --text-xl--line-height: 32px;
  --text-2xl: 32px;  --text-2xl--line-height: 40px;
}

body { background: var(--surface-alt); color: var(--ink); }
.tabular { font-variant-numeric: tabular-nums; }
:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
```

shadcn/ui's own variables (`--primary`, `--background`, `--ring`, …) are mapped onto these tokens
(`--primary: var(--brand-strong)`, `--ring: var(--brand)`, `--background: var(--surface-alt)`,
`--destructive: var(--danger)`), never to raw values.

Light theme only for v1. A dark theme, if added, redefines the same variables under `[data-theme="dark"]`
and must re-pass every row of §2.2.
