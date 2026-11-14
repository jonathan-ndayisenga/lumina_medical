/* Find a registered patient as you type.
 *
 * <div data-patient-picker data-url="/reception/patients/search.json">
 *     <input type="search" data-picker-input>
 *     <input type="hidden" name="patient" data-picker-value>   (pick mode)
 *     <div data-picker-chosen hidden>…</div>                    (pick mode)
 *     <ul data-picker-list hidden></ul>
 * </div>
 *
 * Pick mode (default) puts the chosen patient's id in the hidden input.
 * Link mode (data-mode="link" data-link="/reception/patients/?q=") opens the
 * patient instead. Listens on document, so it survives page swaps. */
(function () {
    if (window.patientPickerReady) return;
    window.patientPickerReady = true;

    var WAIT_MS = 120;
    var state = new WeakMap();

    function parts(picker) {
        return {
            input: picker.querySelector('[data-picker-input]'),
            value: picker.querySelector('[data-picker-value]'),
            chosen: picker.querySelector('[data-picker-chosen]'),
            list: picker.querySelector('[data-picker-list]'),
        };
    }

    function info(state, key) {
        var s = state.get(key);
        if (!s) { s = { timer: null, controller: null, results: [], active: -1 }; state.set(key, s); }
        return s;
    }

    function close(picker) {
        var p = parts(picker);
        if (p.list) { p.list.hidden = true; p.list.replaceChildren(); }
        if (p.input) p.input.setAttribute('aria-expanded', 'false');
        info(state, picker).active = -1;
    }

    function note(list, text) {
        var li = document.createElement('li');
        li.className = 'picker-note';
        li.textContent = text;
        list.replaceChildren(li);
        list.hidden = false;
    }

    function details(patient) {
        return [patient.age, patient.sex, patient.contact, patient.last_visit ? 'last visit ' + patient.last_visit : '']
            .filter(Boolean).join(' · ');
    }

    function render(picker) {
        var p = parts(picker);
        var s = info(state, picker);
        if (!s.results.length) {
            note(p.list, 'No registered patient matches "' + p.input.value.trim() + '".');
            return;
        }
        var items = s.results.map(function (patient, i) {
            var li = document.createElement('li');
            li.className = 'picker-option' + (i === s.active ? ' is-active' : '');
            li.setAttribute('role', 'option');
            li.setAttribute('aria-selected', i === s.active ? 'true' : 'false');
            li.dataset.index = String(i);
            var name = document.createElement('div');
            name.className = 'picker-name';
            name.textContent = patient.name;
            var more = document.createElement('div');
            more.className = 'picker-meta';
            more.textContent = details(patient);
            li.append(name, more);
            return li;
        });
        p.list.replaceChildren.apply(p.list, items);
        p.list.hidden = false;
        p.input.setAttribute('aria-expanded', 'true');
    }

    async function search(picker) {
        var p = parts(picker);
        var s = info(state, picker);
        var q = p.input.value.trim();
        if (!q) { close(picker); return; }
        if (s.controller) s.controller.abort();
        s.controller = new AbortController();
        if (!s.results.length) note(p.list, 'Searching…');
        try {
            var url = new URL(picker.dataset.url, window.location.href);
            url.searchParams.set('q', q);
            var response = await fetch(url, { credentials: 'same-origin', signal: s.controller.signal });
            if (!response.ok) { note(p.list, 'Search is not available right now.'); return; }
            var data = await response.json();
            if (p.input.value.trim() !== q) return; // typing moved on
            s.results = data.results || [];
            s.active = s.results.length ? 0 : -1;
            render(picker);
        } catch (err) {
            if (err.name !== 'AbortError') note(p.list, 'Search is not available right now.');
        }
    }

    function choose(picker, patient) {
        var p = parts(picker);
        if (picker.dataset.mode === 'link') {
            var link = (picker.dataset.link || '') + encodeURIComponent(patient.name);
            close(picker);
            window.location.assign(link);
            return;
        }
        if (p.value) p.value.value = String(patient.id);
        if (p.chosen) {
            var name = p.chosen.querySelector('[data-chosen-name]');
            var meta = p.chosen.querySelector('[data-chosen-meta]');
            if (name) name.textContent = patient.name;
            if (meta) meta.textContent = details(patient);
            p.chosen.hidden = false;
            p.input.hidden = true;
        }
        close(picker);
        picker.dispatchEvent(new CustomEvent('patient-picked', { bubbles: true, detail: patient }));
    }

    function reset(picker) {
        var p = parts(picker);
        if (p.value) p.value.value = '';
        if (p.chosen) p.chosen.hidden = true;
        p.input.hidden = false;
        p.input.value = '';
        p.input.focus();
    }

    document.addEventListener('input', function (e) {
        var picker = e.target.closest && e.target.closest('[data-patient-picker]');
        if (!picker || !e.target.matches('[data-picker-input]')) return;
        var p = parts(picker);
        if (p.value) p.value.value = '';
        var s = info(state, picker);
        clearTimeout(s.timer);
        s.timer = setTimeout(function () { search(picker); }, WAIT_MS);
    });

    document.addEventListener('keydown', function (e) {
        var picker = e.target.closest && e.target.closest('[data-patient-picker]');
        if (!picker || !e.target.matches('[data-picker-input]')) return;
        var s = info(state, picker);
        var open = !parts(picker).list.hidden && s.results.length;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (!open) return;
            e.preventDefault();
            s.active = (s.active + (e.key === 'ArrowDown' ? 1 : -1) + s.results.length) % s.results.length;
            render(picker);
        } else if (e.key === 'Enter') {
            if (open && s.active >= 0) {
                e.preventDefault();
                choose(picker, s.results[s.active]);
            } else if (picker.dataset.mode !== 'link') {
                e.preventDefault(); // nothing picked yet: don't send the form half done
            }
        } else if (e.key === 'Escape') {
            close(picker);
        }
    });

    // mousedown, not click: it fires before the input loses focus.
    document.addEventListener('mousedown', function (e) {
        var option = e.target.closest && e.target.closest('[data-patient-picker] .picker-option');
        if (option) {
            e.preventDefault();
            var picker = option.closest('[data-patient-picker]');
            choose(picker, info(state, picker).results[Number(option.dataset.index)]);
            return;
        }
        document.querySelectorAll('[data-patient-picker]').forEach(function (picker) {
            if (!picker.contains(e.target)) close(picker);
        });
    });

    document.addEventListener('click', function (e) {
        var change = e.target.closest && e.target.closest('[data-picker-change]');
        if (change) {
            e.preventDefault();
            reset(change.closest('[data-patient-picker]'));
        }
    });

    document.addEventListener('focusin', function (e) {
        var picker = e.target.closest && e.target.closest('[data-patient-picker]');
        if (picker && e.target.matches('[data-picker-input]') && e.target.value.trim()) search(picker);
    });
})();
