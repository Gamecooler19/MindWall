/**
 * Mindwall — main.js
 *
 * Minimal, progressive-enhancement JavaScript.
 * The UI must work without this file. JS here improves UX only.
 *
 * Conventions:
 *   - No frameworks. No bundler. Plain ES2022 modules where needed.
 *   - Each feature is scoped to its own init function called at DOMContentLoaded.
 *   - No inline event handlers in templates — use data attributes.
 */

document.addEventListener('DOMContentLoaded', () => {
  initLogoutConfirm();
});

/**
 * Confirm before logging out (prevents accidental sign-out via fat-finger).
 * Skips confirmation when the button has data-no-confirm attribute.
 */
function initLogoutConfirm() {
  document.querySelectorAll('form[action="/logout"]').forEach((form) => {
    form.addEventListener('submit', (e) => {
      if (form.dataset.noConfirm !== undefined) return;
      if (!confirm('Sign out of Mindwall?')) {
        e.preventDefault();
      }
    });
  });
}
