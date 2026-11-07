"""BROWSER.GO1 on the PC (apps/browser/host_test.c) against a local HTTP server:
compressed pages, cookies, forms sent by GET and POST."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import gzip
import subprocess
import threading
import unittest
import zlib

from emulator.board import ROOT

BEARSSL = ROOT/'analysis/toolchains/bearssl-src/bearssl-0.6/inc/bearssl.h'
CERTIFICATES = ROOT/'analysis/toolchains/cacert.pem'

FORM = '''<html><head><title>Form</title></head><body>
<form action="/echo" method="get">
<input type="text" name="q" value="old">
<input type="hidden" name="token" value="t&amp;1">
<input type="checkbox" name="safe" checked> safe
<input type="checkbox" name="extra" value="yes"> extra
<input type="radio" name="size" value="s" checked> small
<input type="radio" name="size" value="l"> large
<select name="lang"><option value="ru">Русский<option value="en" selected>English</select>
<textarea name="note">first line</textarea>
<input type="submit" name="go" value="Find">
</form>
<form action="/echo" method="post"><input name="login" value="me"><input type="password" name="pw"><button>Sign in</button></form>
<form action="/echo"><input name="alone"></form>
</body></html>'''

LONG = ''.join(f'<p>Line {i}: ' + 'word ' * (i % 17) + '</p>' for i in range(600))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def reply(self, body, headers=(), code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        accept = self.headers.get('Accept-Encoding', '')
        if path == '/gzip':
            self.reply(gzip.compress(LONG.encode()), [('Content-Encoding', 'gzip')] if 'gzip' in accept else [])
        elif path == '/deflate':
            self.reply(zlib.compress(b'<p>zlib deflate ok</p>'), [('Content-Encoding', 'deflate')])
        elif path == '/raw-deflate':
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            self.reply(c.compress(b'<p>raw deflate ok</p>')+c.flush(), [('Content-Encoding', 'deflate')])
        elif path == '/set':
            self.reply(b'<p>set</p>', [('Set-Cookie', 'a=1; Path=/'), ('Set-Cookie', 'b=two; Max-Age=3600; HttpOnly'),
                                      ('Set-Cookie', 'c=3; Path=/other'), ('Set-Cookie', 'd=4; Domain=evil.example')])
        elif path == '/drop':
            self.reply(b'<p>drop</p>', [('Set-Cookie', 'a=; Max-Age=0'), ('Set-Cookie', 'b=x; Expires=Thu, 01 Jan 1970 00:00:00 GMT')])
        elif path == '/show':
            self.reply(f'<p>cookie [{self.headers.get("Cookie", "")}]</p>'.encode())
        elif path == '/form':
            self.reply(FORM.encode())
        elif path.startswith('/echo'):
            self.reply(f'<p>GET {path}</p>'.encode())
        else:
            self.reply(b'<p>?</p>', code=404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(length).decode()
        form = self.headers.get('Content-Type') == 'application/x-www-form-urlencoded'
        self.reply(f'<p>POST {"form" if form else "?"} [{data}]</p>'.encode())

    def log_message(self, *args):
        pass


@unittest.skipUnless(BEARSSL.exists() and CERTIFICATES.exists(), 'BearSSL and cacert.pem required')
class BrowserHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.build_browser import build_host
        cls.exe = build_host()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_browser(self, *commands):
        result = subprocess.run([str(self.exe), *commands], capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode('utf-8').replace('\r\n', '\n').split('--\n')

    def test_gzip_page_is_inflated_whole(self):
        out = self.run_browser('load', self.base+'/gzip', 'page', 'request')
        self.assertIn('Accept-Encoding: gzip, deflate', out[2])
        text = ' '.join(out[1].split())  # lines wrap at the screen width
        self.assertIn('Line 0:', text)
        self.assertTrue(text.endswith('Line 599: word word word word'), text[-80:])  # 599 % 17 words

    def test_deflate_zlib_and_raw(self):
        out = self.run_browser('load', self.base+'/deflate', 'page', 'load', self.base+'/raw-deflate', 'page')
        self.assertIn('zlib deflate ok', out[1])
        self.assertIn('raw deflate ok', out[3])

    def test_cookies_follow_path_domain_and_removal(self):
        out = self.run_browser('load', self.base+'/set', 'cookies', 'load', self.base+'/show', 'page',
                               'load', self.base+'/other/x', 'request', 'load', self.base+'/drop', 'cookies')
        jar = out[1]
        self.assertIn('a=1 domain=127.0.0.1 path=/ host', jar)
        self.assertIn('b=two domain=127.0.0.1 path=/ host', jar)
        self.assertIn('c=3 domain=127.0.0.1 path=/other host', jar)
        self.assertNotIn('d=4', jar)  # another site's domain
        self.assertIn('cookie [a=1; b=two]', out[3])
        self.assertIn('Cookie: a=1; b=two; c=3', out[5])
        self.assertNotIn('a=1', out[7])
        self.assertNotIn('b=', out[7])
        self.assertIn('c=3', out[7])

    def test_form_fields_and_get_submission(self):
        out = self.run_browser('load', self.base+'/form', 'page')
        page = out[1]
        self.assertIn('{1:old}', page)
        self.assertIn('{2:x} safe', page)
        self.assertIn('{3: } extra', page)
        self.assertIn('{4:x} small', page)
        self.assertIn('{6:English}', page)
        self.assertIn('{7:first line}', page)
        self.assertIn('[8][Find]', page)
        # edit the text, tick extra, choose large, next option (wraps to Русский), send
        out = self.run_browser('load', self.base+'/form', 'set', '1', 'new value+1', 'ok', '3', 'ok', '5', 'ok', '6',
                               'ok', '8', 'page')
        self.assertIn('GET/echo?q=new+value%2B1&token=t%261&safe=on&extra=yes&size=l&lang=ru&note=first+line&go=Find',
                      ''.join(out[6].split()))  # the long line wraps

    def test_post_form_and_form_without_button(self):
        out = self.run_browser('load', self.base+'/form', 'set', '9', 'me@x', 'set', '10', 'p w', 'ok', '11', 'page', 'request')
        self.assertIn('POST form [login=me%40x&pw=p+w]', ' '.join(out[4].split()))
        self.assertIn('POST /echo HTTP/1.1', out[5])
        out = self.run_browser('load', self.base+'/form', 'page')
        self.assertIn('{12:}', out[1])
        self.assertIn('[13][Отправить]', out[1])
        out = self.run_browser('load', self.base+'/form', 'set', '12', 'solo', 'ok', '13', 'page')
        self.assertIn('GET /echo?alone=solo', out[3])


    def test_russian_multitap_in_a_field_and_a_search(self):
        # field 1: OK opens the editor, up clears, ## to Russian; привет: 5555 6 4 222 33 666 (> ends a letter)
        out = self.run_browser('load', self.base+'/form', 'ok', '1', 'keys', '^##5555>6>4>222>33>666', 'edit',
                               'keys', 'L', 'ok', '8', 'page')
        self.assertEqual(out[3].strip(), 'привет')
        self.assertIn('q=%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82', ''.join(out[6].split()))
        # # cycles abc -> ABC -> абв -> АБВ -> 123: mixed text; Ё is the capital of ё (3: д е ё)
        out = self.run_browser('load', self.base+'/form', 'ok', '1', 'keys', '^2#2#2#2#2#2###333', 'edit')
        self.assertEqual(out[3].strip(), 'aAаА2aЁ')


if __name__ == '__main__':
    unittest.main()
