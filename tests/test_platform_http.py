import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from dorsopathy_platform.application import Application
from dorsopathy_platform.dispatcher import Dispatcher
from dorsopathy_platform.http import create_server
from dorsopathy_platform.registry import ModuleRegistry
from dorsopathy_platform.storage import SqliteRepository,ArtifactStore

class PlatformHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        repo=SqliteRepository(root);blobs=ArtifactStore(root);registry=ModuleRegistry()
        self.app=Application(repo,blobs,registry,Dispatcher(repo,blobs,registry),lambda *args:{},max_upload_bytes=300000)
        self.app.create_episode('a');self.app.create_episode('b')
        self.server=create_server(self.app,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.close();self.temp.cleanup()
    def req(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path,body=body,headers=headers or {})
            response=conn.getresponse();data=response.read()
            return response.status,dict(response.getheaders()),data
        finally:conn.close()
    def test_binary_upload_and_scoped_verified_download(self):
        payload=bytes(range(256))*600
        status,_,body=self.req('POST','/episodes/a/artifacts',payload,{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,201);ref=json.loads(body)
        self.assertEqual(ref['sha256'],hashlib.sha256(payload).hexdigest())
        status,headers,download=self.req('GET','/episodes/a/artifacts/'+ref['artifact_id'])
        self.assertEqual(status,200);self.assertEqual(download,payload)
        self.assertEqual(headers['Content-Disposition'],'attachment')
        self.assertEqual(self.req('GET','/episodes/b/artifacts/'+ref['artifact_id'])[0],404)
    def test_limits_json_types_and_loopback_binding(self):
        self.assertEqual(self.req('POST','/episodes/a/artifacts',b'x',{'Content-Type':'application/octet-stream','Content-Length':'300001'})[0],413)
        self.assertEqual(self.req('POST','/episodes',b'[]',{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.req('POST','/episodes',b'{"episode_id":"../bad"}',{'Content-Type':'application/json'})[0],400)
        with self.assertRaises(ValueError):create_server(self.app,host='0.0.0.0',port=0)
    def test_page_and_health_are_available_without_imaging_imports(self):
        status,_,body=self.req('GET','/');self.assertEqual(status,200)
        self.assertIn('Запустить пример всех модулей'.encode(),body)
        self.assertEqual(json.loads(self.req('GET','/health')[2])['mode'],'research_only')
        self.assertEqual(self.req('GET','/not-a-route')[0],404)
