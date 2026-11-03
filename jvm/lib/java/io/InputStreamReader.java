package java.io;

/* UTF-8 (default) or ISO-8859-1 decoding. */
public class InputStreamReader extends Reader {
    private final InputStream in;
    private final boolean latin1;

    public InputStreamReader(InputStream in) {
        this.in = in;
        latin1 = false;
    }

    public InputStreamReader(InputStream in, String encoding) throws UnsupportedEncodingException {
        this.in = in;
        String e = encoding.toUpperCase();
        if (e.equals("ISO-8859-1") || e.equals("ISO8859_1") || e.equals("US-ASCII")) latin1 = true;
        else if (e.equals("UTF-8") || e.equals("UTF8")) latin1 = false;
        else throw new UnsupportedEncodingException(encoding);
    }

    private int decode() throws IOException {
        int c = in.read();
        if (c < 0x80 || latin1) return c;
        if ((c & 0xe0) == 0xc0) return ((c & 0x1f) << 6) | (in.read() & 0x3f);
        if ((c & 0xf0) == 0xe0) {
            int b = in.read() & 0x3f;
            return ((c & 0x0f) << 12) | (b << 6) | (in.read() & 0x3f);
        }
        return '?';
    }

    public int read() throws IOException {
        return decode();
    }

    public int read(char[] c, int offset, int length) throws IOException {
        int n = 0;
        while (n < length) {
            int ch = decode();
            if (ch < 0) return n == 0 ? -1 : n;
            c[offset + n++] = (char) ch;
            if (in.available() <= 0) break;
        }
        return n;
    }

    public boolean ready() throws IOException { return in.available() > 0; }
    public void close() throws IOException { in.close(); }
}
