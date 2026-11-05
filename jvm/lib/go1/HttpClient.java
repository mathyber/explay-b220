package go1;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Vector;
import javax.microedition.io.HttpConnection;

/* http:// for javax.microedition.io: HTTP/1.1 with "Connection: close" over
   TcpConnection. The request goes out when the response is first needed;
   bodies may be sized, chunked or end with the connection. As in MIDP,
   redirects are left to the application. */
public class HttpClient implements HttpConnection {
    private final String url, host, file, ref, query;
    private final int port;
    private String method = GET;
    private final Vector requestKeys = new Vector(), requestValues = new Vector();
    private final Vector keys = new Vector(), values = new Vector();
    private ByteArrayOutputStream body;
    private TcpConnection tcp;
    private int code = -1;
    private String message = "";
    private Body input;
    private boolean closed;

    public HttpClient(String url) throws IOException {
        this.url = url;
        String rest = url.substring(7);  /* after "http://" */
        int slash = rest.indexOf('/');
        int question = rest.indexOf('?');
        int end = slash < 0 ? question : question < 0 ? slash : Math.min(slash, question);
        String authority = end < 0 ? rest : rest.substring(0, end);
        String path = end < 0 ? "/" : rest.substring(end);
        if (path.startsWith("?")) path = "/" + path;
        int hash = path.indexOf('#');
        ref = hash < 0 ? null : path.substring(hash+1);
        if (hash >= 0) path = path.substring(0, hash);
        int at = authority.lastIndexOf('@');
        if (at >= 0) authority = authority.substring(at+1);
        int colon = authority.indexOf(':');
        host = colon < 0 ? authority : authority.substring(0, colon);
        port = colon < 0 || colon == authority.length()-1 ? 80 : Integer.parseInt(authority.substring(colon+1));
        if (host.length() == 0) throw new IllegalArgumentException("No host: " + url);
        file = path;
        int q = path.indexOf('?');
        query = q < 0 ? null : path.substring(q+1);
    }

    private void needSetup() throws IOException {
        if (closed) throw new IOException("Connection closed");
        if (tcp != null) throw new IOException("Already connected");
    }

    /* ---------- request ---------- */

    public void setRequestMethod(String m) throws IOException {
        needSetup();
        if (!GET.equals(m) && !POST.equals(m) && !HEAD.equals(m)) throw new IOException("Unsupported method " + m);
        method = m;
    }

    public void setRequestProperty(String key, String value) throws IOException {
        needSetup();
        for (int i = 0; i < requestKeys.size(); i++)
            if (((String) requestKeys.elementAt(i)).equalsIgnoreCase(key)) { requestValues.setElementAt(value, i); return; }
        requestKeys.addElement(key);
        requestValues.addElement(value);
    }

    public String getRequestProperty(String key) {
        for (int i = 0; i < requestKeys.size(); i++)
            if (((String) requestKeys.elementAt(i)).equalsIgnoreCase(key)) return (String) requestValues.elementAt(i);
        return null;
    }

    private static void line(ByteArrayOutputStream out, String text) {
        for (int i = 0; i < text.length(); i++) out.write(text.charAt(i) & 255);
        out.write('\r');
        out.write('\n');
    }

    private void connect() throws IOException {
        if (tcp != null) return;
        if (closed) throw new IOException("Connection closed");
        ByteArrayOutputStream head = new ByteArrayOutputStream();
        line(head, method + " " + file + " HTTP/1.1");
        line(head, "Host: " + host + (port == 80 ? "" : ":" + port));
        if (getRequestProperty("User-Agent") == null)
            line(head, "User-Agent: Profile/MIDP-2.0 Configuration/CLDC-1.1 GO1JVM (Explay B220)");
        for (int i = 0; i < requestKeys.size(); i++) {
            String key = (String) requestKeys.elementAt(i);
            if (!key.equalsIgnoreCase("Connection") && !key.equalsIgnoreCase("Content-Length") && !key.equalsIgnoreCase("Host"))
                line(head, key + ": " + requestValues.elementAt(i));
        }
        line(head, "Connection: close");
        if (body != null || POST.equals(method)) line(head, "Content-Length: " + (body == null ? 0 : body.size()));
        line(head, "");
        tcp = new TcpConnection(host, port);
        byte[] bytes = head.toByteArray();
        tcp.write(bytes, 0, bytes.length);
        if (body != null) { bytes = body.toByteArray(); tcp.write(bytes, 0, bytes.length); }
        input = new Body(tcp);
        String status = input.line();
        int space = status.indexOf(' ');
        if (!status.startsWith("HTTP/") || space < 0) throw new IOException("Not an HTTP response");
        int next = status.indexOf(' ', space+1);
        code = Integer.parseInt(next < 0 ? status.substring(space+1) : status.substring(space+1, next));
        message = next < 0 ? "" : status.substring(next+1);
        for (String header = input.line(); header.length() > 0; header = input.line()) {
            int colon = header.indexOf(':');
            if (colon <= 0) continue;
            keys.addElement(header.substring(0, colon).trim());
            values.addElement(header.substring(colon+1).trim());
        }
        String encoding = getHeaderField("Transfer-Encoding");
        input.chunked = encoding != null && encoding.toLowerCase().indexOf("chunked") >= 0;
        input.left = HEAD.equals(method) || code == 204 || code == 304 ? 0 : input.chunked ? -1 : getLength();
    }

    /* ---------- response ---------- */

    public int getResponseCode() throws IOException { connect(); return code; }
    public String getResponseMessage() throws IOException { connect(); return message; }

    public String getHeaderField(String name) throws IOException {
        connect();
        for (int i = keys.size()-1; i >= 0; i--)
            if (((String) keys.elementAt(i)).equalsIgnoreCase(name)) return (String) values.elementAt(i);
        return null;
    }

    public String getHeaderField(int n) throws IOException { connect(); return n < values.size() ? (String) values.elementAt(n) : null; }
    public String getHeaderFieldKey(int n) throws IOException { connect(); return n < keys.size() ? (String) keys.elementAt(n) : null; }

    public int getHeaderFieldInt(String name, int def) throws IOException {
        String v = getHeaderField(name);
        try { return v == null ? def : Integer.parseInt(v.trim()); } catch (NumberFormatException e) { return def; }
    }

    public long getHeaderFieldDate(String name, long def) throws IOException { connect(); return def; }
    public long getExpiration() throws IOException { return getHeaderFieldDate("expires", 0); }
    public long getDate() throws IOException { return getHeaderFieldDate("date", 0); }
    public long getLastModified() throws IOException { return getHeaderFieldDate("last-modified", 0); }

    public String getType() {
        try { return getHeaderField("Content-Type"); } catch (IOException e) { return null; }
    }

    public String getEncoding() {
        try { return getHeaderField("Content-Encoding"); } catch (IOException e) { return null; }
    }

    public long getLength() {
        try { return getHeaderFieldInt("Content-Length", -1); } catch (IOException e) { return -1; }
    }

    public InputStream openInputStream() throws IOException { connect(); return input; }
    public DataInputStream openDataInputStream() throws IOException { return new DataInputStream(openInputStream()); }

    public OutputStream openOutputStream() throws IOException {
        needSetup();
        if (body == null) body = new ByteArrayOutputStream();
        if (GET.equals(method)) method = POST;
        return body;
    }

    public DataOutputStream openDataOutputStream() throws IOException { return new DataOutputStream(openOutputStream()); }

    public void close() {
        closed = true;
        if (tcp != null) tcp.close();
    }

    public String getURL() { return url; }
    public String getProtocol() { return "http"; }
    public String getHost() { return host; }
    public String getFile() { int q = file.indexOf('?'); return q < 0 ? file : file.substring(0, q); }
    public String getRef() { return ref; }
    public String getQuery() { return query; }
    public int getPort() { return port; }
    public String getRequestMethod() { return method; }

    /* Buffered response reader: header lines, then a sized, chunked or open-ended body. */
    static final class Body extends InputStream {
        private final TcpConnection tcp;
        private final byte[] buffer = new byte[1024];
        private int at, count;
        boolean chunked;
        long left;          /* body bytes still to read, -1 unknown */
        private boolean done;

        Body(TcpConnection tcp) { this.tcp = tcp; }

        private int raw() throws IOException {
            if (at == count) {
                count = tcp.read(buffer, 0, buffer.length);
                at = 0;
                if (count <= 0) { count = 0; return -1; }
            }
            return buffer[at++] & 255;
        }

        String line() throws IOException {
            StringBuffer s = new StringBuffer();
            for (int c = raw(); c >= 0 && c != '\n'; c = raw()) if (c != '\r') s.append((char) c);
            return s.toString();
        }

        private boolean nextChunk() throws IOException {
            String size = line();
            if (size.length() == 0) size = line();  /* CRLF after the previous chunk */
            int semicolon = size.indexOf(';');
            if (semicolon >= 0) size = size.substring(0, semicolon);
            try { left = Integer.parseInt(size.trim(), 16); } catch (NumberFormatException e) { throw new IOException("Bad chunk"); }
            if (left == 0) { while (line().length() > 0) {} done = true; }
            return !done;
        }

        public int read() throws IOException {
            byte[] one = new byte[1];
            return read(one, 0, 1) < 0 ? -1 : one[0] & 255;
        }

        public int read(byte[] b, int offset, int length) throws IOException {
            if (done || length == 0) return done ? -1 : 0;
            if (chunked && left == 0 && !nextChunk()) return -1;
            if (!chunked && left == 0) { done = true; return -1; }
            int want = left < 0 || left > length ? length : (int) left;
            int n;
            if (at < count) {
                n = Math.min(want, count-at);
                System.arraycopy(buffer, at, b, offset, n);
                at += n;
            } else {
                n = tcp.read(b, offset, want);
                if (n < 0) { done = true; return -1; }
            }
            if (left > 0) left -= n;
            return n;
        }

        public int available() { return count-at; }
        public void close() { done = true; }
    }
}
