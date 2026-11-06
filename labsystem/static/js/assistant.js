// Ternah Assistant chat panel. Answers come from the server as plain data and
// are written with textContent only, so nothing in a record can run as code.
(function () {
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function init(root) {
        if (root.dataset.ready) return;
        root.dataset.ready = '1';
        const fab = root.querySelector('.ta-fab');
        const panel = root.querySelector('.ta-panel');
        const log = root.querySelector('.ta-log');
        const form = root.querySelector('.ta-form');
        const input = form.querySelector('input');
        let greeted = false;
        let busy = false;

        function scrollDown() { log.scrollTop = log.scrollHeight; }

        function chips(items, into) {
            if (!items || !items.length) return;
            const row = el('div', 'ta-chips');
            items.forEach(function (label) {
                const chip = el('button', '', label);
                chip.type = 'button';
                chip.addEventListener('click', function () { send(label); });
                row.appendChild(chip);
            });
            into.appendChild(row);
        }

        function showAnswer(data) {
            const bubble = el('div', 'ta-msg ta-bot');
            if (data.understood) bubble.appendChild(el('span', 'ta-understood', data.understood));
            bubble.appendChild(el('p', '', data.text || ''));
            if (data.facts && data.facts.length) {
                const table = el('table', 'ta-facts');
                data.facts.forEach(function (pair) {
                    const tr = el('tr');
                    tr.appendChild(el('td', '', pair[0]));
                    tr.appendChild(el('td', '', pair[1]));
                    table.appendChild(tr);
                });
                bubble.appendChild(table);
            }
            if (data.link && data.link.url && data.link.url.charAt(0) === '/') {
                const a = el('a', 'ta-link', data.link.label + ' →');
                a.href = data.link.url;
                bubble.appendChild(a);
            }
            chips(data.clarify, bubble);
            chips(data.suggestions, bubble);
            log.appendChild(bubble);
            scrollDown();
        }

        function send(text) {
            if (busy) return;
            busy = true;
            if (text) {
                log.appendChild(el('div', 'ta-msg ta-me', text));
            }
            const typing = el('div', 'ta-msg ta-bot ta-typing', 'Looking it up…');
            log.appendChild(typing);
            scrollDown();
            fetch(root.dataset.url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': root.dataset.csrf },
                body: JSON.stringify({ q: text || '' }),
            }).then(function (response) {
                if (response.status === 403 || response.redirected) throw new Error('signed-out');
                return response.json();
            }).then(showAnswer).catch(function (error) {
                showAnswer({ text: error.message === 'signed-out' ? 'You have been signed out. Refresh the page to sign in again.' : 'I could not reach the server. Check your connection and try again.' });
            }).finally(function () {
                typing.remove();
                busy = false;
            });
        }

        function setOpen(open) {
            panel.hidden = !open;
            fab.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                if (!greeted) { greeted = true; send(''); }
                input.focus();
            } else {
                fab.focus();
            }
        }

        fab.addEventListener('click', function () { setOpen(panel.hidden); });
        root.querySelector('.ta-close').addEventListener('click', function () { setOpen(false); });
        document.addEventListener('keydown', function (event) {
            // After a page change the old widget is gone; only the live one reacts.
            if (root.isConnected && event.key === 'Escape' && !panel.hidden) setOpen(false);
        });
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            const text = input.value.trim();
            if (!text) return;
            input.value = '';
            send(text);
        });
    }

    function initAll() { document.querySelectorAll('[data-assistant]').forEach(init); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
    else initAll();
    // Ternah Health swaps the page body on navigation; the widget comes back with it.
    document.addEventListener('htmx:afterSettle', initAll);
})();
