const token = decodeURIComponent(location.pathname.split('/')[2] || '');
const API = `/api/d/${encodeURIComponent(token)}`;
const $ = (sel) => document.querySelector(sel);
const PENDING = ['queued', 'analyzing', 'generating'];
const STATUS = {
    queued: 'Jonossa',
    analyzing: 'Tunnistetaan autoa…',
    generating: 'Luodaan taustaa…',
    error: 'Käsittely epäonnistui',
};
const QUICK_FIXES = ['Tee uudelleen', 'Lattia näyttää oudolta', 'Varjo näyttää oudolta', 'Auton reunoissa näkyy vanhaa taustaa', 'Ikkunoissa näkyy vanha tausta'];
const GUIDE_KEY = 'autostudio-guide-seen';

const state = { batches: [], job: null, pollTimer: null, fixImage: null };

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v;
        else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
}

async function api(path, options = {}) {
    const res = await fetch(API + path, options);
    if (!res.ok) {
        let message = 'Jokin meni vikaan. Yritä hetken päästä uudelleen.';
        try { message = (await res.json()).detail || message; } catch (e) { /* ei JSONia */ }
        const error = new Error(message);
        error.status = res.status;
        throw error;
    }
    return res.json();
}
const json = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

let toastTimer;
function toast(text, action) {
    const t = $('#toast');
    t.replaceChildren(el('span', {}, text), action ? el('button', { type: 'button', class: 'toast-action', onclick: () => { t.hidden = true; action[1](); } }, action[0]) : null);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, action ? 12000 : 4000);
}

function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* yksityinen selaus */ } }

function ago(t) {
    const d = new Date(t * 1000);
    const today = new Date();
    const days = Math.floor((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
    const time = d.toLocaleTimeString('fi-FI', { hour: '2-digit', minute: '2-digit' });
    if (days === 0) return `tänään ${time}`;
    if (days === 1) return `eilen ${time}`;
    return d.toLocaleDateString('fi-FI', { day: 'numeric', month: 'numeric' });
}

// --- Aloitus ja näkymät ---

async function init() {
    try {
        const dealer = await api('');
        document.title = `${dealer.name} · Kuvat`;
        $('#dealerName').textContent = dealer.name;
        if (dealer.logo) {
            const logo = $('#logo');
            logo.src = `${API}/logo`;
            logo.alt = dealer.name;
            logo.hidden = false;
            $('#dealerName').hidden = true;
        }
    } catch (e) {
        document.body.replaceChildren(el('p', { class: 'invalid' }, e.message));
        return;
    }
    window.addEventListener('hashchange', route);
    await route();
    if (!storageGet(GUIDE_KEY)) {
        storageSet(GUIDE_KEY, '1'); // merkitään heti: sulkemisen close-tapahtuma ehtii jäädä kesken, jos sivu ladataan uudelleen
        openGuide();
    }
}

async function route() {
    clearTimeout(state.pollTimer);
    const jobId = location.hash.replace(/^#\/?/, '');
    $('#home').hidden = Boolean(jobId);
    $('#batch').hidden = !jobId;
    $('#back').hidden = !jobId;
    if (jobId) {
        try {
            state.job = await api(`/batches/${jobId}`);
        } catch (e) {
            toast(e.message);
            location.hash = '';
            return;
        }
        renderBatch();
    } else {
        state.job = null;
        $('#saveBar').hidden = true;
        await loadCars();
    }
    schedulePoll();
}

$('#back').addEventListener('click', () => { location.hash = ''; });

// --- Autolista ---

async function loadCars() {
    state.batches = await api('/batches');
    const list = $('#cars');
    $('#noCars').hidden = state.batches.length > 0;
    list.replaceChildren(...state.batches.map((b) => {
        const pending = b.pending > 0;
        const status = b.errors
            ? el('span', { class: 'status err' }, `${b.errors} kuvassa virhe`)
            : pending
                ? el('span', { class: 'status busy' }, el('span', { class: 'spinner small', 'aria-hidden': 'true' }), `${b.done}/${b.count} valmiina`)
                : el('span', { class: 'status ok' }, `${b.count} kuvaa · valmis`);
        return el('a', { class: 'car', href: `#/${b.id}` },
            b.cover
                ? el('img', { src: `${API}/batches/${b.id}/${b.cover.name}/output?v=${b.cover.version}`, alt: '', loading: 'lazy' })
                : el('span', { class: 'car-placeholder', 'aria-hidden': 'true' }),
            el('span', { class: 'car-text' }, el('strong', {}, b.title), status, el('span', { class: 'car-time' }, ago(b.created))),
            el('span', { class: 'chevron', 'aria-hidden': 'true' }, '›'));
    }));
}

// --- Yksi auto ---

const isPending = (job) => job.images.some((i) => PENDING.includes(i.status));

function schedulePoll() {
    clearTimeout(state.pollTimer);
    const busy = state.job ? isPending(state.job) : state.batches.some((b) => b.pending > 0);
    if (!busy) return;
    state.pollTimer = setTimeout(async () => {
        try {
            if (state.job) {
                const id = state.job.id;
                const job = await api(`/batches/${id}`);
                if (state.job?.id === id) { state.job = job; renderBatch(); }
            } else {
                await loadCars();
            }
        } catch (e) { /* verkkokatko: yritetään uudelleen */ }
        schedulePoll();
    }, state.job ? 3000 : 5000);
}

const imageUrl = (img, kind) => `${API}/batches/${state.job.id}/${img.name}/${kind}?v=${img.version}-${img.override}`;
const cardKey = (img) => `${img.name}:${img.status}:${img.version}:${img.override}`;

function renderBatch() {
    const job = state.job;
    $('#batchTitle').textContent = job.title;
    const total = job.images.length;
    const done = job.images.filter((i) => i.status === 'done').length;
    $('#progressBar').style.width = total ? `${(done / total) * 100}%` : '0%';
    $('#progressText').textContent = total === 0 ? 'Ei kuvia' : done === total ? `${total} kuvaa valmiina` : `${done}/${total} valmiina`;
    $('.progress').classList.toggle('complete', total > 0 && done === total);

    const gallery = $('#gallery');
    const existing = new Map([...gallery.children].map((c) => [c.dataset.key, c]));
    gallery.replaceChildren(...job.images.map((img) => existing.get(cardKey(img)) || card(img)));
    $('#saveBar').hidden = done === 0;
    $('#saveText').textContent = done === 1 ? '1 kuva' : `${done} kuvaa`;
}

function deleteButton(img) {
    let armed = false;
    let timer;
    return el('button', { type: 'button', class: 'btn icon', 'aria-label': 'Poista kuva', title: 'Poista kuva', onclick: async (e) => {
        const button = e.currentTarget;
        if (!armed) {
            armed = true;
            button.classList.add('armed');
            button.textContent = 'Poista?';
            timer = setTimeout(() => { armed = false; button.classList.remove('armed'); button.replaceChildren(trashIcon()); }, 3000);
            return;
        }
        clearTimeout(timer);
        button.disabled = true;
        try {
            state.job = await api(`/batches/${state.job.id}/${img.name}`, { method: 'DELETE' });
            renderBatch();
            toast('Kuva poistettu.');
        } catch (err) {
            toast(err.message);
        }
    } }, trashIcon());
}

function trashIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20');
    svg.setAttribute('height', '20');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>';
    return svg;
}

function card(img) {
    const node = el('article', { class: 'card', 'data-key': cardKey(img) });
    if (img.status !== 'done') {
        const failed = img.status === 'error';
        node.append(el('div', { class: 'frame pending' + (failed ? ' failed' : '') },
            el('img', { class: 'ghost', src: imageUrl(img, 'original'), alt: '' }),
            el('div', { class: 'state' },
                failed ? null : el('span', { class: 'spinner', 'aria-hidden': 'true' }),
                el('span', {}, STATUS[img.status] || 'Käsitellään…'))));
        if (failed) {
            node.append(el('div', { class: 'actions' },
                el('button', { type: 'button', class: 'btn', onclick: () => sendFix(img, 'Tee uudelleen') }, 'Yritä uudelleen'),
                deleteButton(img)));
        }
        return node;
    }

    const processed = img.kind === 'car' && img.override !== 'other';
    const photo = el('img', { src: imageUrl(img, 'output'), alt: img.original_name, loading: 'lazy', draggable: 'false' });
    const badge = el('span', { class: 'badge', hidden: true }, 'Alkuperäinen');
    const frame = el('div', { class: 'frame' }, photo, badge,
        processed ? el('span', { class: 'hold-hint' }, 'Paina pohjassa: alkuperäinen') : null);

    if (processed) {
        const preload = new Image();
        let timer;
        const show = () => { photo.src = imageUrl(img, 'original'); badge.hidden = false; };
        const hide = () => {
            clearTimeout(timer);
            if (!badge.hidden) { photo.src = imageUrl(img, 'output'); badge.hidden = true; }
        };
        frame.addEventListener('pointerdown', () => {
            preload.src = imageUrl(img, 'original');
            timer = setTimeout(show, 180);
        });
        ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => frame.addEventListener(ev, hide));
        frame.addEventListener('contextmenu', (e) => e.preventDefault());
    }

    const second = img.kind === 'car' && img.override === 'other'
        ? el('button', { type: 'button', class: 'btn', onclick: (e) => restore(img, e.currentTarget) }, 'Palauta käsitelty')
        : img.kind === 'car'
            ? el('button', { type: 'button', class: 'btn', onclick: () => openFix(img) }, 'Korjaa')
            : null;
    node.append(frame, el('div', { class: 'actions' + (second ? '' : ' no-second') },
        el('button', { type: 'button', class: 'btn', onclick: (e) => saveOne(img, e.currentTarget) }, 'Tallenna'),
        second,
        deleteButton(img)));
    return node;
}

// --- Nimeäminen ---

const renameSheet = $('#renameSheet');
$('#rename').addEventListener('click', () => {
    $('#renameInput').value = state.job.title;
    renameSheet.showModal();
});
$('#renameForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        state.job = await api(`/batches/${state.job.id}`, json('PUT', { title: $('#renameInput').value }));
        renderBatch();
        renameSheet.close();
    } catch (err) {
        toast(err.message);
    }
});

// --- Tallennus puhelimeen ---

const slug = (text) => text.toLowerCase().replace(/[äå]/g, 'a').replace(/ö/g, 'o').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'kuvat';

async function fileFor(img) {
    const index = state.job.images.findIndex((i) => i.name === img.name) + 1;
    const blob = await (await fetch(imageUrl(img, 'output'))).blob();
    return new File([blob], `${slug(state.job.title)}-${String(index).padStart(2, '0')}.jpg`, { type: 'image/jpeg' });
}

function canShareFiles() {
    try {
        return Boolean(navigator.canShare && navigator.canShare({ files: [new File([''], 'a.jpg', { type: 'image/jpeg' })] }));
    } catch (e) {
        return false;
    }
}

async function share(files) {
    try {
        await navigator.share({ files });
    } catch (e) {
        if (e.name === 'NotAllowedError') {
            toast(`${files.length === 1 ? 'Kuva' : `${files.length} kuvaa`} valmiina`, ['Tallenna nyt', () => share(files)]);
        } else if (e.name !== 'AbortError') {
            throw e;
        }
    }
}

function download(file) {
    const href = URL.createObjectURL(file);
    const a = el('a', { href, download: file.name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
}

async function saveOne(img, button) {
    button.disabled = true;
    try {
        const file = await fileFor(img);
        if (canShareFiles()) await share([file]);
        else download(file);
    } catch (e) {
        toast('Tallennus ei onnistunut. Yritä uudelleen.');
    } finally {
        button.disabled = false;
    }
}

$('#saveAll').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    const done = state.job.images.filter((i) => i.status === 'done');
    if (!canShareFiles()) {
        location.href = `${API}/batches/${state.job.id}/zip`;
        return;
    }
    button.disabled = true;
    button.textContent = 'Valmistellaan…';
    try {
        await share(await Promise.all(done.map(fileFor)));
    } catch (err) {
        location.href = `${API}/batches/${state.job.id}/zip`;
    } finally {
        button.disabled = false;
        button.textContent = 'Tallenna kaikki';
    }
});

// --- Kuvien lataus ---

async function shrink(file) {
    try {
        const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
        const scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(bitmap.width * scale);
        canvas.height = Math.round(bitmap.height * scale);
        canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        bitmap.close?.();
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
        return blob ? new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' }) : file;
    } catch (e) {
        return file; // esim. HEIC, jota selain ei osaa piirtää: palvelin muuntaa
    }
}

function post(url, form, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
        xhr.onload = () => {
            let body = {};
            try { body = JSON.parse(xhr.responseText); } catch (e) { /* ei JSONia */ }
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject(new Error(body.detail || 'Kuvien lähetys epäonnistui. Yritä uudelleen.'));
        };
        xhr.onerror = () => reject(new Error('Yhteys katkesi. Tarkista verkkoyhteys ja yritä uudelleen.'));
        xhr.send(form);
    });
}

async function uploadFiles(files, { title = '', jobId = null } = {}) {
    if (!files.length) return;
    const box = $('#upload');
    const bar = $('#uploadBar');
    const text = $('#uploadText');
    box.hidden = false;
    bar.style.width = '0%';
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const form = new FormData();
    if (!jobId) form.append('title', title);
    for (const [i, file] of files.entries()) {
        text.textContent = `Valmistellaan kuvia ${i + 1}/${files.length}…`;
        const small = await shrink(file);
        form.append('files', small, small.name);
    }
    text.textContent = `Lähetetään ${files.length} kuvaa…`;
    try {
        const url = jobId ? `${API}/batches/${jobId}/images` : `${API}/batches`;
        const job = await post(url, form, (p) => { bar.style.width = `${Math.round(p * 100)}%`; });
        box.hidden = true;
        toast('Kuvat vastaanotettu. Valmiit kuvat ilmestyvät tähän.');
        if (location.hash === `#/${job.id}`) {
            state.job = job;
            renderBatch();
            schedulePoll();
        } else {
            location.hash = `#/${job.id}`;
        }
    } catch (err) {
        box.hidden = true;
        toast(err.message);
    }
}

const newCarSheet = $('#newCarSheet');
$('#newCar').addEventListener('click', () => {
    $('#carName').value = '';
    newCarSheet.showModal();
});
$('#files').addEventListener('change', (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    const title = $('#carName').value.trim();
    newCarSheet.close();
    uploadFiles(files, { title });
});
$('#moreFiles').addEventListener('change', (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    uploadFiles(files, { jobId: state.job.id });
});

// --- Korjaus ---

const fixDialog = $('#fix');
$('#fixChips').replaceChildren(...QUICK_FIXES.map((text) => el('button', {
    type: 'button',
    class: 'chip',
    onclick: () => { $('#fixText').value = text; $('#fixText').focus(); },
}, text)));

function openFix(img) {
    state.fixImage = img;
    $('#fixImg').src = imageUrl(img, 'output');
    $('#fixText').value = '';
    $('#fixSend').disabled = false;
    $('#fixOriginal').disabled = false;
    fixDialog.showModal();
}

async function sendFix(img, prompt) {
    try {
        state.job = await api(`/batches/${state.job.id}/${img.name}/fix`, json('POST', { prompt }));
        renderBatch();
        schedulePoll();
        toast('Kuvaa korjataan. Palveluntarjoaja saa tiedon.');
    } catch (e) {
        toast(e.message);
    }
}

$('#fixForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('#fixSend').disabled = true;
    await sendFix(state.fixImage, $('#fixText').value.trim() || 'Tee uudelleen');
    fixDialog.close();
});

$('#fixOriginal').addEventListener('click', async () => {
    $('#fixOriginal').disabled = true;
    try {
        state.job = await api(`/batches/${state.job.id}/${state.fixImage.name}/original`, { method: 'POST' });
        renderBatch();
        toast('Kuvaksi vaihdettiin alkuperäinen.');
    } catch (e) {
        toast(e.message);
    }
    fixDialog.close();
});

async function restore(img, button) {
    button.disabled = true;
    try {
        state.job = await api(`/batches/${state.job.id}/${img.name}/restore`, { method: 'POST' });
        renderBatch();
    } catch (e) {
        toast(e.message);
        button.disabled = false;
    }
}

// --- Kuvausohje ja paneelien sulkeminen ---

const guide = $('#guide');
function openGuide() {
    guide.showModal();
}
$('#openGuide').addEventListener('click', openGuide);
guide.addEventListener('close', () => storageSet(GUIDE_KEY, '1'));

document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
    dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
});

init();
