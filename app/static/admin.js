const $ = (sel, root = document) => root.querySelector(sel);
const view = $('#view');

function el(tag, attrs = {}, ...children) {
    const node = document.createElementNS(tag === 'svg' || attrs.svg ? 'http://www.w3.org/2000/svg' : 'http://www.w3.org/1999/xhtml', tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'svg') continue;
        if (k === 'class') node.setAttribute('class', v);
        else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
}
const svg = (tag, attrs = {}) => el(tag, { ...attrs, svg: true });

async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (!res.ok) {
        let message = `Virhe ${res.status}`;
        try { message = (await res.json()).detail || message; } catch (e) { /* ei JSONia */ }
        throw new Error(message);
    }
    return res.json();
}
const send = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

// --- Muotoilu ---
const nf = new Intl.NumberFormat('fi-FI');
const eur = (n) => new Intl.NumberFormat('fi-FI', { style: 'currency', currency: 'EUR', minimumFractionDigits: 2 }).format(n || 0);
const pct = (n) => `${new Intl.NumberFormat('fi-FI', { maximumFractionDigits: 1 }).format((n || 0) * 100)} %`;
const secs = (n) => (n == null ? '–' : n < 90 ? `${Math.round(n)} s` : `${Math.round(n / 60)} min`);
const dateTime = (t) => new Date(t * 1000).toLocaleString('fi-FI', { day: 'numeric', month: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const monthName = (ym) => {
    const [y, m] = ym.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('fi-FI', { month: 'long', year: 'numeric' });
};
function ago(t) {
    if (!t) return 'ei koskaan';
    const d = (Date.now() / 1000 - t) / 86400;
    if (d < 1) return 'tänään';
    if (d < 2) return 'eilen';
    return `${Math.floor(d)} pv sitten`;
}

let toastTimer;
function toast(text) {
    const t = $('#toast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

const STATUS = {
    queued: ['pending', 'Jonossa'], analyzing: ['pending', 'Analysoidaan'], generating: ['pending', 'Luodaan'],
    done: ['good', 'Valmis'], error: ['critical', 'Virhe'],
};
const pill = (kind, text) => el('span', { class: `pill ${kind}` }, text);

// --- Reititys ---
let pollTimer;
async function route() {
    clearTimeout(pollTimer);
    const [, section, id] = location.hash.replace(/^#\/?/, '#/').split('/');
    const NAV = { yritys: 'overview', era: 'overview', palautteet: 'feedback', tyylit: 'styles', tyyli: 'styles', asetukset: 'settings' };
    const activeNav = NAV[section] || 'overview';
    document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === activeNav));
    view.replaceChildren(el('p', { class: 'loading' }, 'Ladataan…'));
    try {
        if (section === 'yritys' && id) await renderCompany(id);
        else if (section === 'era' && id) await renderBatch(id);
        else if (section === 'palautteet') await renderFeedback();
        else if (section === 'tyylit') await renderStyles();
        else if (section === 'tyyli' && id) await renderStyle(id);
        else if (section === 'asetukset') await renderSettings();
        else await renderOverview();
    } catch (e) {
        view.replaceChildren(el('div', { class: 'empty-state' }, el('p', {}, e.message), el('a', { href: '#/', class: 'btn' }, 'Yleiskatsaukseen')));
    }
    refreshBadge();
}
window.addEventListener('hashchange', route);

async function refreshBadge() {
    try {
        const items = await api('/api/admin/feedback');
        const open = items.filter((f) => !f.handled).length;
        const badge = $('#feedbackBadge');
        badge.hidden = open === 0;
        badge.textContent = open;
    } catch (e) { /* ei kriittinen */ }
}

// --- Yleiskatsaus ---
let overviewMonth = null;

async function renderOverview() {
    const data = await api(`/api/admin/overview${overviewMonth ? `?month=${overviewMonth}` : ''}`);
    overviewMonth = data.month;
    const t = data.totals;

    const monthInput = el('input', { type: 'month', id: 'month', value: data.month, onchange: (e) => { overviewMonth = e.target.value; route(); } });
    const newCompany = el('form', { class: 'inline-form', onsubmit: async (e) => {
        e.preventDefault();
        const name = e.target.elements.name.value.trim();
        if (!name) return;
        const c = await api('/api/admin/companies', send('POST', { name }));
        location.hash = `#/yritys/${c.id}`;
    } }, el('label', { for: 'newCompany', class: 'sr-only' }, 'Uuden yrityksen nimi'),
    el('input', { id: 'newCompany', name: 'name', type: 'text', placeholder: 'Uusi yritys' }),
    el('button', { type: 'submit', class: 'btn' }, 'Lisää'));

    const tile = (label, value, sub) => el('div', { class: 'tile' }, el('span', { class: 'tile-label' }, label), el('span', { class: 'tile-value' }, value), sub ? el('span', { class: 'tile-sub' }, sub) : null);

    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('p', { class: 'eyebrow' }, 'Yleiskatsaus'), el('h1', {}, monthName(data.month))),
            el('div', { class: 'head-actions' }, el('label', { for: 'month', class: 'sr-only' }, 'Kuukausi'), monthInput, newCompany)),
        el('section', { class: 'tiles' },
            el('div', { class: 'tile hero' }, el('span', { class: 'tile-label' }, 'Käsitellyt kuvat'), el('span', { class: 'tile-value' }, nf.format(t.images)),
                el('span', { class: 'tile-sub' }, `${nf.format(t.cars)} autoa · ${t.active_companies}/${t.companies} yritystä aktiivisena`)),
            tile('Tulot', eur(t.revenue_eur), 'kuukausihinnat, alv 0'),
            tile('Tekoälykulut', eur(t.cost_eur), 'kuvat ja korjaukset'),
            tile('Kate', eur(t.margin_eur)),
            tile('Korjauspyynnöt', pct(t.fix_rate), `${t.fixes} kpl`),
            tile('Käsittelyaika', secs(t.avg_seconds), 'vastaanotosta valmiiksi')),
        data.alerts.length ? el('section', { class: 'alerts', 'aria-label': 'Huomiot' },
            data.alerts.map((a) => el('a', { class: `alert ${a.level}`, href: a.link },
                el('span', { class: 'alert-icon', 'aria-hidden': 'true' }, a.level === 'critical' ? '!' : a.level === 'warning' ? '▲' : 'i'),
                el('span', {}, a.text)))) : null,
        el('section', { class: 'panel' },
            el('div', { class: 'panel-head' }, el('h2', {}, 'Käsitellyt kuvat päivittäin'), el('span', { class: 'muted' }, monthName(data.month))),
            dailyChart(data.daily)),
        el('section', { class: 'panel' },
            el('div', { class: 'panel-head' }, el('h2', {}, 'Yritykset'), el('span', { class: 'muted' }, 'Klikkaa riviä nähdäksesi yrityksen tiedot')),
            companyTable(data.companies)));
}

function niceMax(v) {
    if (v <= 4) return 4;
    const pow = 10 ** Math.floor(Math.log10(v));
    for (const step of [1, 2, 2.5, 5, 10]) if (step * pow >= v) return step * pow;
    return 10 * pow;
}

function dailyChart(days) {
    const W = 760, H = 200, left = 36, right = 8, top = 12, bottom = 24;
    const max = niceMax(Math.max(...days.map((d) => d.images), 1));
    const band = (W - left - right) / days.length;
    const barW = Math.min(24, Math.max(2, band - 2));
    const y = (v) => top + (H - top - bottom) * (1 - v / max);
    const wrap = el('div', { class: 'chart' });
    const tip = el('div', { class: 'chart-tip', hidden: true });
    const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `Kuvat päivittäin, yhteensä ${days.reduce((s, d) => s + d.images, 0)}` });
    for (const v of [0, max / 2, max]) {
        chart.append(svg('line', { x1: left, x2: W - right, y1: y(v), y2: y(v), class: v === 0 ? 'axis' : 'grid' }));
        const label = svg('text', { x: left - 8, y: y(v) + 4, class: 'tick', 'text-anchor': 'end' });
        label.textContent = nf.format(v);
        chart.append(label);
    }
    days.forEach((d, i) => {
        const x = left + i * band + (band - barW) / 2;
        const group = svg('g', { class: 'bar' });
        if (d.images > 0) {
            const h = y(0) - y(d.images);
            const r = Math.min(4, h / 2);
            group.append(svg('rect', { x, y: y(d.images), width: barW, height: h, rx: r, class: 'bar-fill' }));
            group.append(svg('rect', { x, y: y(0) - Math.min(r, h), width: barW, height: Math.min(r, h), class: 'bar-fill' }));
        }
        if ([1, 5, 10, 15, 20, 25, days.length].includes(d.day)) {
            const label = svg('text', { x: left + i * band + band / 2, y: H - 6, class: 'tick', 'text-anchor': 'middle' });
            label.textContent = d.day;
            chart.append(label);
        }
        const hit = svg('rect', { x: left + i * band, y: top, width: band, height: H - top - bottom, class: 'hit' });
        hit.addEventListener('pointerenter', () => {
            group.classList.add('active');
            tip.textContent = `${d.day}. päivä: ${nf.format(d.images)} kuvaa`;
            tip.hidden = false;
            const rect = wrap.getBoundingClientRect();
            const px = ((left + i * band + band / 2) / W) * rect.width;
            tip.style.left = `${Math.min(Math.max(px, 70), rect.width - 70)}px`;
            tip.style.top = `${(y(d.images) / H) * rect.height - 8}px`;
        });
        hit.addEventListener('pointerleave', () => { group.classList.remove('active'); tip.hidden = true; });
        group.append(hit);
        chart.append(group);
    });
    wrap.append(chart, tip);
    return wrap;
}

function companyTable(rows) {
    if (!rows.length) return el('p', { class: 'muted pad' }, 'Ei yrityksiä. Lisää ensimmäinen yllä.');
    const head = ['Yritys', 'Tila', 'Kuvat', 'Autot', 'Tekoälykulut', 'Korjaukset', 'Hinta / kk', 'Kate', 'Viimeksi', 'Palautteet'];
    return el('div', { class: 'table-wrap' }, el('table', {},
        el('thead', {}, el('tr', {}, head.map((h, i) => el('th', { class: i >= 2 && i !== 8 ? 'num' : '' }, h)))),
        el('tbody', {}, rows.map((r) => el('tr', { class: 'clickable', tabindex: '0', onclick: () => { location.hash = `#/yritys/${r.id}`; }, onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/yritys/${r.id}`; } },
            el('td', {}, el('strong', {}, r.name), el('span', { class: 'sub' }, r.style || 'ei tyyliprofiilia')),
            el('td', {}, r.active ? pill('good', 'Aktiivinen') : pill('muted', 'Pois käytöstä')),
            el('td', { class: 'num' }, nf.format(r.images), r.image_cap ? el('span', { class: 'sub' }, `/ ${nf.format(r.image_cap)}`) : null),
            el('td', { class: 'num' }, nf.format(r.cars)),
            el('td', { class: 'num' }, eur(r.cost_eur)),
            el('td', { class: 'num' }, pct(r.fix_rate)),
            el('td', { class: 'num' }, eur(r.price_eur)),
            el('td', { class: 'num' }, eur(r.margin_eur)),
            el('td', {}, ago(r.last_used)),
            el('td', { class: 'num' }, r.open_feedback ? pill('warning', `${r.open_feedback} avoinna`) : '–'))))));
}

// --- Yritys ---
async function renderCompany(id) {
    const data = await api(`/api/admin/companies/${id}`);
    const c = data.company;
    const link = `${location.origin}/d/${c.token}`;

    const settings = el('form', { class: 'panel form-grid', onsubmit: async (e) => {
        e.preventDefault();
        const f = e.target.elements;
        await api(`/api/admin/companies/${id}`, send('PUT', {
            name: f.name.value, price_eur: f.price.value, image_cap: f.cap.value, style: f.style.value, active: f.active.checked,
        }));
        toast('Tallennettu');
        route();
    } },
    el('div', { class: 'panel-head span' }, el('h2', {}, 'Sopimus ja tyyli')),
    field('cName', 'Nimi', el('input', { id: 'cName', name: 'name', type: 'text', value: c.name, required: true })),
    field('cStyle', 'Tyyliprofiili', (() => {
        const s = el('select', { id: 'cStyle', name: 'style' }, el('option', { value: '' }, 'Ei tekoälytaustaa'), data.styles.map((st) => el('option', { value: st }, st)));
        s.value = c.style || '';
        return s;
    })()),
    field('cPrice', 'Kuukausihinta (€, alv 0)', el('input', { id: 'cPrice', name: 'price', type: 'number', min: '0', step: '1', value: c.price_eur })),
    field('cCap', 'Kuvakatto / kk (0 = ei kattoa)', el('input', { id: 'cCap', name: 'cap', type: 'number', min: '0', step: '10', value: c.image_cap })),
    el('label', { class: 'switch span', for: 'cActive' }, el('input', { id: 'cActive', name: 'active', type: 'checkbox', checked: c.active }), el('span', {}, 'Palvelu käytössä')),
    el('div', { class: 'span' }, el('button', { type: 'submit', class: 'btn primary' }, 'Tallenna')));

    let confirmRotate = false;
    const rotate = el('button', { type: 'button', class: 'btn ghost', onclick: async (e) => {
        if (!confirmRotate) {
            confirmRotate = true;
            e.currentTarget.textContent = 'Vahvista: vanha linkki lakkaa toimimasta';
            return;
        }
        await api(`/api/admin/companies/${id}/token`, { method: 'POST' });
        toast('Uusi linkki luotu');
        route();
    } }, 'Luo uusi linkki');

    const linkPanel = el('section', { class: 'panel' },
        el('div', { class: 'panel-head' }, el('h2', {}, 'Asiakkaan linkki')),
        el('div', { class: 'link-row' },
            el('label', { for: 'cLink', class: 'sr-only' }, 'Asiakkaan linkki'),
            el('input', { id: 'cLink', type: 'text', readonly: true, value: link }),
            el('button', { type: 'button', class: 'btn', onclick: async () => { await navigator.clipboard.writeText(link); toast('Linkki kopioitu'); } }, 'Kopioi'),
            el('a', { class: 'btn ghost', href: link, target: '_blank', rel: 'noopener' }, 'Avaa')),
        el('p', { class: 'muted' }, 'Lähetä linkki yrityksen työntekijöille. Linkki toimii ilman salasanaa, joten uusi se, jos se päätyy vääriin käsiin.'),
        rotate);

    const upload = el('label', { class: 'btn', for: 'adminUpload' }, 'Lataa kuvia yrityksen puolesta',
        el('input', { id: 'adminUpload', type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: async (e) => {
            const form = new FormData();
            [...e.target.files].forEach((f) => form.append('files', f));
            toast('Lähetetään…');
            const res = await api(`/api/admin/companies/${id}/batches`, { method: 'POST', body: form });
            location.hash = `#/era/${res.id}`;
        } }));

    const maxImages = Math.max(1, ...data.months.map((m) => m.images));
    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('a', { href: '#/', class: 'back' }, '‹ Yleiskatsaus'), el('h1', {}, c.name),
                el('p', { class: 'muted' }, c.active ? pill('good', 'Aktiivinen') : pill('muted', 'Pois käytöstä'), ` · ${c.style || 'ei tyyliprofiilia'} · ${eur(c.price_eur)} / kk · `,
                    c.image_cap
                        ? (data.cap_used >= c.image_cap ? pill('warning', `Kuvakatto täynnä ${data.cap_used}/${c.image_cap}`) : `tekoälykuvia tässä kuussa ${data.cap_used}/${c.image_cap}`)
                        : `tekoälykuvia tässä kuussa ${data.cap_used}`)),
            el('div', { class: 'head-actions' }, upload)),
        el('div', { class: 'two-col' }, linkPanel, settings),
        el('section', { class: 'panel' },
            el('div', { class: 'panel-head' }, el('h2', {}, 'Käyttö kuukausittain'), data.open_feedback ? el('a', { href: '#/palautteet' }, pill('warning', `${data.open_feedback} avointa palautetta`)) : null),
            el('div', { class: 'table-wrap' }, el('table', {},
                el('thead', {}, el('tr', {}, ['Kuukausi', 'Kuvat', '', 'Autot', 'Korjaukset', 'Tekoälykulut', 'Kate', 'Käsittelyaika'].map((h, i) => el('th', { class: i && i !== 2 ? 'num' : '' }, h)))),
                el('tbody', {}, [...data.months].reverse().map((m) => el('tr', {},
                    el('td', {}, monthName(m.month)),
                    el('td', { class: 'num' }, nf.format(m.images)),
                    el('td', { class: 'meter-cell' }, el('span', { class: 'meter', style: `width:${(m.images / maxImages) * 100}%` })),
                    el('td', { class: 'num' }, nf.format(m.cars)),
                    el('td', { class: 'num' }, `${m.fixes} (${pct(m.fix_rate)})`),
                    el('td', { class: 'num' }, eur(m.cost_eur)),
                    el('td', { class: 'num' }, eur(m.margin_eur)),
                    el('td', { class: 'num' }, secs(m.avg_seconds)))))))),
        el('section', { class: 'panel' },
            el('div', { class: 'panel-head' }, el('h2', {}, 'Autot'), el('span', { class: 'muted' }, `${data.batches.length} erää`)),
            data.batches.length ? el('div', { class: 'batch-grid' }, data.batches.map((b) => el('a', { class: 'batch-card', href: `#/era/${b.id}` },
                b.cover ? el('img', { src: `/api/admin/batches/${b.id}/${b.cover.name}/file/output?v=${b.cover.version}`, alt: '', loading: 'lazy' }) : el('div', { class: 'no-cover' }, 'Ei valmiita kuvia'),
                el('div', { class: 'batch-meta' },
                    el('strong', {}, b.title),
                    el('span', { class: 'muted' }, `${dateTime(b.created)} · ${b.done}/${b.count} valmiina · ${eur(b.cost_eur)}`),
                    b.errors ? pill('critical', `${b.errors} virhettä`) : b.pending ? pill('pending', `${b.pending} kesken`) : null))))
                : el('p', { class: 'muted pad' }, 'Ei vielä kuvia.')));
}

function field(id, label, control) {
    return el('div', { class: 'field' }, el('label', { for: id }, label), control);
}

// --- Erä ---
async function renderBatch(id) {
    const data = await api(`/api/admin/batches/${id}`);
    const url = (img, kind) => `/api/admin/batches/${id}/${img.name}/file/${kind}?v=${img.version}`;
    const busy = (btn, text) => { btn.disabled = true; btn.textContent = text; };

    const rows = data.images.map((img) => {
        const [statusKind, statusText] = STATUS[img.status] || ['muted', img.status];
        const redoInput = el('input', { type: 'text', id: `redo-${img.name}`, placeholder: 'Oma ohje (valinnainen)' });
        const override = el('select', { id: `ov-${img.name}`, onchange: async (e) => {
            await api(`/api/admin/batches/${id}/${img.name}/override`, send('POST', { override: e.target.value }));
            toast('Päivitetty');
            route();
        } }, el('option', { value: 'auto' }, `Automaattinen (${img.kind === 'car' ? 'ulkokuva' : 'sisäkuva'})`), el('option', { value: 'car' }, 'Käsittele ulkokuvana'), el('option', { value: 'other' }, 'Alkuperäinen kuva'));
        override.value = img.override || 'auto';
        const versions = img.versions.length ? el('select', { id: `ver-${img.name}`, onchange: async (e) => {
            if (!e.target.value) return;
            await api(`/api/admin/batches/${id}/${img.name}/version`, send('POST', { version: Number(e.target.value) }));
            toast('Versio palautettu');
            route();
        } }, el('option', { value: '' }, `Palauta versio (${img.versions.length})`), img.versions.map((v) => el('option', { value: v }, `Versio ${v}`))) : null;

        if (img.deleted) {
            return el('article', { class: 'image-row deleted' },
                el('button', { type: 'button', class: 'thumb', disabled: img.status !== 'done', onclick: () => openViewer(url(img, 'output'), url(img, 'original')) },
                    img.status === 'done' ? el('img', { src: url(img, 'output'), alt: img.original_name, loading: 'lazy' }) : el('span', {}, statusText)),
                el('div', { class: 'image-info' },
                    el('div', { class: 'image-title' }, el('strong', {}, img.original_name), pill('muted', 'Asiakas poisti')),
                    el('dl', { class: 'facts' }, fact('Kulut', eur(img.cost_eur)), fact('Poistettu', img.deleted_at ? dateTime(img.deleted_at) : '–'))),
                el('div', { class: 'image-actions' },
                    el('button', { type: 'button', class: 'btn ghost', onclick: async () => {
                        await api(`/api/admin/batches/${id}/${img.name}/undelete`, { method: 'POST' });
                        toast('Kuva palautettu asiakkaalle');
                        route();
                    } }, 'Palauta asiakkaalle')));
        }
        const aiNote = img.ai?.skipped ? pill('warning', 'Ilman tekoälyä: kuvakatto') : img.ai?.error ? pill('critical', 'Tekoäly epäonnistui') : img.ai?.fallback ? pill('muted', 'Varamalli') : null;
        return el('article', { class: 'image-row' },
            el('button', { type: 'button', class: 'thumb', disabled: img.status !== 'done', onclick: () => openViewer(url(img, 'output'), url(img, 'original')) },
                img.status === 'done' ? el('img', { src: url(img, 'output'), alt: img.original_name, loading: 'lazy' }) : el('span', {}, statusText)),
            el('div', { class: 'image-info' },
                el('div', { class: 'image-title' }, el('strong', {}, img.original_name), pill(statusKind, statusText), pill('muted', img.kind === 'car' ? 'Ulkokuva' : img.kind ? 'Sisäkuva' : '…'), aiNote),
                el('dl', { class: 'facts' },
                    fact('Malli', img.ai?.model?.split('/').pop() || '–'),
                    fact('Tekoälykutsut', img.ai_calls ?? (img.ai?.cost_eur ? 1 : 0)),
                    fact('Kulut', eur(img.cost_eur)),
                    fact('Käsittely', secs(img.seconds)),
                    fact('Versio', img.version || 0)),
                img.error || img.ai?.error ? el('p', { class: 'error-text' }, img.error || img.ai.error) : null,
                img.feedback.length ? el('ul', { class: 'fb-list' }, img.feedback.map((f) => el('li', {}, `${dateTime(f.created)}: ${f.prompt}`))) : null),
            el('div', { class: 'image-actions' },
                el('form', { class: 'redo', onsubmit: async (e) => {
                    e.preventDefault();
                    busy(e.target.querySelector('button'), 'Jonossa…');
                    await api(`/api/admin/batches/${id}/${img.name}/redo`, send('POST', { prompt: redoInput.value }));
                    route();
                } }, el('label', { for: `redo-${img.name}`, class: 'sr-only' }, 'Ohje uudelleentekoon'), redoInput,
                el('button', { type: 'submit', class: 'btn', disabled: !['done', 'error'].includes(img.status) }, 'Tee uudelleen')),
                el('label', { for: `ov-${img.name}`, class: 'sr-only' }, 'Käsittely'), override,
                versions ? [el('label', { for: `ver-${img.name}`, class: 'sr-only' }, 'Palauta versio'), versions] : null));
    });

    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('a', { href: `#/yritys/${data.company.id}`, class: 'back' }, `‹ ${data.company.name}`), el('h1', {}, data.job.title),
                el('p', { class: 'muted' }, `${dateTime(data.job.created)} · ${data.images.length} kuvaa · tekoälykulut ${eur(data.cost_eur)}`)),
            el('div', { class: 'head-actions' },
                el('button', { type: 'button', class: 'btn', onclick: async (e) => {
                    busy(e.currentTarget, 'Päivitetään…');
                    await api(`/api/admin/batches/${id}/refinish`, { method: 'POST' });
                    toast('Ulkoasu päivitetty kaikkiin kuviin');
                    route();
                } }, 'Päivitä ulkoasu kaikkiin (ilmainen)'),
                el('a', { class: 'btn ghost', href: `/api/admin/batches/${id}/zip` }, 'Lataa ZIP'),
                el('a', { class: 'btn ghost', href: `/d/${data.company.token}`, target: '_blank', rel: 'noopener' }, 'Asiakkaan näkymä'))),
        el('section', { class: 'image-list' }, rows));

    if (data.images.some((i) => ['queued', 'analyzing', 'generating'].includes(i.status))) {
        pollTimer = setTimeout(route, 4000);
    }
}

const fact = (label, value) => el('div', {}, el('dt', {}, label), el('dd', {}, String(value)));

// --- Palautteet ---
let feedbackFilter = 'open';

async function renderFeedback() {
    const items = await api('/api/admin/feedback');
    const shown = feedbackFilter === 'open' ? items.filter((f) => !f.handled) : items;
    const filterBtn = (key, label) => el('button', { type: 'button', 'aria-pressed': String(feedbackFilter === key), onclick: () => { feedbackFilter = key; route(); } }, label);
    const STATE = { processing: ['pending', 'Korjataan'], done: ['good', 'Korjattu'], error: ['critical', 'Epäonnistui'] };

    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('p', { class: 'eyebrow' }, 'Asiakkaiden pyynnöt'), el('h1', {}, 'Palautteet')),
            el('div', { class: 'segmented', role: 'group', 'aria-label': 'Suodatus' },
                filterBtn('open', `Käsittelemättä (${items.filter((f) => !f.handled).length})`), filterBtn('all', `Kaikki (${items.length})`))),
        shown.length ? el('section', { class: 'feedback-list' }, shown.map((f) => {
            // Vanhoissa palautteissa ei ole tyyppiä: alkuperäisen valinta tunnistetaan tekstistä
            const isOriginal = f.type === 'original' || (!f.type && f.prompt.startsWith('Asiakas valitsi alkuperäisen'));
            const [kind, text] = isOriginal ? ['muted', 'Alkuperäinen valittu'] : (STATE[f.status] || ['muted', f.status]);
            const note = el('textarea', { id: `note-${f.id}`, rows: '2', placeholder: 'Oma muistiinpano' });
            note.value = f.note || '';
            const redoInput = el('input', { type: 'text', id: `fbredo-${f.id}`, placeholder: 'Oma ohje uudelleentekoon' });
            return el('article', { class: 'panel feedback' + (f.handled ? ' handled' : '') },
                el('div', { class: 'feedback-head' },
                    el('div', {}, el('strong', {}, f.dealer_name), el('span', { class: 'muted' }, ` · ${dateTime(f.created)} · `), el('a', { href: `#/era/${f.job_id}` }, f.job_title)),
                    el('div', { class: 'pills' }, pill(kind, text), f.notified ? pill('muted', 'Sähköposti lähetetty') : null, f.handled ? pill('good', 'Käsitelty') : null)),
                el('blockquote', {}, f.prompt),
                el('div', { class: 'compare' }, (isOriginal ? [['original.jpg', 'Alkuperäinen'], ['before.jpg', 'Käsitelty (hylätty)']] : [['original.jpg', 'Alkuperäinen'], ['before.jpg', 'Ennen'], ['after.jpg', 'Korjattu']]).map(([file, label]) => el('figure', {},
                    el('button', { type: 'button', class: 'thumb', onclick: () => openViewer(`/api/admin/feedback/${f.id}/file/${file}`) },
                        el('img', { src: `/api/admin/feedback/${f.id}/file/${file}`, alt: label, loading: 'lazy', onerror: (e) => e.target.closest('figure').remove() })),
                    el('figcaption', {}, label)))),
                el('div', { class: 'feedback-actions' },
                    el('label', { for: `note-${f.id}`, class: 'sr-only' }, 'Muistiinpano'), note,
                    el('form', { class: 'redo', onsubmit: async (e) => {
                        e.preventDefault();
                        await api(`/api/admin/batches/${f.job_id}/${f.image}/redo`, send('POST', { prompt: redoInput.value }));
                        toast('Kuva jonossa uudelleentekoon');
                    } }, el('label', { for: `fbredo-${f.id}`, class: 'sr-only' }, 'Ohje'), redoInput, el('button', { type: 'submit', class: 'btn' }, 'Tee uudelleen')),
                    el('button', { type: 'button', class: f.handled ? 'btn ghost' : 'btn primary', onclick: async () => {
                        await api(`/api/admin/feedback/${f.id}`, send('PUT', { handled: !f.handled, note: note.value }));
                        toast(f.handled ? 'Palautettu käsittelemättömäksi' : 'Merkitty käsitellyksi');
                        route();
                    } }, f.handled ? 'Merkitse käsittelemättömäksi' : 'Merkitse käsitellyksi')));
        })) : el('div', { class: 'empty-state' }, el('p', {}, feedbackFilter === 'open' ? 'Ei käsittelemättömiä korjauspyyntöjä.' : 'Ei korjauspyyntöjä.')));
}

// --- Tyylit ---
async function renderStyles() {
    const styles = await api('/api/admin/styles');
    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('p', { class: 'eyebrow' }, 'Tyyliprofiilit'), el('h1', {}, 'Tyylit'),
                el('p', { class: 'muted' }, 'Tyyli määrää lattian, seinän, logon ja kuvamallin. Yritykselle valitaan tyyli yrityksen sivulla.'))),
        el('section', { class: 'style-grid' }, styles.map((s) => el('a', { class: 'style-card', href: `#/tyyli/${s.id}` },
            s.floor ? el('img', { src: `/api/admin/styles/${s.id}/file/viimeistely-lattia.jpg`, alt: '', loading: 'lazy' }) : el('div', { class: 'no-cover' }, 'Ei lattianäytettä'),
            el('div', { class: 'batch-meta' },
                el('strong', {}, s.name),
                el('span', { class: 'muted' }, (s.model || '').split('/').pop()),
                el('span', { class: 'muted' }, s.used_by.length ? `Käytössä: ${s.used_by.map((u) => u.name).join(', ')}` : 'Ei käytössä'))))));
}

const STYLE_GROUPS = [
    { title: 'Seinä', free: true, fields: [
        { key: 'wall_brightness', label: 'Sävy (tummuus)', step: 1, fmt: (v) => v },
        { key: 'wall_glow', label: 'Valokeila auton takana', step: 0.05, fmt: (v) => v.toFixed(2) },
        { key: 'wall_uplight', label: 'Vaalennus lattiarajaa kohti', step: 0.05, fmt: (v) => v.toFixed(2) },
        { key: 'wall_vignette', label: 'Yläkulmien tummuus', step: 0.05, fmt: (v) => v.toFixed(2) },
        { key: 'wall_texture', label: 'Pinnan kuvio', step: 0.01, fmt: (v) => v.toFixed(2) },
    ] },
    { title: 'Logo', free: true, fields: [
        { key: 'logo_width', label: 'Leveys', step: 0.01, fmt: (v) => `${Math.round(v * 100)} %` },
        { key: 'logo_y', label: 'Pystysijainti', step: 0.01, fmt: (v) => `${Math.round(v * 100)} %` },
    ] },
    { title: 'Auton sijoittelu ja laatat', free: false, fields: [
        { key: 'car_width', label: 'Auton enimmäisleveys', step: 0.01, fmt: (v) => `${Math.round(v * 100)} %` },
        { key: 'car_height', label: 'Auton enimmäiskorkeus', step: 0.01, fmt: (v) => `${Math.round(v * 100)} %` },
        { key: 'floor_y', label: 'Renkaiden alareuna', step: 0.01, fmt: (v) => `${Math.round(v * 100)} %` },
        { key: 'tile_size_m', label: 'Laatan koko', step: 0.05, fmt: (v) => `${Math.round(v * 100)} cm` },
    ] },
];

async function renderStyle(id) {
    const data = await api(`/api/admin/styles/${id}`);
    const values = { ...data.values };
    const saved = JSON.stringify(values);
    let stamp = Date.now();
    const status = el('span', { class: 'muted', id: 'styleStatus' });
    const markDirty = () => { status.textContent = JSON.stringify(values) === saved ? '' : 'Tallentamattomia muutoksia'; };

    // Esikatselu
    const previews = data.samples.map((s) => ({ ...s, img: el('img', { alt: s.label }), note: el('span', { class: 'muted' }) }));
    let previewSeq = 0;
    async function refreshPreview(useAi = false) {
        const seq = ++previewSeq;
        let total = 0;
        await Promise.all(previews.map(async (p, i) => {
            p.note.textContent = useAi ? 'Luodaan tekoälyllä…' : 'Päivitetään…';
            try {
                const res = await fetch(`/api/admin/styles/${id}/preview`, send('POST', { sample: i, values, ai: useAi }));
                if (!res.ok) throw new Error((await res.json()).detail);
                total += Number(res.headers.get('X-Cost-Eur') || 0);
                const blob = await res.blob();
                if (seq !== previewSeq && !useAi) return;
                if (p.img.src.startsWith('blob:')) URL.revokeObjectURL(p.img.src);
                p.img.src = URL.createObjectURL(blob);
                p.note.textContent = useAi ? 'Tekoälyesikatselu' : 'Esikatselu (seinä ja logo)';
            } catch (e) {
                p.note.textContent = e.message;
            }
        }));
        if (useAi) toast(`Tekoälyesikatselu valmis (${eur(total)})`);
    }
    let previewTimer;
    const schedulePreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(() => refreshPreview(false), 300); };

    const slider = (f, free) => {
        const [min, max] = data.limits[f.key];
        const out = el('output', {}, f.fmt(Number(values[f.key] ?? min)));
        return el('div', { class: 'slider' },
            el('label', { for: `s-${f.key}` }, f.label, out),
            el('input', { id: `s-${f.key}`, type: 'range', min, max, step: f.step, value: values[f.key] ?? min, oninput: (e) => {
                values[f.key] = Number(e.target.value);
                out.textContent = f.fmt(values[f.key]);
                markDirty();
                if (free) schedulePreview();
            } }));
    };

    const modelSelect = el('select', { id: 'styleModel', onchange: (e) => { values.model = e.target.value; markDirty(); } },
        data.models.map((m) => el('option', { value: m.id }, m.label)));
    modelSelect.value = values.model;

    const uploadButton = (label, path, accept, after) => el('label', { class: 'btn ghost', for: `up-${path}` }, label,
        el('input', { id: `up-${path}`, type: 'file', accept, hidden: true, onchange: async (e) => {
            const form = new FormData();
            form.append('file', e.target.files[0]);
            toast('Ladataan…');
            try {
                await api(`/api/admin/styles/${id}/${path}`, { method: 'POST', body: form });
                toast(after);
                route();
            } catch (err) {
                toast(err.message);
            }
        } }));

    const floorDesc = el('textarea', { id: 'floorDesc', rows: '2', placeholder: 'Esim. kiillotettu tumma antrasiitti graniitti' });
    floorDesc.value = values.floor_description || '';
    const generate = el('button', { type: 'button', class: 'btn', onclick: async (e) => {
        const button = e.currentTarget;
        button.disabled = true;
        button.textContent = 'Generoidaan (noin minuutti)…';
        try {
            const res = await api(`/api/admin/styles/${id}/floor/generate`, send('POST', { description: floorDesc.value }));
            toast(`Uusi lattia generoitu (${eur(res.cost_eur)}). Tee tekoälyesikatselu nähdäksesi sen kuvissa.`);
            route();
        } catch (err) {
            toast(err.message);
            button.disabled = false;
            button.textContent = 'Generoi uusi lattia tekoälyllä';
        }
    } }, 'Generoi uusi lattia tekoälyllä');

    const nameInput = el('input', { id: 'styleName', type: 'text', value: values.name || id, oninput: (e) => { values.name = e.target.value; markDirty(); } });

    const copyForm = el('form', { class: 'inline-form', onsubmit: async (e) => {
        e.preventDefault();
        try {
            const copy = await api(`/api/admin/styles/${id}/copy`, send('POST', { name: e.target.elements.copyName.value }));
            location.hash = `#/tyyli/${copy.id}`;
        } catch (err) {
            toast(err.message);
        }
    } }, el('label', { for: 'copyName', class: 'sr-only' }, 'Kopion nimi'), el('input', { id: 'copyName', name: 'copyName', type: 'text', placeholder: 'Kopion nimi', required: true }),
    el('button', { type: 'submit', class: 'btn ghost' }, 'Kopioi tyyli'));

    view.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {}, el('a', { href: '#/tyylit', class: 'back' }, '‹ Tyylit'), el('h1', {}, values.name || id),
                el('p', { class: 'muted' }, data.used_by.length ? `Käytössä: ${data.used_by.map((u) => u.name).join(', ')}` : 'Ei käytössä yhdelläkään yrityksellä')),
            el('div', { class: 'head-actions' }, copyForm)),
        el('div', { class: 'editor' },
            el('div', { class: 'editor-form' },
                el('section', { class: 'panel' }, el('h2', {}, 'Perustiedot'),
                    el('div', { class: 'field' }, el('label', { for: 'styleName' }, 'Nimi'), nameInput),
                    el('div', { class: 'field' }, el('label', { for: 'styleModel' }, 'Kuvamalli'), modelSelect)),
                STYLE_GROUPS.map((g) => el('section', { class: 'panel' },
                    el('div', { class: 'panel-head' }, el('h2', {}, g.title), g.free ? pill('good', 'Esikatselu heti, ilmainen') : pill('warning', 'Näkyy vasta tekoälyesikatselussa')),
                    g.title === 'Logo' ? el('div', { class: 'asset-row' },
                        data.files['logo.png'] ? el('img', { class: 'logo-preview', src: `/api/admin/styles/${id}/file/logo.png?v=${stamp}`, alt: 'Nykyinen logo' }) : el('span', { class: 'muted' }, 'Ei logoa'),
                        uploadButton('Vaihda logo (PNG)', 'logo', 'image/png,image/webp,image/jpeg', 'Logo vaihdettu')) : null,
                    g.fields.map((f) => slider(f, g.free)))),
                el('section', { class: 'panel' },
                    el('div', { class: 'panel-head' }, el('h2', {}, 'Lattiamateriaali'), pill('warning', 'Näkyy vasta tekoälyesikatselussa')),
                    el('div', { class: 'asset-row' },
                        data.files['viimeistely-lattia.jpg'] ? el('img', { class: 'floor-preview', src: `/api/admin/styles/${id}/file/viimeistely-lattia.jpg?v=${stamp}`, alt: 'Nykyinen lattia' }) : el('span', { class: 'muted' }, 'Ei lattianäytettä'),
                        el('div', { class: 'field grow' }, el('label', { for: 'floorDesc' }, 'Kuvaile uusi materiaali'), floorDesc,
                            el('div', { class: 'row' }, generate, uploadButton('Lataa oma kuva', 'floor/upload', 'image/*', 'Lattia vaihdettu'))),
                    el('p', { class: 'muted' }, 'Generointi tehdään Seedream 5 Prolla ylhäältä kuvattuna materiaalikuvana, noin 0,08 €. Oma kuva: suoraan ylhäältä kuvattu materiaali ilman saumoja, heijastuksia ja esineitä. Tarkista aina tekoälyesikatselulla ennen käyttöä.')))),
            el('aside', { class: 'editor-preview panel' },
                el('div', { class: 'panel-head' }, el('h2', {}, 'Esikatselu'), status),
                previews.length ? previews.map((p) => el('figure', {}, p.img, el('figcaption', {}, el('span', {}, p.label), p.note)))
                    : el('p', { class: 'muted' }, 'Esikatselu tarvitsee vähintään yhden valmiin ulkokuvan.'),
                el('div', { class: 'row wrap' },
                    el('button', { type: 'button', class: 'btn primary', onclick: async () => {
                        await api(`/api/admin/styles/${id}`, send('PUT', values));
                        toast('Tyyli tallennettu');
                        route();
                    } }, 'Tallenna'),
                    previews.length ? el('button', { type: 'button', class: 'btn', onclick: (e) => { e.currentTarget.disabled = true; refreshPreview(true).finally(() => { e.currentTarget.disabled = false; }); } },
                        `Esikatsele tekoälyllä (≈${eur(0.06 * previews.length)})`) : null),
                el('button', { type: 'button', class: 'btn ghost', disabled: !data.used_by.length, onclick: async (e) => {
                    const res = await api(`/api/admin/styles/${id}/refinish`, { method: 'POST' });
                    e.currentTarget.disabled = true;
                    toast(`Seinä ja logo päivittyvät ${res.queued} kuvaan taustalla`);
                } }, 'Päivitä seinä ja logo yritysten kuviin (ilmainen)'),
                el('p', { class: 'muted small' }, 'Tallenna ensin. Lattian, laattakoon ja sijoittelun muutokset tulevat kuviin vasta, kun kuva tehdään uudelleen.'))));
    stamp = Date.now();
    if (previews.length) refreshPreview(false);
}

// --- Asetukset ---
async function renderSettings() {
    const s = await api('/api/admin/settings');
    const fallback = el('select', { id: 'fallback', name: 'fallback' }, el('option', { value: '' }, 'Ei varamallia'), s.models.map((m) => el('option', { value: m.id }, m.label)));
    fallback.value = s.fallback_model || '';
    const statusRow = (label, ok, okText, badText) => el('div', { class: 'status-row' }, el('span', {}, label), ok ? pill('good', okText) : pill('warning', badText));
    view.replaceChildren(
        el('div', { class: 'page-head' }, el('div', {}, el('p', { class: 'eyebrow' }, 'Palvelu'), el('h1', {}, 'Asetukset'))),
        el('div', { class: 'two-col' },
            el('form', { class: 'panel', onsubmit: async (e) => {
                e.preventDefault();
                try {
                    await api('/api/admin/settings', send('PUT', { usd_eur: e.target.elements.rate.value, fallback_model: e.target.elements.fallback.value }));
                    toast('Asetukset tallennettu');
                    route();
                } catch (err) {
                    toast(err.message);
                }
            } },
            el('h2', {}, 'Tekoäly ja kulut'),
            el('div', { class: 'field' }, el('label', { for: 'rate' }, 'Euron hinta dollareina muunnettuna (1 $ = x €)'),
                el('input', { id: 'rate', name: 'rate', type: 'number', min: '0.3', max: '2', step: '0.01', value: s.usd_eur }),
                el('span', { class: 'muted small' }, 'OpenRouter laskuttaa dollareina. Jokaisen kutsun hinta muunnetaan euroiksi tällä kurssilla ja tallennetaan euroina.')),
            el('div', { class: 'field' }, el('label', { for: 'fallback' }, 'Varamalli'), fallback,
                el('span', { class: 'muted small' }, 'Käytetään, jos tyylin kuvamalli ei vastaa. Jos varamallikaan ei toimi, kuva tehdään lasketulla lattialla.')),
            el('div', {}, el('button', { type: 'submit', class: 'btn primary' }, 'Tallenna'))),
            el('section', { class: 'panel' },
                el('h2', {}, 'Tila'),
                statusRow('OpenRouter-avain', s.openrouter_key, 'Asetettu', 'Puuttuu'),
                statusRow('Sähköposti-ilmoitukset', s.smtp_configured, `Käytössä (${s.admin_email})`, 'Ei asetettu'),
                statusRow('Ylläpidon salasana', s.admin_protected, 'Käytössä', 'Ei asetettu'),
                el('p', { class: 'muted small' }, 'Sähköposti ja salasana asetetaan palvelimen ympäristömuuttujilla: AUTOSTUDIO_SMTP_HOST, AUTOSTUDIO_SMTP_PORT, AUTOSTUDIO_SMTP_USER, AUTOSTUDIO_SMTP_PASSWORD, AUTOSTUDIO_SMTP_FROM, AUTOSTUDIO_ADMIN_EMAIL, AUTOSTUDIO_USER ja AUTOSTUDIO_PASSWORD.'))));
}

// --- Kuvan suurennus ---
const viewer = $('#viewer');
let viewerSources = {};
function openViewer(output, original) {
    viewerSources = { output, original };
    $('.viewer .segmented').hidden = !original;
    setViewer('output');
    viewer.showModal();
}
function setViewer(kind) {
    $('#viewerImg').src = viewerSources[kind];
    viewer.querySelectorAll('[data-kind]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.kind === kind)));
}
viewer.querySelectorAll('[data-kind]').forEach((b) => b.addEventListener('click', () => setViewer(b.dataset.kind)));
$('#viewerClose').addEventListener('click', () => viewer.close());
viewer.addEventListener('click', (e) => { if (e.target === viewer) viewer.close(); });

route();
