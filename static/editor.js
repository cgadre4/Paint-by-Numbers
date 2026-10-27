/* Local painting editor. Server revisions own document state; gestures stay drafts. */
window.createPaintingEditor = function (hooks) {
    'use strict';
    const {state, el, message, request, loadImage, draw} = hooks;
    let project = null, projectRevision = 0, metadata = null, savedFingerprint = null;
    let tool = 'pan', manual = [], disabled = [], draftHistory = [], draftCursor = 0;
    let eyeDetail = 'fine';
    let epoch = 0, selectionEpoch = 0, selection = null, selectionImage = null;
    let offset = 0, totalRegions = 0, busy = false, uncertain = false, sourceBase = null;
    let gesture = null, candidate = null;
    let selecting = false, queuedSelection = null;
    const copy = value => JSON.parse(JSON.stringify(value));
    const draftKey = () => JSON.stringify({manual, disabled, eye_detail: eyeDetail});
    const committedKey = () => JSON.stringify({manual: metadata?.manual || [], disabled: metadata?.disabled || [], eye_detail: metadata?.eye_detail || 'fine'});
    const dirty = () => Boolean(project && (metadata?.fingerprint !== savedFingerprint || draftKey() !== committedKey()));
    const buttons = ['panTool', 'protectTool', 'selectTool', 'numberTool'];
    const toolNames = ['pan', 'protect', 'select', 'number'];
    const help = {
        pan: 'Drag to pan. Use arrow keys to pan, +/− to zoom, or Home to reset the view.',
        protect: 'Draw an eye rectangle on Source. Drag an active rectangle to move it; drag its lower-right corner to resize. Numeric bounds are available below.',
        select: 'Click Colors or Template to select one connected region. Use the region list for keyboard selection.',
        number: 'Select a region, then click Template to place its readable number. The complete box must fit outside protected eyes.'
    };
    function updateControls() {
        const hasProject = Boolean(project && metadata);
        document.body.classList.toggle('project-open', hasProject);
        document.body.dataset.tool = tool;
        el.editorPanel.classList.toggle('hidden', !hasProject);
        el.protectionPanel.classList.toggle('hidden', tool !== 'protect');
        el.regenerateProtectionBtn.classList.toggle('hidden', !hasProject);
        el.projectEyeDetailGroup.classList.toggle('hidden', !hasProject);
        el.projectEyeDetail.value = eyeDetail;
        el.projectEyeDetail.disabled = busy || uncertain;
        el.cancelEditBtn.classList.toggle('hidden', !busy);
        el.refreshProjectBtn.classList.toggle('hidden', !uncertain);
        el.openProjectBtn.disabled = !state.configured || busy || Boolean(state.active);
        el.finishDesignBtn.disabled = busy || Boolean(state.active);
        el.finishDesignBtn.classList.toggle('hidden', hasProject || !state.result || busy);
        for (let i = 0; i < buttons.length; i++) {
            el[buttons[i]].setAttribute('aria-pressed', String(tool === toolNames[i]));
            el[buttons[i]].disabled = busy || Boolean(state.active) || (i >= 2 && !hasProject);
        }
        el.undoEditBtn.disabled = busy || Boolean(state.active) || !(draftCursor > 0 || hasProject && metadata.cursor > 0);
        el.redoEditBtn.disabled = busy || Boolean(state.active) || !(draftCursor < draftHistory.length || hasProject && metadata.cursor < metadata.history.length - 1);
        el.saveProjectBtn.disabled = !hasProject || busy || uncertain || draftKey() !== committedKey();
        el.includeOriginal.disabled = !metadata?.has_original || busy;
        el.closeProjectBtn.disabled = busy;
        el.regenerateProtectionBtn.disabled = busy || uncertain || draftKey() === committedKey();
        for (const name of ['applyEyeBoxBtn', 'deleteEyeBoxBtn', 'protectionList', 'eyeX', 'eyeY', 'eyeWidth', 'eyeHeight']) el[name].disabled = busy || Boolean(state.active) || !state.source;
        el.deleteEyeBoxBtn.disabled ||= el.protectionList.value === 'new';
        for (const name of ['recolorRegionBtn', 'mergeRegionBtn', 'placeNumberBtn', 'regionColor', 'mergeNeighbor', 'numberX', 'numberY']) el[name].disabled = busy || uncertain || !selection;
        if (selection) {
            el.recolorRegionBtn.disabled ||= selection.protected || Number(el.regionColor.value) === selection.color;
            el.mergeRegionBtn.disabled ||= selection.protected || !selection.neighbors.length;
        }
        el.regionList.disabled = busy || !hasProject;
        el.onlyMissingRegions.disabled = busy;
        el.previousRegions.disabled = busy || offset === 0;
        el.nextRegions.disabled = busy || offset + 100 >= totalRegions;
        el.projectBadge.textContent = dirty() ? 'Unsaved changes' : 'Saved project';
        el.projectBadge.classList.toggle('saved', !dirty());
        el.toolHelp.textContent = busy ? 'Working on a new revision. Your current design stays available until the operation succeeds.' : help[tool];
        el.loadingIndicator.classList.toggle('hidden', !busy && !state.active);
        el.protectionNotice.textContent = hasProject
            ? (draftKey() === committedKey() ? 'Protection matches the committed design.' : 'Unapplied protection changes. Regeneration may change the palette and replaces finishing edits. Undo restores the previous design.')
            : 'Rectangles will be applied when you process the image.';
        state.editorBusy = busy;
    }
    function setTool(value) {
        if (busy || state.active || (['select', 'number'].includes(value) && !project)) return;
        tool = value;
        if (value === 'protect') {
            el.viewChoice.value = 'source';
            el.viewChoice.dispatchEvent(new Event('change'));
        } else if (['select', 'number'].includes(value) && el.viewChoice.value === 'source') {
            el.viewChoice.value = 'template';
            el.viewChoice.dispatchEvent(new Event('change'));
        }
        if (value !== 'pan') document.body.classList.remove('print-ready');
        redrawSource(); updateControls();
    }
    function recordDraft(nextManual, nextDisabled, nextDetail = eyeDetail) {
        const before = {manual: copy(manual), disabled: copy(disabled), eye_detail: eyeDetail};
        manual = copy(nextManual); disabled = copy(nextDisabled);
        eyeDetail = nextDetail;
        const after = {manual: copy(manual), disabled: copy(disabled), eye_detail: eyeDetail};
        if (JSON.stringify(before) === JSON.stringify(after)) return;
        draftHistory = draftHistory.slice(0, draftCursor);
        draftHistory.push({before, after});
        if (draftHistory.length > 100) draftHistory.shift();
        draftCursor = draftHistory.length;
        refreshProtection(); redrawSource(); updateControls();
    }
    function refreshProtection(selected = 'new') {
        el.protectionList.replaceChildren(new Option('New rectangle', 'new'));
        manual.forEach((box, index) => el.protectionList.add(new Option(`Manual eye ${index + 1}`, String(index))));
        el.protectionList.value = selected;
        el.detectedProtectionList.replaceChildren();
        for (let i = 0; i < (metadata?.detected.length || 0); i++) {
            const label = document.createElement('label');
            const input = document.createElement('input');
            input.type = 'checkbox'; input.checked = !disabled.includes(i); input.disabled = busy;
            input.addEventListener('change', () => {
                const next = new Set(disabled);
                if (input.checked) next.delete(i); else next.add(i);
                recordDraft(manual, [...next].sort((a, b) => a - b));
            });
            label.append(input, document.createTextNode(` Detected eye ${i + 1}`));
            el.detectedProtectionList.append(label);
        }
    }
    function boxFields() {
        const index = Number(el.protectionList.value);
        const box = el.protectionList.value === 'new' ? [0, 0, .1, .1] : manual[index];
        if (!box) return;
        ['eyeX', 'eyeY', 'eyeWidth', 'eyeHeight'].forEach((name, i) => { el[name].value = Number((box[i] * 100).toFixed(3)); });
        el.applyEyeBoxBtn.textContent = el.protectionList.value === 'new' ? 'Add rectangle' : 'Update rectangle';
        updateControls(); redrawSource();
    }
    function redrawSource(preview = null) {
        if (!sourceBase || !state.source) return;
        const ctx = el.sourceCanvas.getContext('2d');
        ctx.clearRect(0, 0, el.sourceCanvas.width, el.sourceCanvas.height);
        ctx.drawImage(sourceBase, 0, 0);
        if (tool !== 'protect') return;
        const w = el.sourceCanvas.width, h = el.sourceCanvas.height;
        ctx.save(); ctx.lineWidth = Math.max(1, w / 550);
        const active = el.protectionList.value;
        const paint = (box, selected, detected = false) => {
            const [x, y, bw, bh] = box;
            ctx.strokeStyle = selected ? '#f2cc60' : detected ? '#58c9ed' : '#c6a3ff';
            ctx.fillStyle = selected ? '#f2cc6028' : '#ad82ff20';
            ctx.fillRect(x * w, y * h, bw * w, bh * h);
            ctx.strokeRect(x * w, y * h, bw * w, bh * h);
            if (selected) { ctx.fillStyle = '#f2cc60'; const size = Math.max(4, w / 90); ctx.fillRect((x + bw) * w - size / 2, (y + bh) * h - size / 2, size, size); }
        };
        for (let i = 0; i < (metadata?.detected.length || 0); i++) {
            if (disabled.includes(i)) continue;
            const [x, y, bw, bh] = metadata.detected[i];
            const dimensions = state.result.image_dimensions;
            paint([x / dimensions.width, y / dimensions.height, bw / dimensions.width, bh / dimensions.height], false, true);
        }
        (preview || manual).forEach((box, i) => paint(box, String(i) === active));
        ctx.restore();
    }
    function captureSource() {
        if (sourceBase) sourceBase.width = sourceBase.height = 1;
        sourceBase = document.createElement('canvas');
        sourceBase.width = el.sourceCanvas.width; sourceBase.height = el.sourceCanvas.height;
        sourceBase.getContext('2d').drawImage(el.sourceCanvas, 0, 0);
        redrawSource(); updateControls();
    }
    function imagePoint(canvas, event) {
        const rect = canvas.getBoundingClientRect();
        const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height);
        const left = rect.left + (rect.width - canvas.width * scale) / 2;
        const top = rect.top + (rect.height - canvas.height * scale) / 2;
        const x = (event.clientX - left) / scale / canvas.width;
        const y = (event.clientY - top) / scale / canvas.height;
        return {x, y, width: canvas.width * scale, height: canvas.height * scale, inside: x >= 0 && y >= 0 && x < 1 && y < 1};
    }
    function resetSelection() {
        selectionEpoch += 1; selection = selectionImage = null;
        queuedSelection = null;
        el.regionDescription.textContent = 'Click a region in Colors or Template, or choose one above.';
        el.mergeNeighbor.replaceChildren();
    }
    async function regionList() {
        if (!project || !metadata) return;
        const owner = epoch, revision = projectRevision;
        try {
            const response = await request(`/api/projects/${project}/revisions/${revision}/regions?offset=${offset}&missing=${el.onlyMissingRegions.checked}`);
            const data = await response.json();
            if (owner !== epoch || revision !== projectRevision) return;
            totalRegions = data.total;
            el.regionList.replaceChildren(new Option('Choose a connected region', ''));
            for (const item of data.items) el.regionList.add(new Option(`Region ${item.region + 1} · color ${item.color} · ${item.area} px${item.numbered ? '' : ' · unnumbered'}`, String(item.region)));
            el.regionPage.textContent = data.total ? `${offset + 1}–${Math.min(offset + 100, data.total)} / ${data.total}` : 'No regions';
            updateControls();
        } catch (error) { if (owner === epoch) message(error.message, 'error'); }
    }
    async function selectRegion(query) {
        if (!project || busy) return;
        const owner = epoch, revision = projectRevision, work = ++selectionEpoch;
        if (selecting) { queuedSelection = query; return; }
        selecting = true;
        try {
            const response = await request(`/api/projects/${project}/revisions/${revision}/regions?${query}`);
            const region = await response.json();
            const image = await loadImage(region.mask);
            if (owner !== epoch || revision !== projectRevision || work !== selectionEpoch) return;
            selection = region; selectionImage = image;
            el.regionList.value = String(region.region);
            el.regionColor.value = String(region.color);
            el.mergeNeighbor.replaceChildren(...region.neighbors.map(n => new Option(`Region ${n.region + 1} · color ${n.color} · ${n.area} px`, String(n.region))));
            const [x, y, w, h] = region.box;
            el.numberX.value = x + Math.floor(w / 2); el.numberY.value = y + Math.floor(h / 2);
            el.regionDescription.textContent = `Region ${region.region + 1} · palette ${region.color} · ${region.area} pixels · ${region.numbered ? 'numbered' : 'unnumbered'}${region.protected ? ' · protected: recoloring and merging blocked' : ''}.`;
            updateControls();
            el.showMissing.dispatchEvent(new Event('change'));
        } catch (error) { if (owner === epoch && work === selectionEpoch) message(error.message, 'error'); }
        finally {
            selecting = false;
            const next = queuedSelection; queuedSelection = null;
            if (next && project && !busy) void selectRegion(next);
        }
    }
    function overlaySelection() {
        if (!selection || !selectionImage || tool === 'pan' || document.body.classList.contains('print-ready')) return;
        const [x, y] = selection.box;
        el.templateCanvas.getContext('2d').drawImage(selectionImage, x, y);
    }
    async function postRetry(url, options) {
        try { return await request(url, options); }
        catch (error) { if (error.status) throw error; return request(url, options); }
    }
    function historyUI() {
        el.editHistory.replaceChildren();
        metadata.history.forEach((title, index) => {
            const li = document.createElement('li'); li.textContent = title;
            li.className = index === metadata.cursor ? 'current' : index > metadata.cursor ? 'undone' : '';
            if (index === metadata.cursor) li.setAttribute('aria-current', 'step');
            el.editHistory.append(li);
        });
        el.historyCount.textContent = `(${metadata.cursor} undo / ${metadata.history.length - 1 - metadata.cursor} redo)`;
        el.historyNote.textContent = metadata.pruned ? `${metadata.pruned} oldest action(s) were removed at the history limit. Save regularly.` : 'Up to 100 actions and 64 MiB of history. Saved projects retain this history.';
    }
    async function present(snapshot, owner, imported, preloaded = null) {
        const result = snapshot.result;
        const urls = [result.processed_image, result.template_image, result.label_map, result.missing_regions.mask, result.editor.source];
        const images = preloaded || await Promise.all(urls.map(loadImage));
        if (owner !== epoch) return false;
        state.revision += 1;
        projectRevision = snapshot.revision; metadata = result.editor;
        eyeDetail = metadata.eye_detail || 'fine';
        manual = copy(metadata.manual); disabled = copy(metadata.disabled);
        draftHistory = []; draftCursor = 0;
        resetSelection();
        hooks.presentResult(result, ...images.slice(0, 4), `/api/projects/${project}/revisions/${projectRevision}/export`);
        draw(el.sourceCanvas, images[4]);
        state.source ||= new File([], 'project-source.png', {type: 'image/png'});
        state.image = {naturalWidth: images[4].naturalWidth, naturalHeight: images[4].naturalHeight};
        state.parameters = {...result.parameters};
        el.sourcePlaceholder.classList.add('hidden');
        if (imported) savedFingerprint = metadata.fingerprint;
        captureSource(); refreshProtection(); historyUI();
        el.regionColor.replaceChildren(...result.palette.map(c => new Option(`${c.id} · ${c.hex.toUpperCase()}`, String(c.id))));
        el.numberX.max = result.image_dimensions.width - 1; el.numberY.max = result.image_dimensions.height - 1;
        offset = 0;
        void regionList();
        return true;
    }
    async function waitForProject(id, owner) {
        let failures = 0;
        while (owner === epoch) {
            try {
                const response = await request(`/api/projects/${id}`);
                const snapshot = await response.json();
                if (owner !== epoch) return null;
                if (!snapshot.pending) return snapshot;
                if (snapshot.operation) el.toolHelp.textContent = `${snapshot.operation.status} (${snapshot.operation.progress || 0}%). Your current design is unchanged until this succeeds.`;
                failures = 0;
            } catch (error) {
                if (owner !== epoch) return null;
                if (++failures >= 3) throw error;
            }
            await new Promise(resolve => setTimeout(resolve, 250));
        }
        return null;
    }
    async function closeRemote(id) {
        try { await request(`/api/projects/${id}`, {method: 'DELETE'}); }
        catch (error) { message(`The project could not be closed on the server: ${error.message}`, 'error'); }
    }
    async function openCandidate(options, imported) {
        if (busy || state.active || !state.configured || !confirmDiscard()) return;
        const owner = ++epoch, id = crypto.randomUUID();
        busy = true; candidate = id; updateControls();
        message('Opening project… Your current design stays available until the project has been validated.');
        try {
            let url, requestOptions;
            if (options.file) {
                const form = new FormData(); form.append('file', options.file); form.append('request_id', id);
                url = '/api/projects/open'; requestOptions = {method: 'POST', body: form};
            } else {
                url = '/api/projects'; requestOptions = {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({job_id: options.job, request_id: id})};
            }
            await postRetry(url, requestOptions);
            if (owner !== epoch) { void closeRemote(id); return; }
            const snapshot = await waitForProject(id, owner);
            if (!snapshot) return;
            if (snapshot.error || !snapshot.result) throw new Error(snapshot.error || 'The project could not be opened.');
            const previous = project;
            // Decode every asset before replacing the old project.
            const images = await Promise.all([snapshot.result.processed_image, snapshot.result.template_image, snapshot.result.label_map, snapshot.result.missing_regions.mask, snapshot.result.editor.source].map(loadImage));
            if (owner !== epoch) return;
            project = id; savedFingerprint = null;
            await present(snapshot, owner, imported, images);
            if (previous) void closeRemote(previous);
            message(imported ? 'Project reopened. Your design and undo history are restored.' : 'Finishing workspace ready. Select a region or protect a missed eye.', 'success');
        } catch (error) {
            if (owner === epoch) message(`Could not open project: ${error.message}. Your current design is unchanged.`, 'error');
            void closeRemote(id);
        } finally {
            if (owner === epoch) {
                busy = false; candidate = null;
                if (project === id) {
                    if (window.innerWidth <= 850) { el.viewChoice.value = 'template'; el.viewChoice.dispatchEvent(new Event('change')); }
                    setTool('select');
                }
                hooks.updateControls();
            }
        }
    }
    async function command(value) {
        if (!project || busy || uncertain) return;
        const owner = epoch, baseRevision = projectRevision;
        const payload = {expected_revision: baseRevision, request_id: crypto.randomUUID(), command: value};
        busy = true; updateControls();
        message('Applying edit… You can cancel the operation while the new revision is prepared.');
        try {
            await postRetry(`/api/projects/${project}/commands`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
            const snapshot = await waitForProject(project, owner);
            if (!snapshot) return;
            if (snapshot.error) { message(`${snapshot.error} Your previous design is unchanged.`, 'error'); return; }
            if (snapshot.revision !== baseRevision) await present(snapshot, owner, false);
            message(`Revision ${snapshot.revision} ready. ${snapshot.result.warnings.join(' ')}`, snapshot.result.warnings.length ? 'info' : 'success');
        } catch (error) {
            if (owner === epoch) {
                uncertain = true;
                message(`${error.message} Your last committed design remains visible. Reconnect to confirm the server state.`, 'error');
            }
        } finally {
            if (owner === epoch) { busy = false; hooks.updateControls(); }
        }
    }
    function confirmDiscard() {
        return !dirty() || window.confirm('This project has unsaved changes. Continue without downloading them?');
    }
    function prepareNewSource() {
        if (!confirmDiscard()) return false;
        const previous = project, pending = candidate;
        epoch += 1; selectionEpoch += 1;
        project = metadata = candidate = savedFingerprint = null;
        projectRevision = 0; manual = []; disabled = []; draftHistory = []; draftCursor = 0;
        eyeDetail = 'fine';
        busy = uncertain = false; tool = 'pan';
        resetSelection();
        if (sourceBase) sourceBase.width = sourceBase.height = 1;
        sourceBase = null;
        if (previous) void closeRemote(previous);
        if (pending && pending !== previous) void closeRemote(pending);
        refreshProtection(); updateControls();
        return true;
    }
    async function undoRedo(kind) {
        if (busy || state.active) return;
        if (kind === 'undo' && draftCursor > 0 || kind === 'redo' && draftCursor < draftHistory.length) {
            const entry = kind === 'undo' ? draftHistory[--draftCursor].before : draftHistory[draftCursor++].after;
            manual = copy(entry.manual); disabled = copy(entry.disabled);
            eyeDetail = entry.eye_detail;
            refreshProtection(); redrawSource(); updateControls();
        } else if (project) await command({type: kind});
    }
    async function saveProject() {
        if (!project || busy || draftKey() !== committedKey()) return;
        const owner = epoch, revision = projectRevision, key = metadata.fingerprint;
        try {
            const response = await request(`/api/projects/${project}/revisions/${revision}/export/project.pbn?include_original=${el.includeOriginal.checked}`);
            const blob = await response.blob();
            if (owner !== epoch || revision !== projectRevision) return;
            const url = URL.createObjectURL(blob), anchor = document.createElement('a');
            anchor.href = url; anchor.download = 'painting.pbn'; document.body.append(anchor); anchor.click(); anchor.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            savedFingerprint = key; updateControls(); message('Project download prepared, including undo history. Keep the .pbn file to reopen your work.', 'success');
        } catch (error) { if (owner === epoch) message(`Save failed: ${error.message}`, 'error'); }
    }
    el.finishDesignBtn.addEventListener('click', () => void openCandidate({job: state.completedJob}, false));
    el.openProjectBtn.addEventListener('click', () => el.projectFileInput.click());
    el.refreshSessionsBtn.addEventListener('click', async () => {
        try {
            const sessions = await (await request('/api/projects')).json();
            el.projectSessions.replaceChildren();
            for (const session of sessions) {
                const li = document.createElement('li'), button = document.createElement('button');
                const current = session.project_id === project;
                li.append(document.createTextNode(`${current ? 'This tab' : session.project_id.slice(0, 8)} · revision ${session.revision}${session.pending ? ' · working' : ''} `));
                button.type = 'button'; button.textContent = 'Close';
                button.addEventListener('click', async () => {
                    if (current) { hooks.resetAll(); return; }
                    if (!window.confirm('Close this server session? Work not downloaded as a .pbn file will be lost. It may belong to another open tab.')) return;
                    await closeRemote(session.project_id); el.refreshSessionsBtn.click();
                });
                li.append(button); el.projectSessions.append(li);
            }
            if (!sessions.length) el.projectSessions.textContent = 'No projects are open.';
        } catch (error) { message(error.message, 'error'); }
    });
    el.projectFileInput.addEventListener('change', event => {
        const file = event.target.files[0]; event.target.value = '';
        if (!file) return;
        if (file.size > 128 * 1024 * 1024) return message('Projects must be 128 MiB or smaller.', 'error');
        void openCandidate({file}, true);
    });
    el.closeProjectBtn.addEventListener('click', () => hooks.resetAll());
    buttons.forEach((name, i) => el[name].addEventListener('click', () => setTool(toolNames[i])));
    el.undoEditBtn.addEventListener('click', () => void undoRedo('undo'));
    el.redoEditBtn.addEventListener('click', () => void undoRedo('redo'));
    el.saveProjectBtn.addEventListener('click', () => void saveProject());
    el.cancelEditBtn.addEventListener('click', async () => {
        const id = candidate || project;
        if (!id) return;
        try { await request(`/api/projects/${id}/cancel`, {method: 'POST'}); }
        catch (error) { message(error.message, 'error'); }
    });
    el.refreshProjectBtn.addEventListener('click', async () => {
        if (!project || busy) return;
        const owner = epoch; busy = true; updateControls();
        try {
            const snapshot = await waitForProject(project, owner);
            if (!snapshot) return;
            if (snapshot.revision !== projectRevision) await present(snapshot, owner, false);
            uncertain = false;
            message(snapshot.error || 'Project state confirmed. You can continue editing.', snapshot.error ? 'error' : 'success');
        } catch (error) { message(error.message, 'error'); }
        finally { if (owner === epoch) { busy = false; updateControls(); } }
    });
    el.protectionList.addEventListener('change', boxFields);
    el.applyEyeBoxBtn.addEventListener('click', () => {
        if (busy || state.active) return;
        const box = ['eyeX', 'eyeY', 'eyeWidth', 'eyeHeight'].map(name => Number(el[name].value) / 100);
        if (box.some(v => !Number.isFinite(v)) || box[0] < 0 || box[1] < 0 || box[0] >= 1 || box[1] >= 1 || box[2] <= 0 || box[3] <= 0 || box[0] + box[2] > 1.0000001 || box[1] + box[3] > 1.0000001) return message('Keep the complete rectangle inside 0–100% of the image.', 'error');
        const next = copy(manual), selected = el.protectionList.value;
        if (selected === 'new') {
            if (next.length >= 128) return message('Use at most 128 protection rectangles.', 'error');
            next.push(box);
        } else next[Number(selected)] = box;
        recordDraft(next, disabled);
        refreshProtection(selected === 'new' ? String(next.length - 1) : selected); boxFields();
    });
    el.deleteEyeBoxBtn.addEventListener('click', () => {
        if (el.protectionList.value === 'new') return;
        const next = copy(manual); next.splice(Number(el.protectionList.value), 1);
        recordDraft(next, disabled); boxFields();
    });
    el.regenerateProtectionBtn.addEventListener('click', () => {
        if (metadata.cursor > 0 && !window.confirm('Regenerate with these eye areas? This replaces finishing edits and may change the palette. Undo can restore the previous design.')) return;
        void command({type: 'regenerate', manual: copy(manual), disabled: copy(disabled), eye_detail: eyeDetail});
    });
    el.projectEyeDetail.addEventListener('change', () => { recordDraft(manual, disabled, el.projectEyeDetail.value); });
    el.regionList.addEventListener('change', () => { if (el.regionList.value !== '') void selectRegion(`region=${el.regionList.value}`); });
    el.previousRegions.addEventListener('click', () => { offset = Math.max(0, offset - 100); void regionList(); });
    el.nextRegions.addEventListener('click', () => { offset += 100; void regionList(); });
    el.onlyMissingRegions.addEventListener('change', () => { offset = 0; void regionList(); });
    el.regionColor.addEventListener('change', updateControls);
    el.recolorRegionBtn.addEventListener('click', () => { if (selection) void command({type: 'recolor', region: selection.region, color: Number(el.regionColor.value)}); });
    el.mergeRegionBtn.addEventListener('click', () => { if (selection && el.mergeNeighbor.value) void command({type: 'merge', region: selection.region, target: Number(el.mergeNeighbor.value)}); });
    el.placeNumberBtn.addEventListener('click', () => { if (selection && el.numberX.reportValidity() && el.numberY.reportValidity()) void command({type: 'number', region: selection.region, x: Number(el.numberX.value), y: Number(el.numberY.value)}); });
    for (const canvas of [el.sourceCanvas, el.quantizedCanvas, el.templateCanvas]) {
        canvas.addEventListener('pointerdown', event => {
            if (tool === 'pan' || event.button !== 0 || busy || state.active || !state.source) return;
            const point = imagePoint(canvas, event);
            event.stopImmediatePropagation();
            if (!point.inside) return;
            if (tool === 'protect' && canvas === el.sourceCanvas) {
                const active = el.protectionList.value === 'new' ? -1 : Number(el.protectionList.value);
                const box = manual[active];
                const resize = box && Math.abs(point.x - box[0] - box[2]) * point.width < 8 && Math.abs(point.y - box[1] - box[3]) * point.height < 8;
                const move = box && point.x >= box[0] && point.y >= box[1] && point.x <= box[0] + box[2] && point.y <= box[1] + box[3];
                gesture = {id: event.pointerId, point, active, kind: resize ? 'resize' : move ? 'move' : 'new', before: copy(manual)};
                canvas.setPointerCapture(event.pointerId);
            } else if (tool === 'select' && canvas !== el.sourceCanvas) {
                void selectRegion(`x=${Math.floor(point.x * canvas.width)}&y=${Math.floor(point.y * canvas.height)}`);
            } else if (tool === 'number' && canvas === el.templateCanvas && selection) {
                void command({type: 'number', region: selection.region, x: Math.floor(point.x * canvas.width), y: Math.floor(point.y * canvas.height)});
            }
        }, true);
        canvas.addEventListener('pointermove', event => {
            if (!gesture || event.pointerId !== gesture.id) return;
            const point = imagePoint(canvas, event);
            const x = Math.max(0, Math.min(1, point.x)), y = Math.max(0, Math.min(1, point.y));
            const next = copy(gesture.before), start = gesture.point;
            let box;
            if (gesture.kind === 'new') {
                box = [Math.min(start.x, x), Math.min(start.y, y), Math.abs(start.x - x), Math.abs(start.y - y)];
                next.push(box);
            } else {
                box = next[gesture.active];
                if (gesture.kind === 'move') { box[0] = Math.max(0, Math.min(1 - box[2], box[0] + x - start.x)); box[1] = Math.max(0, Math.min(1 - box[3], box[1] + y - start.y)); }
                else { box[2] = Math.max(.001, x - box[0]); box[3] = Math.max(.001, y - box[1]); }
            }
            gesture.preview = next; redrawSource(next);
        });
        canvas.addEventListener('pointerup', event => {
            if (!gesture || event.pointerId !== gesture.id) return;
            const next = gesture.preview, selected = gesture.kind === 'new' ? manual.length : gesture.active;
            gesture = null;
            if (next && next.length <= 128 && next.every(b => b[2] > .001 && b[3] > .001)) {
                recordDraft(next, disabled); refreshProtection(String(selected)); boxFields();
            } else redrawSource();
        });
        for (const name of ['pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { if (gesture) { gesture = null; redrawSource(); } });
    }
    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || event.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
        if (event.key.toLowerCase() === 'z') { event.preventDefault(); void undoRedo(event.shiftKey ? 'redo' : 'undo'); }
        if (event.key.toLowerCase() === 'y') { event.preventDefault(); void undoRedo('redo'); }
        if (event.key.toLowerCase() === 's' && project) { event.preventDefault(); void saveProject(); }
    });
    window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('pagehide', event => {
        if (event.persisted) return;
        for (const id of new Set([project, candidate].filter(Boolean))) navigator.sendBeacon(`/api/projects/${id}/close`);
    });
    return {get project() { return project; }, get tool() { return tool; }, get manual() { return copy(manual); },
        updateControls, prepareNewSource, captureSource, overlaySelection};
};
