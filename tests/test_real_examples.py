import copy
import json
from pathlib import Path
import tempfile
import unittest
from dorsopathy_platform.real_examples import read_catalog,render_catalog

class FakeApplication:
    def __init__(self):
        self.run={'episode_id':'ep-a','manifest':{'linkage':'unlinked'},'snapshot_id':'snap-a'}
        self.result={'module_id':'ultrasound','status':'complete','artifacts':[{'artifact_id':'image-a','media_type':'image/png'}],
          'observations':[{'concept':'ultrasound.distance','assessment_status':'assessed','value':7.35,'unit':'mm','origin':'automatically_measured','anatomy':{'level':None,'side':'unknown'}}]}
    def get_run(self,rid):return self.run
    def get_snapshot(self,sid):return {'results':[self.result]}

class RealExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'catalog.json'
        self.data={'schema_version':'1.0','examples':[{'id':'example-a','title':'<script>alert(1)</script>','description':'Real input','module_id':'ultrasound','episode_id':'ep-a','run_id':'run-a','source':{'title':'Source','url':'https://example.org/source','license':'CC BY 4.0'},'notes':['manual ROI'],'preview_artifact_ids':['image-a']}],'unavailable':[]}
    def tearDown(self):self.temp.cleanup()
    def read(self):self.path.write_text(json.dumps(self.data));return read_catalog(self.path)
    def test_source_and_text_are_escaped_and_measurements_come_from_snapshot(self):
        data=self.read();app=FakeApplication();page=render_catalog(data,app).decode()
        self.assertNotIn('<script>alert',page);self.assertIn('&lt;script&gt;',page)
        self.assertIn('7,35 мм',page);self.assertIn('/episodes/ep-a/artifacts/image-a',page)
        app.result['observations'][0]['value']=9.5
        self.assertIn('9,5 мм',render_catalog(data,app).decode())
    def test_cross_episode_and_linked_cases_cannot_appear_as_public_examples(self):
        for field,value in [('episode_id','ep-other'),('linkage','verified_linked')]:
            app=FakeApplication()
            if field=='episode_id':app.run[field]=value
            else:app.run['manifest'][field]=value
            with self.assertRaises(ValueError):render_catalog(self.read(),app)
    def test_synthetic_values_and_other_result_previews_are_rejected(self):
        app=FakeApplication();app.result['observations'][0]['origin']='synthetic_demo'
        with self.assertRaises(ValueError):render_catalog(self.read(),app)
        app=FakeApplication();self.data['examples'][0]['preview_artifact_ids']=['other-result-image']
        with self.assertRaises(ValueError):render_catalog(self.read(),app)
    def test_executable_source_links_duplicate_ids_and_symlinks_are_rejected(self):
        for url in ['javascript:alert(1)','http://example.org/source','https://user:password@example.org/source']:
            self.data['examples'][0]['source']['url']=url
            with self.assertRaises(ValueError):self.read()
        self.data['examples'][0]['source']['url']='https://example.org/source'
        self.data['examples'].append(copy.deepcopy(self.data['examples'][0]))
        with self.assertRaises(ValueError):self.read()
        self.data['examples'].pop();self.read()
        link=self.path.with_name('link.json');link.symlink_to(self.path)
        with self.assertRaises(ValueError):read_catalog(link)
