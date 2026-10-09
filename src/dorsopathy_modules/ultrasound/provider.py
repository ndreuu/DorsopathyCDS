"""Manual 2D line/contour measurements inside an explicit calibration region."""
import base64
import math
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope
from ._png import decode_png
from ._support import COMMON_FIELDS, MAX_SOURCE_BYTES, add_json, author, calibration, common, line, metadata, number, observation, png, read_source, require


def _cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _intersect(a,b,c,d):
    first,second,third,fourth=_cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b)
    def within(p,q,r): return min(p[0],q[0])-1e-12<=r[0]<=max(p[0],q[0])+1e-12 and min(p[1],q[1])-1e-12<=r[1]<=max(p[1],q[1])+1e-12
    return first*second<0 and third*fourth<0 or any(abs(v)<1e-12 and within(p,q,r) for v,p,q,r in ((first,a,b,c),(second,a,b,d),(third,c,d,a),(fourth,c,d,b)))


def _polygon(points):
    if len(points)<3 or len(points)>1024 or len(set(map(tuple,points)))!=len(points):
        raise ContractError("Ultrasound contour requires unique simple polygon vertices")
    for i in range(len(points)):
        for j in range(i+1,len(points)):
            if j==i+1 or i==0 and j==len(points)-1: continue
            if _intersect(points[i],points[(i+1)%len(points)],points[j],points[(j+1)%len(points)]):
                raise ContractError("Ultrasound contour cannot self-intersect")
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1])))/2


class UltrasoundProvider:
    spec=ProviderSpec("ultrasound","0.1.0","Calibrated raster and manual ultrasound measurements")

    def analyze(self,request,context):
        request,data,source=read_source(request,context,self.spec)
        require(data,COMMON_FIELDS|{'frame_artifact_id','width','height','frame_index','annotations'})
        common(data,'ultrasound-manual.v1',2)
        cal=calibration(data,{'mm_per_pixel_x','mm_per_pixel_y','region'})
        if type(data['frame_index']) is not int or data['frame_index']!=0:
            raise ContractError('Initial raster profile supports exactly frame zero')
        references={ref['artifact_id']:ref for ref in request['input_artifacts']}
        frame=references.get(data['frame_artifact_id'])
        if len(references)!=2 or frame is None or frame['role']!='source' or frame['media_type']!='image/png' or frame['size_bytes']>MAX_SOURCE_BYTES:
            raise ContractError('Ultrasound profile requires its bounded PNG source frame')
        with context.artifacts.open(frame) as stream: raw=stream.read(MAX_SOURCE_BYTES+1)
        if len(raw)>MAX_SOURCE_BYTES: raise ContractError('Ultrasound PNG exceeds supported size')
        width,height,rows=decode_png(raw)
        if type(data['width']) is not int or type(data['height']) is not int or (data['width'],data['height'])!=(width,height):
            raise ContractError('Declared ultrasound dimensions must match the original frame')
        sx=None if cal['mm_per_pixel_x'] is None else number(cal['mm_per_pixel_x'],positive=True)
        sy=None if cal['mm_per_pixel_y'] is None else number(cal['mm_per_pixel_y'],positive=True)
        region=cal['region']
        if region is not None:
            if type(region) is not list or len(region)!=4: raise ContractError('Calibration region requires x0,y0,x1,y1')
            region=[number(value) for value in region]
            if not 0<=region[0]<=region[2]<width or not 0<=region[1]<=region[3]<height:
                raise ContractError('Calibration region lies outside its source frame')
        calibrated=cal['status']=='verified' and sx is not None and sy is not None and region is not None
        if cal['status']=='verified' and not calibrated: raise ContractError('Verified ultrasound calibration requires both scales and a region')
        annotations=require(data['annotations'],{'author','distances','areas'});author(annotations['author'])
        if any(type(annotations[key]) is not list for key in ('distances','areas')) or not annotations['distances'] and not annotations['areas']:
            raise ContractError('Ultrasound requires authored line or contour annotations')
        measurements=[];ids=set();svg=[]
        for kind,key in (('distance','distances'),('area','areas')):
            for item in annotations[key]:
                require(item,{'id','points' if kind=='distance' else 'polygon'});opaque_id(item['id'])
                if item['id'] in ids: raise ContractError('Ultrasound annotation IDs must be unique')
                ids.add(item['id']); points=item['points' if kind=='distance' else 'polygon']
                if type(points) is not list or kind=='distance' and len(points)!=2:
                    raise ContractError('Ultrasound measurement points are unsupported')
                for point in points:
                    if type(point) is not list or len(point)!=2: raise ContractError('Ultrasound points require x/y coordinates')
                    x,y=map(number,point)
                    if not 0<=x<width or not 0<=y<height: raise ContractError('Ultrasound point is outside its source frame')
                raw_area=_polygon(points) if kind=='area' else None
                inside=region is not None and all(region[0]<=point[0]<=region[2] and region[1]<=point[1]<=region[3] for point in points)
                reason='unknown_or_invalid_calibration' if not calibrated else 'outside_calibration_region' if not inside else 'degenerate_polygon' if kind=='area' and raw_area==0 else None
                value=None if reason else math.hypot((points[1][0]-points[0][0])*sx,(points[1][1]-points[0][1])*sy) if kind=='distance' else raw_area*sx*sy
                measurements.append({'id':item['id'],'kind':kind,'points_pixels':points,'value':value,'unit':'mm' if kind=='distance' else 'mm2','reason':reason,'annotation_method':'manual'})
                edges=list(zip(points,points[1:]+points[:1])) if kind=='area' else [(points[0],points[1])]
                for a,b in edges: line(rows,a,b,(255,255,0) if kind=='area' else (0,255,255))
                tag='polygon' if kind=='area' else 'polyline'
                svg.append('<'+tag+' points="'+' '.join(f'{point[0]},{point[1]}' for point in points)+'" fill="none" stroke="'+('#ffff00' if kind=='area' else '#00ffff')+'" stroke-width="0.1"/>')
        annotation_ref=add_json(context,{'schema_version':'1.0','source_kind':'ultrasound.annotations.v1','frame_artifact_id':frame['artifact_id'],**annotations})
        overlay='<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 '+str(width)+' '+str(height)+'"><image width="'+str(width)+'" height="'+str(height)+'" href="data:image/png;base64,'+base64.b64encode(raw).decode()+'"/>'+''.join(svg)+'</svg>'
        preview_refs=[context.artifacts.add_bytes(overlay.encode(),'image/svg+xml',role='derived'),context.artifacts.add_bytes(png(width,height,rows),'image/png',role='derived')]
        summary={'schema_version':'1.0','source_kind':'ultrasound.measurements.v1','source_artifact_id':source['artifact_id'],'frame_artifact_id':frame['artifact_id'],
                 'annotations_artifact_id':annotation_ref['artifact_id'],'width':width,'height':height,'frame_index':0,'pose':data['pose'],'acquired_at':data['acquired_at'],
                 'calibration':cal,'measurements':measurements,'comparison_metadata':metadata(data,source,'px',cal['status'])}
        measurement_ref=add_json(context,summary)
        observations=[]
        for item in measurements:
            evidence=[{'artifact_id':frame['artifact_id'],'selector':{'kind':'image','frame_index':0,'coordinate_frame':data['coordinate_frame']}},
                      {'artifact_id':source['artifact_id'],'selector':{'kind':'json','pointer':'/annotations'}},
                      {'artifact_id':measurement_ref['artifact_id'],'selector':{'kind':'measurement','measurement_id':item['id'],'frame_index':0}},
                      {'artifact_id':preview_refs[1]['artifact_id'],'selector':{'kind':'image','frame_index':0,'coordinate_frame':data['coordinate_frame']}}]
            observations.append(observation(request,data,'ultrasound.'+item['kind'],item['id'],item['value'],item['unit'],'unreliable' if cal['status']=='invalid' else 'usable' if item['value'] is not None else 'unknown',
                                            'manual_'+('line_anisotropic_distance_v1' if item['kind']=='distance' else 'simple_polygon_shoelace_anisotropic_area_v1'),evidence))
        partial=any(item['value'] is None for item in measurements)
        warnings=[{'code':'ultrasound_measurement_incomplete','message':'Some manual measurements lack valid regional calibration; no pathology detector is implemented.'}] if partial else []
        return validate_result(result_envelope(request,observations,[source,frame,annotation_ref,*preview_refs,measurement_ref],'partial' if partial else 'complete',warnings),request)


def calibration_from_dicom_regions(regions,width,height):
    """Map one already-decoded DICOM Cartesian tissue region; no pixel decoder."""
    if len(regions)!=1: raise ContractError('Only one unambiguous ultrasound calibration region is supported')
    item=regions[0]
    if any(getattr(item,key,None)!=expected for key,expected in (('RegionSpatialFormat',1),('RegionDataType',1),('PhysicalUnitsXDirection',3),('PhysicalUnitsYDirection',3))):
        raise ContractError('DICOM ultrasound region must be 2D tissue with centimetre X/Y units')
    dx=number(getattr(item,'PhysicalDeltaX',None));dy=number(getattr(item,'PhysicalDeltaY',None))
    bounds=[getattr(item,key,None) for key in ('RegionLocationMinX0','RegionLocationMinY0','RegionLocationMaxX1','RegionLocationMaxY1')]
    if dx==0 or dy==0 or any(type(value) is not int for value in bounds) or not 0<=bounds[0]<=bounds[2]<width or not 0<=bounds[1]<=bounds[3]<height:
        raise ContractError('DICOM ultrasound region calibration is invalid')
    return {'status':'verified','mm_per_pixel_x':abs(dx)*10,'mm_per_pixel_y':abs(dy)*10,'region':bounds,'source':'DICOM-US-Region-Calibration','author_id':'dicom-header'}
