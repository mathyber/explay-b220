"""Small loopback-only HTTP server used to verify the original phone browser."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import secrets
import threading


class HttpFixture:
    def __init__(self):
        self.token = 'B220-'+secrets.token_hex(3).upper()
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def do_GET(self):
                body = (f'<html><head><title>B220 network test</title></head>'
                        f'<body><h1>B220 NETWORK OK</h1><p>{fixture.token}</p>'
                        f'<p>Page: {self.path}</p><a href="/next">Next page</a></body></html>').encode('ascii')
                if len(fixture.requests) < 128:
                    fixture.requests.append(dict(method='GET',path=self.path,
                                                  host=self.headers.get('Host'),token=fixture.token,
                                                  response_body=body.decode('ascii')))
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=us-ascii')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self,*args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs=dict(poll_interval=0.05),daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return self.server.server_address

    def report(self):
        return dict(endpoint=list(self.endpoint),token=self.token,requests=list(self.requests))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
