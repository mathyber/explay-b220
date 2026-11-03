package b22;

import java.io.IOException;
import java.io.InputStream;

/* A large JAR resource decompressed while it is read (about 34 KB of state
   instead of the whole resource). */
public final class ResourceStream extends InputStream {
    private byte[] state;
    private final byte[] one = new byte[1];

    public ResourceStream(byte[] state) { this.state = state; }

    private byte[] open() throws IOException {
        if (state == null) throw new IOException("closed");
        return state;
    }

    public int read() throws IOException {
        return read(one, 0, 1) == 1 ? one[0] & 0xff : -1;
    }

    public int read(byte[] b, int off, int len) throws IOException {
        if (b == null) throw new NullPointerException();
        if (off < 0 || len < 0 || off + len > b.length) throw new IndexOutOfBoundsException();
        if (len == 0) return 0;
        int n = read0(open(), b, off, len);
        return n == 0 ? -1 : n;
    }

    public long skip(long n) throws IOException {
        byte[] scratch = new byte[(int) Math.min(n, 1024)];
        long skipped = 0;
        while (skipped < n) {
            int r = read(scratch, 0, (int) Math.min(n - skipped, scratch.length));
            if (r < 0) break;
            skipped += r;
        }
        return skipped;
    }

    public int available() throws IOException { return left(open()); }

    public void close() { state = null; }

    private static native int read0(byte[] state, byte[] b, int off, int len);
    private static native int left(byte[] state);
}
