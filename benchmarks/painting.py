"""Paired physical-paintability evaluation; cached photos, no production annotations."""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from benchmarks.photos import verified_photo, load_photo, detail_metrics
from benchmarks.faces import annotation_masks, color_metrics, comparison, source_edge_metrics
from canvas_size import painting_geometry, canvas_metadata
from facial import detect_eyes, eye_mask, preserve_eye_pixels
from pipeline import quantize_image, extract_contours_and_label, bilateral_filter
from regions import cleanup_regions
from settings import PRESETS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size',type=int,default=512)
    parser.add_argument('--seed',type=int,default=12345)
    parser.add_argument('--widths',type=float,nargs='+',default=[20,40])
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('.local/painting-benchmark'))
    parser.add_argument('--eye-detail',choices=['fine','balanced'],default='fine')
    args=parser.parse_args()
    if not 32<=args.size<=2000 or not 0<=args.seed<2**31 or any(not 5<=w<=100 for w in args.widths):
        parser.error('Use size 32-2000 and a nonnegative 31-bit seed.')
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)
    args.output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).parent
    items=json.loads((root/'photo_sources.json').read_text())+json.loads((root/'portrait_sources.json').read_text())
    annotations=json.loads((root/'face_annotations.json').read_text(encoding='utf-8-sig'))['images']
    annotations.update(json.loads((root/'portrait_annotations.json').read_text())['images'])
    cases=[]
    for item in items:
        source=load_photo(verified_photo(item,Path('.local/real-photos'),download=args.download),args.size,24)
        boxes,faces=detect_eyes(source)
        protected=eye_mask(source.shape,boxes)
        annotation=annotations.get(item['id'])
        eyes,face,face_box=annotation_masks(source.shape,annotation)
        for preset,base_params in PRESETS.items():
            params=dict(base_params,eye_detail=args.eye_detail)
            filtered=preserve_eye_pixels(source,bilateral_filter(source,params['bilateral_diameter'],params['sigma_color'],params['sigma_color']),protected,detail=args.eye_detail)
            raw,centers=quantize_image(filtered,params['k'],args.seed,'lab')
            for width in args.widths:
                methods={}
                outputs={}
                for mode in ('pixels','physical'):
                    options=dict(params,canvas_width_cm=width,sizing_mode=mode)
                    geometry=painting_geometry(source.shape[1],source.shape[0],options)
                    start=time.perf_counter()
                    labels=cleanup_regions(raw,params['k'],centers=centers,protected_mask=protected,**geometry)
                    template,placements,missing=extract_contours_and_label(labels,params['k'],geometry['stroke_width'],geometry['font_scale'],protected,minimum_font_scale=geometry['minimum_font_scale'])
                    elapsed=time.perf_counter()-start
                    preview=centers[labels].astype(np.uint8)
                    metrics=detail_metrics(labels,raw,centers,placements)
                    metrics.update(color_metrics(source,preview,eyes,face))
                    metrics.update(unnumbered_regions=missing,protected_pixels_changed=int(np.count_nonzero(labels[protected]!=raw[protected])),seconds=elapsed)
                    metrics['canvas']=canvas_metadata(source.shape[1],source.shape[0],{**options,'stroke_width':geometry['stroke_width']},placements)
                    for feature in ('nose','mouth'):
                        if annotation and feature in annotation:
                            sx,sy=source.shape[1]/annotation['size'][0],source.shape[0]/annotation['size'][1]
                            mask=eye_mask(source.shape,[[round(x*sx),round(y*sy),max(1,round(w*sx)),max(1,round(h*sy))] for x,y,w,h in annotation[feature]])
                            metrics[feature+'_edges']=source_edge_metrics(source,preview,mask)
                    assert metrics['protected_pixels_changed']==0
                    if mode=='physical' and placements:
                        assert metrics['canvas']['minimum_number_height_mm']>=1.999
                    methods[mode]=metrics
                    outputs[mode]=dict(preview=preview,raw=centers[raw].astype(np.uint8),template=template)
                ident=f"{item['id']}-{preset}-{width}cm"
                comparison(args.output/(ident+'.png'),source,outputs,face_box)
                cases.append(dict(id=item['id'],category=item['category'],preset=preset,width_cm=width,detected_faces=faces,eye_boxes=boxes,metrics=methods))
                print(ident,methods['pixels']['unnumbered_regions'],'->',methods['physical']['unnumbered_regions'],flush=True)
        report=dict(baseline='1.2 pixel-mode geometry (04cf37a); identical Lab quantization per pair',size=args.size,seed=args.seed,eye_detail=args.eye_detail,cases=cases)
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
