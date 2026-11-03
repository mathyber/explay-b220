package java.io;

/* UTF-8 (default) or ISO-8859-1 encoding. */
public class OutputStreamWriter extends Writer {
    private final OutputStream out;
    private final boolean latin1;

    public OutputStreamWriter(OutputStream out) {
        this.out = out;
        latin1 = false;
    }

    public OutputStreamWriter(OutputStream out, String encoding) throws UnsupportedEncodingException {
        this.out = out;
        String e = encoding.toUpperCase();
        if (e.equals("ISO-8859-1") || e.equals("ISO8859_1") || e.equals("US-ASCII")) latin1 = true;
        else if (e.equals("UTF-8") || e.equals("UTF8")) latin1 = false;
        else throw new UnsupportedEncodingException(encoding);
    }

    public void write(char[] c, int offset, int length) throws IOException {
        for (int i = offset; i < offset + length; i++) {
            char ch = c[i];
            if (ch < 0x80 || latin1) out.write(ch < 256 ? ch : '?');
            else if (ch < 0x800) {
                out.write(0xc0 | (ch >> 6));
                out.write(0x80 | (ch & 0x3f));
            } else {
                out.write(0xe0 | (ch >> 12));
                out.write(0x80 | ((ch >> 6) & 0x3f));
                out.write(0x80 | (ch & 0x3f));
            }
        }
    }

    public void flush() throws IOException { out.flush(); }
    public void close() throws IOException { out.close(); }
}
