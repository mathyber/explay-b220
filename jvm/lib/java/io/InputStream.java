package java.io;

public abstract class InputStream {
    public abstract int read() throws IOException;

    public int read(byte[] b) throws IOException {
        return read(b, 0, b.length);
    }

    public int read(byte[] b, int offset, int length) throws IOException {
        if (offset < 0 || length < 0 || offset > b.length - length) throw new IndexOutOfBoundsException();
        if (length == 0) return 0;
        int c = read();
        if (c < 0) return -1;
        b[offset] = (byte) c;
        int n = 1;
        for (; n < length; n++) {
            if (available() <= 0) break;
            c = read();
            if (c < 0) break;
            b[offset + n] = (byte) c;
        }
        return n;
    }

    public long skip(long n) throws IOException {
        long skipped = 0;
        while (skipped < n && read() >= 0) skipped++;
        return skipped;
    }

    public int available() throws IOException { return 0; }
    public void close() throws IOException {}
    public synchronized void mark(int limit) {}
    public synchronized void reset() throws IOException { throw new IOException("mark/reset not supported"); }
    public boolean markSupported() { return false; }
}
