/* Search as you type, for every search box that filters a list on the page.
 *
 * Any GET form inside #page-content that searches the page it sits on is
 * live: as each character is typed, the page is fetched in the background with
 * the form's values and everything around the form is refreshed. The form
 * itself is left alone, so the cursor stays where it is.
 *
 * A page whose scripts are wired to its filters can mark the part that
 * changes with data-live-results; then only that part is refreshed.
 *
 * Opt out with data-no-live on the form or the input. A script that builds its
 * own URL can call window.liveSearchGo(url, input).
 *
 * Loaded once in <head>; it listens on document, so it keeps working after the
 * app swaps pages in the background. */
(function () {
    if (window.liveSearchGo) return;

    var TEXT_INPUT = 'input[type="search"], input[type="text"], input:not([type])';
    var WAIT_MS = 220;
    var timer = null;
    var controller = null;

    function contentBox() {
        return document.getElementById('page-content');
    }

    function liveForm(input) {
        var form = input.form;
        if (!form || input.closest('[data-no-live], [data-patient-picker]')) return null;
        if ((form.getAttribute('method') || 'get').toLowerCase() !== 'get') return null;
        var box = contentBox();
        if (!box || !box.contains(form)) return null;
        var action = new URL(form.getAttribute('action') || window.location.href, window.location.href);
        // A search that goes to another page (e.g. "find a patient") isn't a filter for this one.
        if (action.origin !== window.location.origin || action.pathname !== window.location.pathname) return null;
        return form;
    }

    function formUrl(form) {
        var url = new URL(form.getAttribute('action') || window.location.pathname, window.location.href);
        var params = new URLSearchParams();
        new FormData(form).forEach(function (value, key) {
            if (typeof value === 'string') params.append(key, value);
        });
        params.delete('page'); // a new search starts at the first page
        url.search = params.toString();
        return url;
    }

    // Swap every part of the page except the branch that holds `keep`, which
    // stays as it is. Falls back to swapping the whole page and putting `keep`
    // back in its place when the two pages don't line up.
    function patch(oldEl, newEl, keep) {
        var oldKids = Array.prototype.slice.call(oldEl.children);
        var newKids = Array.prototype.slice.call(newEl.children);
        var branch = oldKids.find(function (kid) { return kid.contains(keep); });
        if (!branch || oldKids.length !== newKids.length) return false;
        var ok = true;
        oldKids.forEach(function (kid, i) {
            if (kid === branch) {
                if (kid !== keep && !patch(kid, newKids[i], keep)) {
                    ok = false;
                }
            } else {
                var fresh = document.importNode(newKids[i], true);
                kid.replaceWith(fresh);
                if (window.htmx) window.htmx.process(fresh);
            }
        });
        return ok;
    }

    function replaceAll(box, fresh, keep, input) {
        var start = input.selectionStart, end = input.selectionEnd;
        var copy = document.importNode(fresh, true);
        var keepSelector = keep.id ? '#' + CSS.escape(keep.id) : null;
        var twin = keepSelector ? copy.querySelector(keepSelector) : null;
        if (!twin && keep.tagName === 'FORM') {
            var index = Array.prototype.indexOf.call(box.querySelectorAll('form'), keep);
            twin = copy.querySelectorAll('form')[index] || null;
        }
        if (twin) twin.replaceWith(keep);
        box.replaceChildren.apply(box, Array.prototype.slice.call(copy.childNodes));
        if (window.htmx) window.htmx.process(box);
        if (document.contains(input)) {
            input.focus();
            try { input.setSelectionRange(start, end); } catch (e) { /* not a text field */ }
        }
    }

    async function go(url, input, keep) {
        keep = keep || input.form || input;
        if (controller) controller.abort();
        controller = new AbortController();
        var box = contentBox();
        if (!box) return;
        box.setAttribute('aria-busy', 'true');
        try {
            var response = await fetch(url, { credentials: 'same-origin', signal: controller.signal });
            var type = response.headers.get('Content-Type') || '';
            if (!response.ok || type.indexOf('text/html') === -1) return;
            if (new URL(response.url).pathname !== window.location.pathname) return; // sent elsewhere, e.g. logged out
            var page = new DOMParser().parseFromString(await response.text(), 'text/html');
            var fresh = page.getElementById('page-content');
            if (!fresh || !document.contains(keep)) return;
            var regions = box.querySelectorAll('[data-live-results]');
            var freshRegions = fresh.querySelectorAll('[data-live-results]');
            if (regions.length && regions.length === freshRegions.length) {
                regions.forEach(function (region, i) {
                    var copy = document.importNode(freshRegions[i], true);
                    region.replaceWith(copy);
                    if (window.htmx) window.htmx.process(copy);
                });
            } else if (!patch(box, fresh, keep)) {
                replaceAll(box, fresh, keep, input);
            }
            window.history.replaceState(window.history.state, '', url.pathname + url.search);
        } catch (err) {
            if (err.name !== 'AbortError') console.warn('Live search failed', err);
        } finally {
            box.removeAttribute('aria-busy');
        }
    }

    window.liveSearchGo = function (url, input) {
        return go(new URL(url, window.location.href), input);
    };

    document.addEventListener('input', function (e) {
        var input = e.target;
        if (!input.matches || !input.matches(TEXT_INPUT)) return;
        var form = liveForm(input);
        if (!form) return;
        clearTimeout(timer);
        timer = setTimeout(function () { go(formUrl(form), input, form); }, WAIT_MS);
    });

    // Pressing Enter runs the search straight away instead of reloading the page.
    document.addEventListener('submit', function (e) {
        var form = e.target;
        var input = document.activeElement;
        if (!input || input.form !== form || !input.matches(TEXT_INPUT) || !liveForm(input)) return;
        e.preventDefault();
        clearTimeout(timer);
        go(formUrl(form), input, form);
    }, true);
})();
