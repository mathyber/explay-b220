package java.io;

public class ByteArrayOutputStream extends OutputStream {
    protected byte[] buf;
    protected int count;

    public ByteArrayOutputStream() {
        this(32);
    }

    public ByteArrayOutputStream(int size) {
        if (size < 0) throw new IllegalArgumentException();
        buf = new byte[size];
    }

    private void grow(int minimum) {
        if (minimum > buf.length) {
            byte[] bigger = new byte[Math.max(buf.length * 2, minimum)];
            System.arraycopy(buf, 0, bigger, 0, count);
            buf = bigger;
        }
    }

    public synchronized void write(int b) {
        grow(count + 1);
        buf[count++] = (byte) b;
    }

    public synchronized void write(byte[] b, int offset, int length) {
        if (offset < 0 || length < 0 || offset > b.length - length) throw new IndexOutOfBoundsException();
        grow(count + length);
        System.arraycopy(b, offset, buf, count, length);
        count += length;
    }

    public synchronized void reset() { count = 0; }

    public synchronized byte[] toByteArray() {
        byte[] copy = new byte[count];
        System.arraycopy(buf, 0, copy, 0, count);
        return copy;
    }

    public int size() { return count; }
    public String toString() { return new String(buf, 0, count); }
    public synchronized void close() {}
}
