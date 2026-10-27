/* Paint-by-Numbers: one owner for each upload/job, with stale-response guards. */
(() => {
    'use strict';
    let defaults = null, presets = null;
    const controls = {
        resolution_factor: ['resolutionFactor', 'resolutionBadge'], k: ['kSlider', 'kBadge'],
        bilateral_diameter: ['bilateralDiameter', 'bilateralBadge'], sigma_color: ['sigmaColor', 'sigmaBadge'],
        area_min: ['areaMin', 'areaMinBadge'], stroke_width: ['strokeWidth', 'strokeBadge'],
        font_scale: ['fontScale', 'fontBadge']
    };
    const el = Object.fromEntries([...document.querySelectorAll('[id]')].map(node => [node.id, node]));
    const canvases = [el.sourceCanvas, el.quantizedCanvas, el.templateCanvas];
    const state = {revision: 0, source: null, image: null, active: null, result: null,
        template: null, labels: null, selected: null, parameters: {}, configured: false, zoom: 100, panX: 0, panY: 0};

    function message(text, type = 'info') {
        el.statusMessage.textContent = text;
        el.statusMessage.className = `status-message ${type}`;
    }
    function updateControls() {
        if (!state.configured) {
            el.processBtn.disabled = true;
            return;
        }
        for (const [name, [slider, badge]] of Object.entries(controls)) {
            el[slider].value = state.parameters[name];
            el[badge].textContent = ['resolution_factor', 'font_scale'].includes(name)
                ? state.parameters[name].toFixed(1) : state.parameters[name];
            el[slider].disabled = Boolean(state.active);
        }
        const divisor = el.canvasUnit.value === 'in' ? 2.54 : 1;
        el.canvasWidth.min = 5 / divisor; el.canvasWidth.max = 200 / divisor;
        el.canvasWidth.value = state.parameters.canvas_width_cm ? Number((state.parameters.canvas_width_cm / divisor).toFixed(6)) : '';
        el.paperSize.value = state.parameters.paper_size; el.sizingMode.value = state.parameters.sizing_mode;
        el.paperSize.disabled = el.sizingMode.disabled = el.canvasUnit.disabled = Boolean(state.active);
        for (const key of ['stroke_width', 'font_scale']) el[controls[key][0]].disabled = Boolean(state.active) || state.parameters.sizing_mode === 'physical';
        el.canvasWidth.disabled = el.preset.disabled = Boolean(state.active);
        el.colorSpace.value = state.parameters.color_space;
        el.protectEyes.checked = state.parameters.protect_eyes;
        el.colorSpace.disabled = el.protectEyes.disabled = Boolean(state.active);
        el.processBtn.disabled = Boolean(state.active) || !state.source;
        el.stopBtn.classList.toggle('hidden', !state.active);
        el.loadingIndicator.classList.toggle('hidden', !state.active);
        if (!state.active) {
            el.processBtnText.textContent = 'Process Image';
            el.progressFill.style.width = '0%';
        }
        editor?.updateControls();
    }
    function transform() {
        el.zoomSlider.value = state.zoom;
        el.zoomBadge.textContent = `${state.zoom}%`;
        for (const canvas of canvases) {
            canvas.style.transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.zoom / 100})`;
        }
    }
    function resetView() {
        state.zoom = 100; state.panX = 0; state.panY = 0;
        transform();
    }
    function clearResults() {
        el.finishDesignBtn.classList.add('hidden');
        state.completedJob = null;
        state.result = state.template = state.labels = state.selected = null;
        el.resultsContainer.classList.add('hidden');
        el.printTemplate.removeAttribute('src');
        el.canvasInfo.textContent = '';
        state.missing = null;
        document.body.classList.remove('print-ready');
        el.printReadyBtn.textContent = 'Print Ready View';
        el.printReadyBtn.setAttribute('aria-pressed', 'false');
        el.palettePreview.replaceChildren();
        const empty = document.createElement('p');
        empty.className = 'empty-text';
        empty.textContent = 'Process an image to view palette';
        el.palettePreview.append(empty);
        el.palettePreview.classList.add('empty');
        for (const name of ['quantized', 'template']) {
            el[`${name}Canvas`].width = el[`${name}Canvas`].height = 1;
            el[`${name}Placeholder`].classList.remove('hidden');
            el[`${name}Placeholder`].textContent = 'Process an image to preview';
        }
        for (const name of ['TemplatePNG', 'TemplatePDF', 'PalettePNG', 'PalettePDF', 'TilesPDF']) {
            el[`download${name}`].removeAttribute('href');
        }
    }
    async function request(url, options = {}) {
        const response = await fetch(url, {...options, signal: options.signal || AbortSignal.timeout(30000)});
        if (!response.ok) {
            let detail = `Request failed (${response.status}).`;
            try {
                const body = await response.json();
                if (typeof body.detail === 'string') detail = body.detail;
                else if (Array.isArray(body.detail)) detail = body.detail.map(error =>
                    `${error.loc?.at(-1) || 'Input'}: ${error.msg}`).join('; ');
            } catch (_) { /* Keep a useful message for a non-JSON server response. */ }
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        return response;
    }
    async function stopRemote(run) {
        if (!run?.requestId || run.stopSent) return;
        run.stopSent = true;
        try {
            await request(`/api/process/cancel/${run.requestId}`, {method: 'POST'});
        } catch (error) {
            run.stopSent = false;
            // Local state stays invalidated even if the server is temporarily unreachable.
            if (!state.active && state.revision === run.cancelRevision) message(`Stopped waiting. Server cancellation could not be confirmed: ${error.message}`, 'error');
        }
    }
    function invalidate() {
        state.revision += 1;
        const old = state.active;
        state.active = null;
        if (old) {
            old.cancelRevision = state.revision;
            clearTimeout(old.timer);
            old.pollController?.abort();
            void stopRemote(old);
            // If /start is pending, its continuation cancels the returned job ID.
        }
        clearResults();
        updateControls();
        return state.revision;
    }
    function isCurrent(run) {
        return state.active === run && state.revision === run.revision;
    }
    function loadImage(url) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = () => reject(new Error('The image could not be decoded. Use PNG, JPEG, WebP, or BMP.'));
            image.src = url;
        });
    }
    function draw(canvas, image) {
        const scale = canvas === el.sourceCanvas ? Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight)) : 1;
        canvas.width = Math.round(image.naturalWidth * scale); canvas.height = Math.round(image.naturalHeight * scale);
        const context = canvas.getContext('2d');
        context.fillStyle = 'white'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
    }
    async function handleFile(file) {
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) return message('Images must be 20 MB or smaller.', 'error');
        if (!/\.(png|jpe?g|webp|bmp)$/i.test(file.name) && !['image/png', 'image/jpeg', 'image/webp', 'image/bmp'].includes(file.type)) {
            return message('Use a PNG, JPEG, WebP, or BMP image.', 'error');
        }
        if (!editor.prepareNewSource()) return;
        const revision = invalidate();
        state.source = state.image = null;
        el.sourceCanvas.width = el.sourceCanvas.height = 1;
        el.sourcePlaceholder.classList.remove('hidden');
        updateControls(); resetView();
        const url = URL.createObjectURL(file);
        try {
            const image = await loadImage(url);
            if (state.revision !== revision) return;
            if (image.naturalWidth * image.naturalHeight > 40000000) throw new Error('The source image must contain at most 40 million pixels.');
            state.source = file; state.image = {naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight};
            draw(el.sourceCanvas, image);
            editor.captureSource();
            el.sourcePlaceholder.classList.add('hidden');
            message('Image loaded. Choose settings and process it.', 'success');
        } catch (error) {
            if (state.revision === revision) message(error.message, 'error');
        } finally {
            URL.revokeObjectURL(url);
            if (state.revision === revision) updateControls();
        }
    }
    async function processImage() {
        if (!state.source || state.active || state.editorBusy || editor.project || !state.configured) return;
        if (!el.canvasWidth.reportValidity()) return;
        const width = Math.round(state.image.naturalWidth * state.parameters.resolution_factor);
        const height = Math.round(state.image.naturalHeight * state.parameters.resolution_factor);
        if (width < 1 || height < 1 || width * height < state.parameters.k) {
            return message('The image is too small for this resolution and number of colors.', 'error');
        }
        if (width * height > 4000000) return message('Reduce Resolution Factor: processing is limited to 4 million pixels.', 'error');
        const revision = invalidate();
        const run = {revision, requestId: crypto.randomUUID(), jobId: null, timer: null, pollController: null, stopSent: false, failures: 0};
        state.active = run;
        updateControls();
        el.processBtnText.textContent = 'Starting…';
        message('Starting processing…');
        const form = new FormData();
        form.append('file', state.source, state.source.name);
        form.append('request_id', run.requestId);
        form.append('manual_protection', JSON.stringify(editor.manual));
        for (const [key, value] of Object.entries(state.parameters)) form.append(key, String(value));
        try {
            const response = await request('/api/process/start', {method: 'POST', body: form});
            const body = await response.json();
            run.jobId = body.job_id;
            if (!isCurrent(run)) return void stopRemote(run);
            void poll(run);
        } catch (error) {
            if (!isCurrent(run)) return;
            run.cancelRevision = state.revision;
            void stopRemote(run);
            state.active = null; updateControls();
            message(error.message, 'error');
        }
    }
    function presentResult(result, preview, template, labels, missing, exportBase) {
        const labelCanvas = document.createElement('canvas');
        draw(labelCanvas, labels);
        const rgba = labelCanvas.getContext('2d').getImageData(0, 0, labels.naturalWidth, labels.naturalHeight).data;
        state.labels = new Uint8Array(rgba.length / 4);
        for (let i = 0; i < state.labels.length; i++) state.labels[i] = rgba[i * 4];
        draw(labelCanvas, missing);
        const missingRGBA = labelCanvas.getContext('2d').getImageData(0, 0, missing.naturalWidth, missing.naturalHeight).data;
        state.missing = new Uint8Array(state.labels.length);
        for (let i = 0; i < state.missing.length; i++) state.missing[i] = missingRGBA[i * 4];
        labelCanvas.width = labelCanvas.height = 1;
        state.result = result; state.template = template;
        el.canvasInfo.textContent = result.canvas ? `Canvas: ${(result.canvas.width_mm / 10).toFixed(1)} × ${(result.canvas.height_mm / 10).toFixed(1)} cm; ${result.canvas.effective_dpi} ppi. ${result.canvas.custom ? 'Custom PDF: print at 100%, without Fit to page.' : `PDF fits ${result.parameters.paper_size.toUpperCase()} with 10 mm margins.`}` : '';
        draw(el.quantizedCanvas, preview); draw(el.templateCanvas, template);
        transform();
        el.quantizedPlaceholder.classList.add('hidden');
        el.templatePlaceholder.classList.add('hidden');
        renderPalette(result.palette);
        for (const kind of ['template', 'palette']) {
            for (const format of ['png', 'pdf']) {
                el[`download${kind[0].toUpperCase() + kind.slice(1)}${format.toUpperCase()}`].href =
                    `${exportBase}/${kind}.${format}`;
            }
        }
        el.downloadTilesPDF.href = `${exportBase}/tiles.pdf`;
        let printStyle = document.getElementById('printPaperStyle');
        if (!printStyle) { printStyle = document.createElement('style'); printStyle.id = 'printPaperStyle'; document.head.append(printStyle); }
        const paper = result.parameters.paper_size === 'letter' ? 'letter' : 'A4';
        const orientation = result.image_dimensions.width > result.image_dimensions.height ? 'landscape' : 'portrait';
        printStyle.textContent = `@media print { @page { size: ${paper} ${orientation}; margin: 10mm; } #printTemplate { max-width: 100%; max-height: ${paper === 'letter' ? (orientation === 'landscape' ? 195.9 : 259.4) : (orientation === 'landscape' ? 190 : 277)}mm; } }`;
        el.printTemplate.src = result.template_image;
        el.resultsContainer.classList.remove('hidden');
        el.finishDesignBtn.classList.toggle('hidden', Boolean(editor.project));
    }
    async function poll(run) {
        if (!isCurrent(run)) return;
        try {
            run.pollController = new AbortController();
            const response = await request(`/api/process/status/${run.jobId}`, {
                signal: AbortSignal.any([run.pollController.signal, AbortSignal.timeout(15000)])
            });
            const data = await response.json();
            if (!isCurrent(run)) return;
            if (data.error || data.status === 'Stopped' || data.status === 'Error') {
                state.active = null; updateControls();
                return message(data.error || data.status, 'error');
            }
            el.progressFill.style.width = `${Math.max(0, Math.min(100, data.progress || 0))}%`;
            el.processBtnText.textContent = `${data.status} (${data.progress || 0}%)`;
            if (data.status === 'Complete' && data.result) {
                const result = data.result;
                const [preview, template, labels, missing] = await Promise.all([
                    loadImage(result.processed_image), loadImage(result.template_image), loadImage(result.label_map), loadImage(result.missing_regions.mask)
                ]);
                if (!isCurrent(run)) return;
                presentResult(result, preview, template, labels, missing, `/api/process/export/${run.jobId}`);
                state.completedJob = run.jobId;
                state.active = null; updateControls();
                message(['Processing complete.', ...(result.warnings || [])].join(' '), result.warnings?.length ? 'info' : 'success');
                return;
            }
            run.failures = 0;
        } catch (error) {
            if (!isCurrent(run)) return;
            // Retry brief network failures, but never poll forever after losing the server.
            if (++run.failures >= 3) {
                invalidate();
                message(`Processing could not be completed: ${error.message}`, 'error');
                return;
            }
            message(`Connection interrupted. Retrying (${run.failures}/3)…`);
        }
        // Schedule only after the previous response and rendering have finished.
        if (isCurrent(run)) run.timer = setTimeout(() => poll(run), run.failures ? 1000 : 250);
    }
    function renderPalette(palette) {
        el.palettePreview.replaceChildren();
        el.palettePreview.classList.remove('empty');
        for (const color of palette) {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'palette-swatch';
            button.style.backgroundColor = color.hex;
            const linear = color.rgb.map(c => c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
            const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
            button.style.color = luminance > 0.179 ? '#000' : '#fff';
            button.textContent = color.id;
            button.dataset.id = color.id;
            button.title = `Color ${color.id}: ${color.hex.toUpperCase()} (RGB ${color.rgb.join(', ')})`;
            button.setAttribute('aria-label', button.title);
            button.setAttribute('aria-pressed', 'false');
            button.addEventListener('click', () => {
                state.selected = state.selected === color.id ? null : color.id;
                for (const swatch of el.palettePreview.children) {
                    const selected = Number(swatch.dataset.id) === state.selected;
                    swatch.classList.toggle('active', selected);
                    swatch.setAttribute('aria-pressed', String(selected));
                }
                drawHighlight();
            });
            el.palettePreview.append(button);
        }
    }
    let highlightRevision = 0;
    async function drawHighlight() {
        const revision = ++highlightRevision;
        const resultRevision = state.revision;
        const selected = state.selected;
        const labels = state.labels, missing = state.missing;
        if (!state.template) return;
        draw(el.templateCanvas, state.template);
        if (document.body.classList.contains('print-ready')) return;
        if (el.showEyes.checked) {
            const overlay = el.templateCanvas.getContext('2d');
            overlay.strokeStyle = '#b000cc'; overlay.lineWidth = 2;
            for (const [x,y,w,h] of state.result.eye_protection.boxes) overlay.strokeRect(x,y,w,h);
        }
        if (!selected && !el.showMissing.checked) { editor.overlaySelection(); return; }
        const context = el.templateCanvas.getContext('2d');
        const image = context.getImageData(0, 0, el.templateCanvas.width, el.templateCanvas.height);
        for (let offset = 0; offset < image.data.length; offset += 4) {
            if (offset && offset % 262144 === 0) {
                await new Promise(resolve => requestAnimationFrame(resolve));
                if (revision !== highlightRevision || resultRevision !== state.revision) return;
            }
            if (el.showMissing.checked && missing[offset / 4]) {
                image.data[offset] = Math.round(image.data[offset] * .6 + 255 * .4);
                image.data[offset + 1] = Math.round(image.data[offset + 1] * .6 + 120 * .4);
                image.data[offset + 2] = Math.round(image.data[offset + 2] * .6);
            } else if (labels[offset / 4] + 1 === selected) {
                image.data[offset] = Math.round(image.data[offset] * 0.65);
                image.data[offset + 1] = Math.round(image.data[offset + 1] * 0.65 + 166 * 0.35);
                image.data[offset + 2] = Math.round(image.data[offset + 2] * 0.65 + 255 * 0.35);
            }
        }
        context.putImageData(image, 0, 0);
        editor.overlaySelection();
    }
    function resetAll() {
        if (!state.configured || !editor.prepareNewSource()) return;
        invalidate();
        state.parameters = {...defaults}; state.source = state.image = null;
        el.fileInput.value = '';
        el.preset.value = 'simple';
        el.sourceCanvas.width = el.sourceCanvas.height = 1;
        el.sourcePlaceholder.classList.remove('hidden');
        updateControls(); resetView();
        message('Image and settings reset.');
    }
    async function download(event) {
        event.preventDefault();
        const href = event.currentTarget.getAttribute('href');
        const revision = state.revision;
        if (!state.result || !href) return;
        try {
            const response = await request(href);
            const blob = await response.blob();
            if (revision !== state.revision) return;
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url; anchor.download = href.split('/').at(-1);
            document.body.append(anchor); anchor.click(); anchor.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            if (revision === state.revision) message(`Download failed: ${error.message}`, 'error');
        }
    }
    const editor = window.createPaintingEditor({state, el, message, request, loadImage, draw, transform, resetView, updateControls, presentResult, resetAll, clearResults});
    el.dropzone.addEventListener('click', () => el.fileInput.click());
    el.dropzone.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); el.fileInput.click(); }
    });
    el.fileInput.addEventListener('change', event => { void handleFile(event.target.files[0]); event.target.value = ''; });
    for (const type of ['dragover', 'dragleave', 'drop']) {
        el.dropzone.addEventListener(type, event => {
            event.preventDefault(); event.stopPropagation();
            el.dropzone.classList.toggle('drag-over', type === 'dragover');
            if (type === 'drop') void handleFile(event.dataTransfer?.files[0]);
        });
    }
    for (const [name, [slider]] of Object.entries(controls)) {
        el[slider].addEventListener('input', () => {
            if (state.active) return;
            state.parameters[name] = Number(el[slider].value);
            el.preset.value = 'custom';
            if (state.result) { invalidate(); message('Settings changed. Process the image to update the result.'); }
            updateControls();
        });
    }
    for (const [name, id] of [['color_space', 'colorSpace'], ['protect_eyes', 'protectEyes']]) {
        el[id].addEventListener('change', () => {
            if (state.active) return;
            state.parameters[name] = name === 'protect_eyes' ? el[id].checked : el[id].value;
            if (state.result) { invalidate(); message('Settings changed. Process the image to update the result.'); }
            updateControls();
        });
    }
    el.canvasUnit.addEventListener('change', updateControls);
    for (const [id, key] of [['paperSize', 'paper_size'], ['sizingMode', 'sizing_mode']]) el[id].addEventListener('change', () => {
        state.parameters[key] = el[id].value; invalidate(); clearResults(); updateControls();
        message('Painting dimensions changed. Process again to update numbers and exports.');
    });
    el.canvasWidth.addEventListener('change', () => {
        if (!el.canvasWidth.checkValidity()) return message(`Choose a canvas width from ${Number(el.canvasWidth.min).toFixed(2)} to ${Number(el.canvasWidth.max).toFixed(2)} ${el.canvasUnit.value}, or leave it blank to fit the selected paper.`, 'error');
        state.parameters.canvas_width_cm = Number(el.canvasWidth.value) * (el.canvasUnit.value === 'in' ? 2.54 : 1) || 0;
        invalidate(); updateControls();
        message('Canvas width changed. Process to update export and readability checks.');
    });
    el.preset.addEventListener('change', () => {
        if (!presets[el.preset.value]) return;
        state.parameters = {...presets[el.preset.value], canvas_width_cm: state.parameters.canvas_width_cm, paper_size: state.parameters.paper_size};
        invalidate(); updateControls();
        message('Preset applied. Review the result, especially fine facial details.');
    });
    for (const button of document.querySelectorAll('[data-pan]')) button.addEventListener('click', () => {
        const [x, y] = button.dataset.pan.split(',').map(Number);
        state.panX += x; state.panY += y; transform();
    });
    el.viewChoice.addEventListener('change', () => {
        document.querySelector('.canvas-container').dataset.view = el.viewChoice.value;
    });
    el.showEyes.addEventListener('change', drawHighlight);
    el.showMissing.addEventListener('change', drawHighlight);
    el.processBtn.addEventListener('click', processImage);
    el.stopBtn.addEventListener('click', () => { invalidate(); message('Processing cancelled.'); });
    el.resetBtn.addEventListener('click', resetAll);
    el.resetTransformBtn.addEventListener('click', resetView);
    el.zoomSlider.addEventListener('input', () => { state.zoom = Number(el.zoomSlider.value); transform(); });
    el.printReadyBtn.addEventListener('click', () => {
        const ready = document.body.classList.toggle('print-ready');
        el.printReadyBtn.textContent = ready ? 'Exit Print Ready View' : 'Print Ready View';
        el.printReadyBtn.setAttribute('aria-pressed', String(ready));
        resetView(); drawHighlight();
    });
    for (const name of ['TemplatePNG', 'TemplatePDF', 'PalettePNG', 'PalettePDF', 'TilesPDF']) el[`download${name}`].addEventListener('click', download);
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void processImage(); }
    });
    for (const canvas of canvases) {
        canvas.tabIndex = 0;
        canvas.setAttribute('aria-label', `${canvas.id.replace('Canvas', '')} image. Arrow keys pan, plus and minus zoom, Home resets.`);
        canvas.addEventListener('keydown', event => {
            const movement = {ArrowLeft: [-30,0], ArrowRight: [30,0], ArrowUp: [0,-30], ArrowDown: [0,30]}[event.key];
            if (movement) { state.panX += movement[0]; state.panY += movement[1]; }
            else if (event.key === '+' || event.key === '=') state.zoom = Math.min(300, state.zoom + 10);
            else if (event.key === '-') state.zoom = Math.max(25, state.zoom - 10);
            else if (event.key === 'Home') resetView();
            else return;
            event.preventDefault(); transform();
        });
        let drag = null;
        canvas.addEventListener('wheel', event => {
            event.preventDefault();
            state.zoom = Math.max(25, Math.min(300, state.zoom + (event.deltaY < 0 ? 10 : -10)));
            transform();
        }, {passive: false});
        canvas.addEventListener('pointerdown', event => {
            if (event.button !== 0 || !state.source || editor.tool !== 'pan') return;
            drag = {id: event.pointerId, x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY};
            canvas.setPointerCapture(event.pointerId);
        });
        canvas.addEventListener('pointermove', event => {
            if (!drag || drag.id !== event.pointerId) return;
            state.panX = drag.panX + event.clientX - drag.x;
            state.panY = drag.panY + event.clientY - drag.y;
            transform();
        });
        for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(event, () => { drag = null; });
    }
    window.addEventListener('pagehide', () => {
        if (state.active?.requestId) navigator.sendBeacon(`/api/process/cancel/${state.active.requestId}`);
    });
    void request('/api/hardware-status').then(response => response.json()).then(status => {
        el.hwAccelStatus.textContent = status.message;
        el.hwAccelStatus.classList.toggle('active', status.has_hardware_acceleration);
    }).catch(() => { el.hwAccelStatus.textContent = 'Hardware status unavailable; processing may still be available.'; });
    clearResults(); updateControls(); transform();
    void request('/api/settings').then(response => response.json()).then(config => {
        defaults = Object.freeze(config.defaults); presets = config.presets;
        state.parameters = {...defaults}; state.configured = true;
        for (const [name, [slider]] of Object.entries(controls)) {
            [el[slider].min, el[slider].max] = config.ranges[name];
        }
        updateControls();
    }).catch(error => message(`Settings could not be loaded: ${error.message}. Reload the page.`, 'error'));
})();
