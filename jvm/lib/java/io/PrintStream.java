package java.io;

/* Console stream: text goes straight to the host (1 = out, 2 = err). */
public class PrintStream extends OutputStream {
    private final int stream;

    public PrintStream(int stream) {
        this.stream = stream;
    }

    public PrintStream(OutputStream out) {
        this.stream = 1;
    }

    private static native void write0(int stream, String s);

    public void write(int b) {
        write0(stream, String.valueOf((char) (b & 0xff)));
    }

    public void print(String s) { write0(stream, s == null ? "null" : s); }
    public void print(Object o) { print(String.valueOf(o)); }
    public void print(char[] chars) { print(new String(chars)); }
    public void print(boolean b) { print(String.valueOf(b)); }
    public void print(char c) { print(String.valueOf(c)); }
    public void print(int i) { print(String.valueOf(i)); }
    public void print(long l) { print(String.valueOf(l)); }
    public void print(float f) { print(String.valueOf(f)); }
    public void print(double d) { print(String.valueOf(d)); }
    public void println() { print("\n"); }
    public void println(String s) { print(s); println(); }
    public void println(Object o) { print(String.valueOf(o)); println(); }
    public void println(char[] chars) { print(chars); println(); }
    public void println(boolean b) { print(b); println(); }
    public void println(char c) { print(c); println(); }
    public void println(int i) { print(i); println(); }
    public void println(long l) { print(l); println(); }
    public void println(float f) { print(f); println(); }
    public void println(double d) { print(d); println(); }
    public void flush() {}
    public void close() {}
    public boolean checkError() { return false; }
}
