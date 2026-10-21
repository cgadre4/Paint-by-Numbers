/* Paint-by-Numbers: one owner for each upload/job, with stale-response guards. */
(() => {
    'use strict';
    const defaults = Object.freeze({resolution_factor: 1, k: 8, bilateral_diameter: 7,
        sigma_color: 75, area_min: 50, stroke_width: 2, font_scale: 1});
    const controls = {
        resolution_factor: ['resolutionFactor', 'resolutionBadge'], k: ['kSlider', 'kBadge'],
        bilateral_diameter: ['bilateralDiameter', 'bilateralBadge'], sigma_color: ['sigmaColor', 'sigmaBadge'],
        area_min: ['areaMin', 'areaMinBadge'], stroke_width: ['strokeWidth', 'strokeBadge'],
        font_scale: ['fontScale', 'fontBadge']
    };
    const el = Object.fromEntries([...document.querySelectorAll('[id]')].map(node => [node.id, node]));
    const canvases = [el.sourceCanvas, el.quantizedCanvas, el.templateCanvas];
    const state = {revision: 0, source: null, image: null, active: null, result: null,
        template: null, labels: null, selected: null, parameters: {...defaults}, zoom: 100, panX: 0, panY: 0};

    function message(text, type = 'info') {
        el.statusMessage.textContent = text;
        el.statusMessage.className = `status-message ${type}`;
    }
    function updateControls() {
        for (const [name, [slider, badge]] of Object.entries(controls)) {
            el[slider].value = state.parameters[name];
            el[badge].textContent = ['resolution_factor', 'font_scale'].includes(name)
                ? state.parameters[name].toFixed(1) : state.parameters[name];
            el[slider].disabled = Boolean(state.active);
        }
        el.processBtn.disabled = Boolean(state.active) || !state.source;
        el.stopBtn.classList.toggle('hidden', !state.active);
        el.loadingIndicator.classList.toggle('hidden', !state.active);
        if (!state.active) {
            el.processBtnText.textContent = 'Process Image';
            el.progressFill.style.width = '0%';
        }
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
        state.result = state.template = state.labels = state.selected = null;
        el.resultsContainer.classList.add('hidden');
        el.printTemplate.removeAttribute('src');
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
            el[`${name}Canvas`].getContext('2d').clearRect(0, 0, el[`${name}Canvas`].width, el[`${name}Canvas`].height);
            el[`${name}Placeholder`].classList.remove('hidden');
            el[`${name}Placeholder`].textContent = 'Process an image to preview';
        }
        for (const name of ['TemplatePNG', 'TemplatePDF', 'PalettePNG', 'PalettePDF']) {
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
            throw new Error(detail);
        }
        return response;
    }
    async function stopRemote(run) {
        if (!run?.jobId || run.stopSent) return;
        run.stopSent = true;
        try {
            await request(`/api/process/stop/${run.jobId}`, {method: 'POST'});
        } catch (error) {
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
        canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const context = canvas.getContext('2d');
        context.fillStyle = 'white'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0);
    }
    async function handleFile(file) {
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) return message('Images must be 20 MB or smaller.', 'error');
        if (!/\.(png|jpe?g|webp|bmp)$/i.test(file.name) && !['image/png', 'image/jpeg', 'image/webp', 'image/bmp'].includes(file.type)) {
            return message('Use a PNG, JPEG, WebP, or BMP image.', 'error');
        }
        const revision = invalidate();
        state.source = state.image = null;
        el.sourceCanvas.getContext('2d').clearRect(0, 0, el.sourceCanvas.width, el.sourceCanvas.height);
        el.sourcePlaceholder.classList.remove('hidden');
        updateControls(); resetView();
        const url = URL.createObjectURL(file);
        try {
            const image = await loadImage(url);
            if (state.revision !== revision) return;
            if (image.naturalWidth * image.naturalHeight > 40000000) throw new Error('The source image must contain at most 40 million pixels.');
            state.source = file; state.image = image;
            draw(el.sourceCanvas, image);
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
        if (!state.source || state.active) return;
        const width = Math.round(state.image.naturalWidth * state.parameters.resolution_factor);
        const height = Math.round(state.image.naturalHeight * state.parameters.resolution_factor);
        if (width < 1 || height < 1 || width * height < state.parameters.k) {
            return message('The image is too small for this resolution and number of colors.', 'error');
        }
        if (width * height > 4000000) return message('Reduce Resolution Factor: processing is limited to 4 million pixels.', 'error');
        const revision = invalidate();
        const run = {revision, jobId: null, timer: null, pollController: null, stopSent: false, failures: 0};
        state.active = run;
        updateControls();
        el.processBtnText.textContent = 'Starting…';
        message('Starting processing…');
        const form = new FormData();
        form.append('file', state.source, state.source.name);
        for (const [key, value] of Object.entries(state.parameters)) form.append(key, String(value));
        try {
            const response = await request('/api/process/start', {method: 'POST', body: form});
            const body = await response.json();
            run.jobId = body.job_id;
            if (!isCurrent(run)) return void stopRemote(run);
            void poll(run);
        } catch (error) {
            if (!isCurrent(run)) return;
            state.active = null; updateControls();
            message(error.message, 'error');
        }
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
                const [preview, template, labels] = await Promise.all([
                    loadImage(result.processed_image), loadImage(result.template_image), loadImage(result.label_map)
                ]);
                if (!isCurrent(run)) return;
                const labelCanvas = document.createElement('canvas');
                draw(labelCanvas, labels);
                state.labels = labelCanvas.getContext('2d').getImageData(0, 0, labels.naturalWidth, labels.naturalHeight).data;
                state.result = result; state.template = template;
                draw(el.quantizedCanvas, preview); draw(el.templateCanvas, template);
                transform();
                el.quantizedPlaceholder.classList.add('hidden');
                el.templatePlaceholder.classList.add('hidden');
                renderPalette(result.palette);
                for (const kind of ['template', 'palette']) {
                    for (const format of ['png', 'pdf']) {
                        el[`download${kind[0].toUpperCase() + kind.slice(1)}${format.toUpperCase()}`].href =
                            `/api/process/export/${run.jobId}/${kind}.${format}`;
                    }
                }
                el.printTemplate.src = result.template_image;
                el.resultsContainer.classList.remove('hidden');
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
    function drawHighlight() {
        if (!state.template) return;
        draw(el.templateCanvas, state.template);
        if (!state.selected || document.body.classList.contains('print-ready')) return;
        const context = el.templateCanvas.getContext('2d');
        const image = context.getImageData(0, 0, el.templateCanvas.width, el.templateCanvas.height);
        for (let offset = 0; offset < image.data.length; offset += 4) {
            if (state.labels[offset] + 1 === state.selected) {
                image.data[offset] = Math.round(image.data[offset] * 0.65);
                image.data[offset + 1] = Math.round(image.data[offset + 1] * 0.65 + 166 * 0.35);
                image.data[offset + 2] = Math.round(image.data[offset + 2] * 0.65 + 255 * 0.35);
            }
        }
        context.putImageData(image, 0, 0);
    }
    function resetAll() {
        invalidate();
        state.parameters = {...defaults}; state.source = state.image = null;
        el.fileInput.value = '';
        el.sourceCanvas.getContext('2d').clearRect(0, 0, el.sourceCanvas.width, el.sourceCanvas.height);
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
            if (state.result) { invalidate(); message('Settings changed. Process the image to update the result.'); }
            updateControls();
        });
    }
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
    for (const name of ['TemplatePNG', 'TemplatePDF', 'PalettePNG', 'PalettePDF']) el[`download${name}`].addEventListener('click', download);
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void processImage(); }
    });
    for (const canvas of canvases) {
        let drag = null;
        canvas.addEventListener('wheel', event => {
            event.preventDefault();
            state.zoom = Math.max(25, Math.min(300, state.zoom + (event.deltaY < 0 ? 10 : -10)));
            transform();
        }, {passive: false});
        canvas.addEventListener('pointerdown', event => {
            if (event.button !== 0 || !state.source) return;
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
        if (state.active?.jobId) navigator.sendBeacon(`/api/process/stop/${state.active.jobId}`);
    });
    void request('/api/hardware-status').then(response => response.json()).then(status => {
        el.hwAccelStatus.textContent = status.message;
        el.hwAccelStatus.classList.toggle('active', status.has_hardware_acceleration);
    }).catch(() => { el.hwAccelStatus.textContent = 'Hardware status unavailable; processing may still be available.'; });
    clearResults(); updateControls(); transform();
})();
