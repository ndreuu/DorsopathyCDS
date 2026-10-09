"""Loopback HTTP transport for the module platform; no imaging imports."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .api_contracts import canonical_bytes, opaque_id
from .contracts import loads_json, ContractError
from .registry import RegistryError
from .storage import StorageError, ConflictError
from .uploads import UploadError

_JSON_LIMIT = 1024 * 1024

class LimitedBody:
    """HTTP Content-Length bounds the upload without probing for connection EOF."""
    def __init__(self, stream, length): self.stream, self.remaining = stream, length
    def read(self, size=-1):
        if not self.remaining: return b''
        count=self.remaining if size<0 else min(size,self.remaining)
        data=self.stream.read(count); self.remaining-=len(data)
        return data

class PlatformServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, application):
        self.application = application
        super().__init__(address, Handler)

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def _respond(self, code, data, media_type='application/json; charset=utf-8'):
        body = canonical_bytes(data) if media_type.startswith('application/json') else data
        self.send_response(code)
        self.send_header('Content-Type', media_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _length(self, limit):
        values = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(values) != 1 or not re.fullmatch(r'\d+', values[0]):
            raise ContractError('A single Content-Length is required')
        length = int(values[0])
        if length > limit:
            raise OverflowError('Body exceeds configured limit')
        return length

    def _json(self):
        if self.headers.get_content_type() != 'application/json':
            raise ContractError('JSON body required')
        length = self._length(_JSON_LIMIT)
        data = self.rfile.read(length)
        if len(data) != length:
            raise ContractError('Incomplete JSON body')
        return loads_json(data)

    def do_GET(self):
        self._route(False)

    def do_POST(self):
        self._route(True)

    def _route(self, posting):
        self.connection.settimeout(30)
        app = self.server.application
        try:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment:
                return self._respond(404, {'error':'unknown_route'})
            path = parsed.path
            pieces = path.strip('/').split('/')
            if not posting and path == '/':
                page = (Path(__file__).parent/'web/index.html').read_bytes()
                return self._respond(200, page, 'text/html; charset=utf-8')
            if not posting and path == '/health':
                return self._respond(200, {'status':'ok','mode':'research_only','schema_version':'1.0'})
            if not posting and path == '/modules':
                return self._respond(200, app.modules())
            if not posting and path == '/demo-inputs':
                from .demo_flow import demo_inputs
                return self._respond(200,demo_inputs())
            if posting and path == '/episodes':
                data = self._json()
                if type(data) is not dict or set(data)-{'episode_id'}:
                    raise ContractError('Invalid episode envelope')
                return self._respond(201, app.create_episode(data.get('episode_id')))
            if len(pieces)==3 and pieces[0]=='episodes' and pieces[2]=='artifacts' and posting:
                episode = opaque_id(pieces[1])
                length = self._length(app.uploads.max_bytes)
                media = self.headers.get('Content-Type','application/octet-stream')
                ref = app.upload(episode,LimitedBody(self.rfile,length),media,length)
                return self._respond(201,ref)
            if len(pieces)==4 and pieces[0]=='episodes' and pieces[2]=='artifacts' and not posting:
                ref,stream = app.open_artifact(opaque_id(pieces[1]),opaque_id(pieces[3]))
                with stream:
                    self.send_response(200)
                    self.send_header('Content-Type',ref['media_type'])
                    self.send_header('Content-Length',str(ref['size_bytes']))
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.send_header('Content-Disposition','inline' if ref['media_type'] in {'image/png','image/jpeg','image/webp'} else 'attachment')
                    self.send_header('Content-Security-Policy','sandbox')
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Connection','close'); self.end_headers(); self.close_connection=True
                    while True:
                        chunk = stream.read(1024*1024)
                        if not chunk: break
                        self.wfile.write(chunk)
                return
            if len(pieces)==3 and pieces[0]=='episodes' and pieces[2]=='runs' and posting:
                episode = opaque_id(pieces[1]); manifest = self._json()
                if type(manifest) is not dict or manifest.get('episode_id')!=episode:
                    raise ContractError('Manifest episode mismatch')
                keys=self.headers.get_all('Idempotency-Key',[])
                if len(keys)!=1: raise ContractError('Idempotency-Key required')
                return self._respond(202,app.start_run(manifest,keys[0]))
            if not posting and len(pieces)==2:
                methods={'runs':app.get_run,'jobs':app.get_job,'results':app.get_result,'snapshots':app.get_snapshot}
                if pieces[0] in methods:
                    return self._respond(200,methods[pieces[0]](opaque_id(pieces[1])))
            if not posting and len(pieces)==3 and pieces[0]=='runs':
                report=app.get_report(opaque_id(pieces[1]))
                if pieces[2]=='report': return self._respond(200,report)
                if pieces[2]=='report.html':
                    from dorsopathy_cds.episode_report import render_html
                    return self._respond(200,render_html(report).encode('utf-8'),'text/html; charset=utf-8')
            return self._respond(404,{'error':'unknown_route'})
        except ConflictError:
            self._respond(409,{'error':'conflict'})
        except OverflowError:
            self._respond(413,{'error':'payload_too_large'})
        except UploadError as exc:
            self._respond(413 if getattr(exc,'code',None)=='upload_too_large' else 400,{'error':'invalid_upload'})
        except (ContractError,RegistryError,TypeError):
            self._respond(400,{'error':'invalid_request'})
        except StorageError:
            self._respond(404,{'error':'record_unavailable'})
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True
        except Exception:
            self._respond(500,{'error':'internal_error'})

def create_server(application,host='127.0.0.1',port=8766):
    try:
        if not ipaddress.ip_address(host).is_loopback: raise ValueError
    except ValueError:
        raise ValueError('Platform HTTP requires a numeric loopback address') from None
    if type(port) is not int or not 0<=port<=65535:
        raise ValueError('Invalid server port')
    if ':' in host:
        import socket
        class IPv6PlatformServer(PlatformServer): address_family=socket.AF_INET6
        return IPv6PlatformServer((host,port),application)
    return PlatformServer((host,port),application)
