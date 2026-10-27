"""Compare RGB/Lab clustering and eye protection against a shared photo reference."""
import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from benchmarks import facial_baseline
from benchmarks.photos import MANIFEST, verified_photo, load_photo, detail_metrics
from benchmarks.run import DEFAULTS, quality
from facial import detect_eyes, eye_mask, preserve_eye_pixels
from pipeline import bilateral_filter, quantize_image, extract_contours_and_label
from regions import cleanup_regions

ANNOTATIONS = Path(__file__).with_name('face_annotations.json')
METHODS = {'rgb': ('rgb', False), 'lab': ('lab', False),
           'rgb_eyes': ('rgb', True), 'lab_eyes': ('lab', True)}


def lab_image(image):
    return cv2.cvtColor(image.astype(np.float32)/255.0, cv2.COLOR_BGR2Lab)


def source_edge_metrics(source, preview, mask):
    reference = cv2.Canny(cv2.cvtColor(source, cv2.COLOR_BGR2GRAY), 60, 120)
    output = cv2.Canny(cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY), 60, 120)
    reference = (reference > 0) & mask
    output = (output > 0) & mask
    kernel = np.ones((3, 3), np.uint8)
    matched_ref = reference & (cv2.dilate(output.astype(np.uint8), kernel) > 0)
    matched_out = output & (cv2.dilate(reference.astype(np.uint8), kernel) > 0)
    recall = matched_ref.sum()/reference.sum() if reference.any() else float(not output.any())
    precision = matched_out.sum()/output.sum() if output.any() else float(not reference.any())
    f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.0
    return dict(recall=round(float(recall)*100, 3), precision=round(float(precision)*100, 3),
                f1=round(float(f1)*100, 3))


def annotation_masks(shape, annotation):
    if not annotation:
        return None, None, None
    sx, sy = shape[1]/annotation['size'][0], shape[0]/annotation['size'][1]
    def scale(box):
        x,y,w,h = box
        return [round(x*sx),round(y*sy),max(1,round(w*sx)),max(1,round(h*sy))]
    eyes = eye_mask(shape, [scale(box) for box in annotation['eyes']])
    face = scale(annotation['face'])
    return eyes, eye_mask(shape, [face]), face


def color_metrics(source, preview, eyes=None, face=None):
    error = np.linalg.norm(lab_image(source)-lab_image(preview), axis=2)
    metrics = dict(source_delta_e76=round(float(error.mean()), 4))
    for key, mask in [('eye', eyes), ('face', face)]:
        if mask is not None and mask.any():
            metrics[key+'_delta_e76'] = round(float(error[mask].mean()), 4)
            metrics[key+'_edges'] = source_edge_metrics(source, preview, mask)
    return metrics


def comparison(path, source, outputs, face=None):
    cell = 256
    titles = ['Source'] + list(outputs)
    sheet = Image.new('RGB', (len(titles)*cell, 4*(cell+24)), 'white')
    draw = ImageDraw.Draw(sheet)
    for col, title in enumerate(titles):
        for row in range(4):
            im = source if col == 0 else outputs[title][('preview','raw','preview','template')[row]]
            if row and face is not None:
                x,y,w,h=face
                im=im[y:y+h,x:x+w]
            image=Image.fromarray(cv2.cvtColor(im,cv2.COLOR_BGR2RGB))
            image=ImageOps.contain(image,(cell,cell),Image.Resampling.NEAREST)
            x,y=col*cell,row*(cell+24)
            label = ('clean preview','raw cluster crop','clean crop','template crop')[row]
            draw.text((x+5,y+4),title+' / '+label,fill='black')
            sheet.paste(image,(x+(cell-image.width)//2,y+24))
    sheet.save(path)


def run_case(item, source, params, output, annotation=None, seed=12345):
    started=time.perf_counter()
    boxes, faces = detect_eyes(source)
    detection_seconds=time.perf_counter()-started
    protection=eye_mask(source.shape,boxes)
    eyes, face, face_box=annotation_masks(source.shape,annotation)
    filtered=bilateral_filter(source,params['bilateral_diameter'],params['sigma_color'],params['sigma_color'])
    outputs, metrics, timings = {}, {}, {}
    order=list(METHODS) if seed%2 else list(reversed(METHODS))
    for name in order:
        space, preserve=METHODS[name]
        pixels=filtered.copy()
        if preserve:
            pixels=preserve_eye_pixels(source,filtered,protection,detail=params.get('eye_detail','fine'))
        started=time.perf_counter()
        raw,centers=(facial_baseline.quantize_image(pixels,params['k'],seed) if space == 'rgb'
                     else quantize_image(pixels,params['k'],seed,space))
        quant_seconds=time.perf_counter()-started
        started=time.perf_counter()
        if preserve:
            labels=cleanup_regions(raw,params['k'],params['area_min'],centers,
                                   params['stroke_width'],params['font_scale'],protection)
        else:
            labels=facial_baseline.cleanup_regions(raw,params['k'],params['area_min'],centers,
                                                  params['stroke_width'],params['font_scale'])
        clean_seconds=time.perf_counter()-started
        if preserve:
            template,placements,missing=extract_contours_and_label(labels,params['k'],params['stroke_width'],params['font_scale'],protection)
        else:
            template,placements,missing=facial_baseline.extract_contours_and_label(labels,params['k'],params['stroke_width'],params['font_scale'])
        preview=centers[labels].astype(np.uint8)
        measured=quality(labels,raw,centers,source,placements,missing,params['area_min'])
        measured.update(color_metrics(source,preview,eyes,face))
        # Coverage is still useful, but raw-dependent edge recall is deliberately omitted.
        measured['unnumbered_area_percent']=detail_metrics(labels,raw,centers,placements)['unnumbered_area_percent']
        measured['raw_color_metrics']=color_metrics(source,centers[raw].astype(np.uint8),eyes,face)
        if protection.any():
            measured['detected_eye_pixels_changed_by_cleanup']=int(np.count_nonzero(labels[protection]!=raw[protection]))
        metrics[name]=measured
        timings[name]=dict(quantization_seconds=quant_seconds,cleanup_seconds=clean_seconds)
        outputs[name]=dict(preview=preview,raw=centers[raw].astype(np.uint8),template=template)
    outputs={name:outputs[name] for name in METHODS}
    ident=f'{item["id"]}-{params["k"]}-{seed}'
    comparison(Path(output)/f'{ident}.png',source,outputs,face_box)
    return dict(id=item['id'],category=item['category'],seed=seed,parameters=params,
                width=source.shape[1],height=source.shape[0],eye_boxes=boxes,detected_faces=faces,
                detection_seconds=detection_seconds,metrics=metrics,timings=timings,comparison=f'{ident}.png')


def write_report(output, report):
    output=Path(output)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    lines=['# Facial-detail benchmark', '',
           f"Baseline `{report['baseline_commit']}`; maximum side {report['size']} px; seeds {report['seeds']}; CPU OpenCV {report['opencv']} with two threads. Eye preprocessing: {report.get('eye_detail','fine')}.", '',
           'Common reference: original resized source. Delta E 76 is Euclidean float-Lab error (lower is better). Eye F1 matches Canny 60/120 edges with one-pixel tolerance inside fixed, manually checked regions; it is a proxy, not a semantic accuracy score. Human and cat results must be reported separately: the detector only protects human eyes.', '',
           '| Photo / K / seed | Method | Source DE76 | Eye DE76 | Eye edge F1 % | Unnumbered | Unnumbered area % |',
           '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for case in report['cases']:
        for name in METHODS:
            m=case['metrics'][name]
            label=f"[{case['id']} / {case['parameters']['k']} / {case['seed']}]({case['comparison']})"
            lines.append(f"| {label} | {name} | {m['source_delta_e76']} | {m.get('eye_delta_e76','-')} | {m.get('eye_edges',{}).get('f1','-')} | {m['unnumbered_regions']} | {m['unnumbered_area_percent']} |")
    lines += ['', 'RGB and Lab without protection use frozen 6b657ba cleanup/rendering. Protected variants use mild filtering in detected eyes, preserve their connected components, draw thin eye outlines, and exclude numbers from eye boxes. The rest of the pipeline and palette budget are shared. Raw/pre-cleanup metrics, detected boxes, and per-stage timing samples are in report.json. Image panels show full previews, raw clusters, cleaned face crops, and templates.', '',
              'Photos are checksum-pinned in benchmarks/photo_sources.json. No downloads or external image uploads are performed by this runner. Detection and preprocessing are separate from quantization/cleanup timings; timings are diagnostic single samples per seed, not a speed guarantee.']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--colors',type=int,nargs='+',default=[8,16,32])
    parser.add_argument('--seeds',type=int,nargs='+',default=[12345,42])
    parser.add_argument('--size',type=int,default=512)
    parser.add_argument('--output',type=Path,default=Path('.local/facial-benchmark'))
    parser.add_argument('--eye-detail',choices=['fine','balanced'],default='fine')
    args=parser.parse_args()
    if not 32<=args.size<=2000 or any(not 3<=k<=32 for k in args.colors) or any(not 0<=s<2**31 for s in args.seeds):
        parser.error('Use size 32-2000, colors 3-32, seeds 0 through 2^31-1.')
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)
    args.output.mkdir(parents=True,exist_ok=True)
    annotations=json.loads(ANNOTATIONS.read_text(encoding='utf-8-sig'))['images']
    cases=[]
    for item in json.loads(MANIFEST.read_text(encoding='utf-8')):
        data=verified_photo(item,Path('.local/real-photos'))
        for k in args.colors:
            source=load_photo(data,args.size,k)
            for seed in args.seeds:
                case=run_case(item,source,dict(DEFAULTS,k=k,eye_detail=args.eye_detail),args.output,annotations.get(item['id']),seed)
                cases.append(case)
                print(f"{item['id']} k={k} seed={seed}: DE76 " + ', '.join(f"{name}={m['source_delta_e76']:.3f}" for name,m in case['metrics'].items()),flush=True)
                report=dict(baseline_commit='6b657ba2cc783edc22cd084126347b52fd274e7c',size=args.size,seeds=args.seeds,eye_detail=args.eye_detail,
                            opencv=cv2.__version__,python=platform.python_version(),annotations=annotations,cases=cases)
                write_report(args.output,report)


if __name__=='__main__':
    main()
