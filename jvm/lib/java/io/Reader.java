package java.io;

public abstract class Reader {
    protected Object lock;

    protected Reader() {
        lock = this;
    }

    protected Reader(Object lock) {
        this.lock = lock;
    }

    public int read() throws IOException {
        char[] one = new char[1];
        return read(one, 0, 1) < 0 ? -1 : one[0];
    }

    public int read(char[] c) throws IOException {
        return read(c, 0, c.length);
    }

    public abstract int read(char[] c, int offset, int length) throws IOException;

    public long skip(long n) throws IOException {
        long skipped = 0;
        while (skipped < n && read() >= 0) skipped++;
        return skipped;
    }

    public boolean ready() throws IOException { return false; }
    public boolean markSupported() { return false; }
    public void mark(int limit) throws IOException { throw new IOException("mark not supported"); }
    public void reset() throws IOException { throw new IOException("reset not supported"); }
    public abstract void close() throws IOException;
}
