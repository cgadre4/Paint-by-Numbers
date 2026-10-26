import io
import json

import numpy as np
import pytest
from pypdf import PdfReader

from canvas_size import painting_geometry, canvas_metadata
from cluster_cache import load_clusters
from exports import make_export, tile_layout
from pipeline import extract_contours_and_label, img_to_base64
from regions import cleanup_regions, label_glyph
from tests.conftest import sample_image, wait_for


@pytest.mark.parametrize("width_cm", [5, 20, 40, 100, 200])
def test_physical_labels_obey_minimum_and_containment(params, width_cm):
    params.update(canvas_width_cm=width_cm, k=3)
    labels = np.zeros((600, 800), np.int32)
    labels[:, 300:600] = 1
    labels[:, 600:] = 2
    geometry = painting_geometry(800, 600, params)
    _, placements, missing = extract_contours_and_label(labels, 3, geometry['stroke_width'], geometry['font_scale'], minimum_font_scale=geometry['minimum_font_scale'])
    assert not missing
    metadata = canvas_metadata(800, 600, {**params, 'stroke_width': geometry['stroke_width']}, placements)
    assert metadata['minimum_number_height_mm'] >= 2
    for p in placements:
        x, y, w, h = p['box']
        assert np.all(labels[y:y+h, x:x+w] == p['id']-1)
    assert min(label_glyph(c, geometry['minimum_font_scale']).shape[0] for c in range(3)) * width_cm * 10 / 800 >= 2


def test_physical_cleanup_preserves_contrasting_tiny_details(params):
    params.update(canvas_width_cm=5, k=3)
    raw = np.zeros((200, 200), np.int32)
    raw[40:50, 40:50] = 1
    raw[70:72, 70:72] = 2
    palette = np.array([[255,255,255], [0,0,0], [250,250,250]], np.float32)
    protected = np.zeros(raw.shape, bool)
    protected[70:72, 70:72] = True
    result = cleanup_regions(raw, 3, centers=palette, protected_mask=protected, **painting_geometry(200,200,params))
    assert np.all(result[40:50,40:50] == 1)
    assert np.array_equal(result[protected], raw[protected])


def test_extreme_physical_geometry_is_bounded(params):
    with pytest.raises(ValueError, match='too small'):
        painting_geometry(4_000_000, 1, dict(params, canvas_width_cm=5))


@pytest.mark.parametrize('paper', ['a4', 'letter'])
@pytest.mark.parametrize('dimensions', [(400,600), (600,400), (190,190)])
def test_tiled_pdf_scale_layout_and_alignment(paper, dimensions):
    w,h = dimensions
    result = dict(canvas=dict(width_mm=w,height_mm=h), parameters=dict(paper_size=paper), template_image=img_to_base64(np.full((60,40,3),255,np.uint8)))
    data = make_export(result,'tiles','pdf')
    pdf = PdfReader(io.BytesIO(data))
    layout = tile_layout(w,h,paper)
    assert len(pdf.pages) == layout['pages']
    uw,uh = layout['usable']
    assert layout['columns']*(uw-5)+5 >= w-1e-6
    assert layout['rows']*(uh-5)+5 >= h-1e-6
    for page in pdf.pages:
        assert float(page.mediabox.width) == pytest.approx(layout['page'][0],abs=.001)
        assert '50 mm calibration' in page.extract_text()
        assert 'Print 100%' in page.extract_text()
        # Image matrix remains the physical canvas size on every sheet, never fitted.
        content = page.get_contents().get_data().decode('latin1')
        assert f'{w} 0 0 {h} ' in content


def test_tiling_page_cap():
    with pytest.raises(ValueError,match='100'):
        tile_layout(3000,3000,'letter')


@pytest.mark.parametrize('fault', ['truncated','shape','range','nan','mask','boxes'])
def test_cache_validation_rejects_invalid_arrays(tmp_path, fault):
    path = tmp_path/'cache.npz'
    arrays=dict(labels=np.zeros((20,30),np.int32), centers=np.zeros((3,3),np.float32), protection=np.zeros((20,30),bool), boxes=np.empty((0,4),np.int32), face_count=0)
    if fault=='shape': arrays['labels']=np.zeros((1,1),np.int32)
    if fault=='range': arrays['labels'][0,0]=3
    if fault=='nan': arrays['centers'][0,0]=np.nan
    if fault=='mask': arrays['protection'][0,0]=True
    if fault=='boxes': arrays['boxes']=np.array([[29,19,2,2]])
    np.savez(path,**arrays)
    if fault=='truncated': path.write_bytes(b'PKbroken')
    assert load_clusters(path,3,(20,30),eyes=True) is None


def test_corrupt_owned_cache_rebuilds_job(tmp_path, params):
    from jobs import JobManager
    manager=JobManager(root=tmp_path)
    try:
        first=manager.start(sample_image(),params)
        done=wait_for(lambda:manager.snapshot(first),lambda s:s['status'] in {'Complete','Error'})
        assert done['status']=='Complete'
        (manager.jobs[first].directory/'clusters.npz').write_bytes(b'truncated')
        second=manager.start(sample_image(),dict(params,canvas_width_cm=40))
        done=wait_for(lambda:manager.snapshot(second),lambda s:s['status'] in {'Complete','Error'})
        assert done['status']=='Complete',done
        assert not done['result']['diagnostics']['reused_color_analysis']
        assert (manager.jobs[first].directory/'clusters.npz').read_bytes()==b'truncated'
    finally:
        manager.close()


def test_benchmark_corrupt_cache_rebuilds(tmp_path):
    from benchmarks.photos import prepare_quantization
    params=dict(k=3,bilateral_diameter=5,sigma_color=40)
    source=np.arange(20*30*3,dtype=np.uint8).reshape(20,30,3)
    a,b=prepare_quantization(source,params,42,tmp_path)
    next(tmp_path.glob('*.npz')).write_bytes(b'broken')
    c,d=prepare_quantization(source,params,42,tmp_path)
    assert np.array_equal(a,c) and np.array_equal(b,d)


def test_portrait_manifest_pinned():
    from pathlib import Path
    sources=json.loads(Path('benchmarks/portrait_sources.json').read_text())
    assert len(sources)>=4
    for item in sources:
        assert len(item['sha256'])==64 and item['download_url'].startswith('https://')
        assert item['license'] and item['author']


def test_cache_rejects_forged_allocation_header(tmp_path, monkeypatch):
    import zipfile
    stream=io.BytesIO()
    np.lib.format.write_array_header_1_0(stream,dict(descr='<i4',fortran_order=False,shape=(2**40,2**40)))
    path=tmp_path/'forged.npz'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('labels.npy',stream.getvalue())
    def forbidden(*args,**kwargs):
        raise AssertionError('Unbounded header reached allocation')
    monkeypatch.setattr(np,'load',forbidden)
    assert load_clusters(path,3) is None
