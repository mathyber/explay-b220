package java.io;

public class DataInputStream extends InputStream implements DataInput {
    protected InputStream in;

    public DataInputStream(InputStream in) {
        this.in = in;
    }

    public int read() throws IOException { return in.read(); }
    public final int read(byte[] b) throws IOException { return in.read(b, 0, b.length); }
    public final int read(byte[] b, int offset, int length) throws IOException { return in.read(b, offset, length); }

    public final void readFully(byte[] b) throws IOException {
        readFully(b, 0, b.length);
    }

    public final void readFully(byte[] b, int offset, int length) throws IOException {
        while (length > 0) {
            int n = in.read(b, offset, length);
            if (n < 0) throw new EOFException();
            offset += n;
            length -= n;
        }
    }

    public final int skipBytes(int n) throws IOException {
        int total = 0;
        while (total < n) {
            long skipped = in.skip(n - total);
            if (skipped <= 0) break;
            total += (int) skipped;
        }
        return total;
    }

    private int next() throws IOException {
        int c = in.read();
        if (c < 0) throw new EOFException();
        return c;
    }

    public final boolean readBoolean() throws IOException { return next() != 0; }
    public final byte readByte() throws IOException { return (byte) next(); }
    public final int readUnsignedByte() throws IOException { return next(); }
    public final short readShort() throws IOException { return (short) readUnsignedShort(); }
    public final int readUnsignedShort() throws IOException { int a = next(); return (a << 8) | next(); }
    public final char readChar() throws IOException { return (char) readUnsignedShort(); }
    public final int readInt() throws IOException { int a = readUnsignedShort(); return (a << 16) | readUnsignedShort(); }
    public final long readLong() throws IOException { long a = readInt(); return (a << 32) | (readInt() & 0xffffffffL); }
    public final float readFloat() throws IOException { return Float.intBitsToFloat(readInt()); }
    public final double readDouble() throws IOException { return Double.longBitsToDouble(readLong()); }

    public final String readUTF() throws IOException {
        return readUTF(this);
    }

    public static final String readUTF(DataInput input) throws IOException {
        int length = input.readUnsignedShort();
        byte[] bytes = new byte[length];
        input.readFully(bytes);
        char[] chars = new char[length];
        int n = 0;
        for (int i = 0; i < length; ) {
            int c = bytes[i++] & 0xff;
            if (c >= 0x80) {
                if ((c & 0xe0) == 0xc0 && i < length) c = ((c & 0x1f) << 6) | (bytes[i++] & 0x3f);
                else if ((c & 0xf0) == 0xe0 && i + 1 < length) {
                    c = ((c & 0x0f) << 12) | ((bytes[i] & 0x3f) << 6) | (bytes[i + 1] & 0x3f);
                    i += 2;
                } else throw new UTFDataFormatException();
            }
            chars[n++] = (char) c;
        }
        return new String(chars, 0, n);
    }

    public long skip(long n) throws IOException { return in.skip(n); }
    public int available() throws IOException { return in.available(); }
    public void close() throws IOException { in.close(); }
    public synchronized void mark(int limit) { in.mark(limit); }
    public synchronized void reset() throws IOException { in.reset(); }
    public boolean markSupported() { return in.markSupported(); }
}
