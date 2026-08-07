# GroundedOps — internal 10.14.1 (HOTFIX: white screen)

GO_v2.0 line. Single file: src/frontend/src/App.jsx. Rebuild frontend:
docker compose up -d --build frontend

## Fix
10.14.0 imported HelpIcon from icons.jsx for the FAQ handle, but your
deployed icons.jsx doesn't export HelpIcon — the failed ES module import
threw at load and blanked the whole app (white screen; console:
"does not provide an export named 'HelpIcon'").

Fixed by removing the HelpIcon import and inlining the help icon as an
SVG directly in App.jsx. It uses stroke="currentColor", so it still
follows the dark/light theme via the .faq-handle CSS (accent colour,
inverts on hover) — same themed icon, zero dependency on icons.jsx.

Everything else in 10.14.0 stands. This only replaces App.jsx.

## Verify
Rebuild frontend, reload — app renders (no white screen), FAQ icon shows
on the right edge and follows your theme.
