// Password inputs marked data-password-field get:
// - a brief flash of the letter just typed (then it's masked again), and
// - an eye button to show / hide the whole password.
// Security: the field stays type="password" while typing; showing it turns off
// spellcheck/autocorrect (so the text isn't sent to a spelling service) and it
// hides itself again after 15 seconds, when the tab is hidden, and on submit.
(function () {
  const FLASH_MS = 800;
  const AUTO_HIDE_MS = 15000;
  const EYE = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M2.04 12.32a1 1 0 0 1 0-.64C3.42 7.51 7.36 4.5 12 4.5c4.64 0 8.57 3 9.96 7.18a1 1 0 0 1 0 .64C20.58 16.49 16.64 19.5 12 19.5c-4.64 0-8.57-3-9.96-7.18Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.22A10.48 10.48 0 0 0 2.04 12.32c1.38 4.17 5.32 7.18 9.96 7.18 1.69 0 3.29-.4 4.7-1.11M6.23 6.23A10.45 10.45 0 0 1 12 4.5c4.64 0 8.57 3 9.96 7.18a10.5 10.5 0 0 1-4.19 5.55M6.23 6.23 3 3m3.23 3.23 3.65 3.65m7.89 7.89L21 21m-3.23-3.23-3.65-3.65m0 0a3 3 0 1 0-4.24-4.24m4.24 4.24L9.88 9.88"/></svg>';

  function injectStyles() {
    if (document.getElementById('pw-field-styles')) return;
    const style = document.createElement('style');
    style.id = 'pw-field-styles';
    style.textContent =
      '.pw-wrap{position:relative;display:block}' +
      '.pw-wrap>input{padding-right:2.75rem!important;margin-bottom:0!important}' +
      '.pw-wrap>input.pw-flashing{color:transparent!important;-webkit-text-fill-color:transparent}' +
      '.pw-overlay{position:absolute;top:0;bottom:0;left:0;right:2.75rem;display:flex;align-items:center;' +
      'overflow:hidden;white-space:pre;pointer-events:none;visibility:hidden}' +
      '.pw-overlay.pw-on{visibility:visible}' +
      '.pw-eye{position:absolute;top:50%;right:.5rem;transform:translateY(-50%);width:2rem;height:2rem;padding:0!important;' +
      'display:flex;align-items:center;justify-content:center;border:0!important;border-radius:.4rem;' +
      'background:transparent!important;color:#6b7280!important;cursor:pointer}' +
      '.pw-eye:hover{color:#111827!important}' +
      '.pw-eye:focus-visible{outline:2px solid #2f4cff;outline-offset:1px}';
    document.head.appendChild(style);
  }

  function enhance(input) {
    if (input.dataset.pwReady) return;
    input.dataset.pwReady = '1';
    input.setAttribute('spellcheck', 'false');
    input.setAttribute('autocapitalize', 'off');
    input.setAttribute('autocorrect', 'off');

    const wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    const blockMargin = getComputedStyle(input).marginBottom;
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    wrap.style.marginBottom = blockMargin;

    // The overlay mirrors the masked text with the last letter visible.
    const overlay = document.createElement('span');
    overlay.className = 'pw-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    wrap.appendChild(overlay);

    const eye = document.createElement('button');
    eye.type = 'button';
    eye.className = 'pw-eye';
    eye.setAttribute('aria-controls', input.id);
    wrap.appendChild(eye);

    let flashTimer = null;
    let hideTimer = null;

    function endFlash() {
      clearTimeout(flashTimer);
      flashTimer = null;
      overlay.textContent = '';
      overlay.classList.remove('pw-on');
      input.classList.remove('pw-flashing');
    }

    function setShown(shown) {
      endFlash();
      clearTimeout(hideTimer);
      input.type = shown ? 'text' : 'password';
      eye.innerHTML = shown ? EYE_OFF : EYE;
      eye.setAttribute('aria-pressed', shown ? 'true' : 'false');
      eye.setAttribute('aria-label', shown ? 'Hide password' : 'Show password');
      eye.title = shown ? 'Hide password' : 'Show password';
      if (shown) hideTimer = setTimeout(function () { setShown(false); }, AUTO_HIDE_MS);
    }

    function flash(letter) {
      const cs = getComputedStyle(input);
      overlay.style.font = cs.font;
      overlay.style.letterSpacing = cs.letterSpacing;
      overlay.style.color = cs.color;
      overlay.style.paddingLeft = (parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth)) + 'px';
      overlay.textContent = '•'.repeat(input.value.length - 1) + letter;
      overlay.style.justifyContent = input.scrollWidth > input.clientWidth ? 'flex-end' : 'flex-start';
      overlay.classList.add('pw-on');
      input.classList.add('pw-flashing');
      clearTimeout(flashTimer);
      flashTimer = setTimeout(endFlash, FLASH_MS);
    }

    input.addEventListener('input', function (event) {
      if (input.type !== 'password') return;
      const typedOneAtEnd = event.inputType === 'insertText' && event.data && event.data.length === 1
        && input.selectionStart === input.value.length;
      // Pastes, deletions and edits mid-password never reveal anything.
      if (typedOneAtEnd) flash(event.data); else endFlash();
    });
    input.addEventListener('blur', endFlash);
    eye.addEventListener('click', function () {
      setShown(input.type === 'password');
      input.focus();
    });
    // Mouse down on the eye mustn't steal focus (keeps the caret in place).
    eye.addEventListener('mousedown', function (event) { event.preventDefault(); });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) setShown(false);
    });
    window.addEventListener('pagehide', function () { setShown(false); });
    if (input.form) input.form.addEventListener('submit', function () { setShown(false); });

    setShown(false);
  }

  function init() {
    const fields = document.querySelectorAll('input[data-password-field]');
    if (!fields.length) return;
    injectStyles();
    fields.forEach(enhance);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
