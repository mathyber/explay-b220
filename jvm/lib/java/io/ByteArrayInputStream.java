package java.io;

public class ByteArrayInputStream extends InputStream {
    protected byte[] buf;
    protected int pos;
    protected int mark;
    protected int count;

    public ByteArrayInputStream(byte[] buffer) {
        this(buffer, 0, buffer.length);
    }

    public ByteArrayInputStream(byte[] buffer, int offset, int length) {
        buf = buffer;
        pos = offset;
        mark = offset;
        count = Math.min(offset + length, buffer.length);
    }

    public synchronized int read() {
        return pos < count ? buf[pos++] & 0xff : -1;
    }

    public synchronized int read(byte[] b, int offset, int length) {
        if (offset < 0 || length < 0 || offset > b.length - length) throw new IndexOutOfBoundsException();
        if (pos >= count) return -1;
        if (length > count - pos) length = count - pos;
        System.arraycopy(buf, pos, b, offset, length);
        pos += length;
        return length;
    }

    public synchronized long skip(long n) {
        if (n > count - pos) n = count - pos;
        if (n < 0) return 0;
        pos += (int) n;
        return n;
    }

    public synchronized int available() { return count - pos; }
    public boolean markSupported() { return true; }
    public void mark(int limit) { mark = pos; }
    public synchronized void reset() { pos = mark; }
    public synchronized void close() {}
}
