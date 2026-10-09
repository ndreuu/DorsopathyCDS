"""Import an explicitly selected local MRI replay bundle; never from HTTP paths."""
import hashlib
from pathlib import Path
import uuid

from .api_contracts import default_manifest, opaque_id
from .contracts import loads_json

def start_bundle(application,directory):
    root=Path(directory).resolve(strict=True)
    manifest_path=root/'upload-manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size>1024*1024:
        raise ValueError('Invalid bundle manifest')
    manifest=loads_json(manifest_path.read_bytes())
    fields={'schema_version','processing','first_artifact_id','files','linkage','clinical_use'}
    if type(manifest) is not dict or set(manifest)!=fields or manifest['schema_version']!='1.0' or manifest['processing']!='replay' or manifest['linkage']!='unlinked' or manifest['clinical_use']!='research_only':
        raise ValueError('Unsupported replay bundle')
    files=manifest['files']
    if type(files) is not list or not files or len(files)>1000:
        raise ValueError('Invalid bundle files')
    checked=[]; seen=set()
    for item in files:
        if type(item) is not dict or set(item)!={'artifact_id','relative_path','media_type','sha256','size_bytes'}:
            raise ValueError('Invalid bundle file entry')
        aid=opaque_id(item['artifact_id']); relative=Path(item['relative_path'])
        if aid in seen or relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Invalid bundle file path')
        candidate=root/relative
        if any(parent.is_symlink() for parent in [candidate,*list(candidate.parents)[:len(relative.parts)-1]]):
            raise ValueError('Bundle symlinks are unsupported')
        path=candidate.resolve(strict=True)
        if root not in path.parents or not path.is_file() or type(item['size_bytes']) is not int or item['size_bytes']<0 or path.stat().st_size!=item['size_bytes']:
            raise ValueError('Invalid bundle size')
        sha=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):sha.update(chunk)
        if sha.hexdigest()!=item['sha256'] or aid!='artifact-'+sha.hexdigest():
            raise ValueError('Bundle content hash mismatch')
        seen.add(aid);checked.append((item,path))
    if files[0]['artifact_id']!=manifest['first_artifact_id'] or files[0]['media_type']!='application/json':
        raise ValueError('MRI source JSON must be first')
    episode=application.create_episode()['episode_id'];ids=[]
    for item,path in checked:
        with path.open('rb') as stream:
            ref=application.upload(episode,stream,item['media_type'],item['size_bytes'])
        if ref['artifact_id']!=item['artifact_id']:raise ValueError('Imported content changed')
        ids.append(ref['artifact_id'])
    flow=default_manifest(episode,[{'module_id':'mri.anatomy','module_version':'0.1.0',
        'input_artifact_ids':ids,'parameters':{'processing_mode':'replay'}}],mode='replay',linkage='unlinked')
    return application.start_run(flow,'bundle-'+uuid.uuid4().hex)
