package java.io;

public abstract class OutputStream {
    public abstract void write(int b) throws IOException;

    public void write(byte[] bytes) throws IOException {
        write(bytes, 0, bytes.length);
    }

    public void write(byte[] bytes, int start, int length) throws IOException {
        for (int i = 0; i < length; i++) write(bytes[start + i]);
    }

    public void flush() throws IOException {}

    public void close() throws IOException {}
}
