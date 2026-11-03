package java.io;

public class DataOutputStream extends OutputStream implements DataOutput {
    protected OutputStream out;

    public DataOutputStream(OutputStream out) {
        this.out = out;
    }

    public void write(int b) throws IOException { out.write(b); }
    public void write(byte[] b, int offset, int length) throws IOException { out.write(b, offset, length); }
    public void flush() throws IOException { out.flush(); }
    public void close() throws IOException { out.close(); }
    public final void writeBoolean(boolean v) throws IOException { out.write(v ? 1 : 0); }
    public final void writeByte(int v) throws IOException { out.write(v); }
    public final void writeShort(int v) throws IOException { out.write(v >>> 8); out.write(v); }
    public final void writeChar(int v) throws IOException { writeShort(v); }
    public final void writeInt(int v) throws IOException { writeShort(v >>> 16); writeShort(v); }
    public final void writeLong(long v) throws IOException { writeInt((int) (v >>> 32)); writeInt((int) v); }
    public final void writeFloat(float v) throws IOException { writeInt(Float.floatToIntBits(v)); }
    public final void writeDouble(double v) throws IOException { writeLong(Double.doubleToLongBits(v)); }

    public final void writeChars(String s) throws IOException {
        for (int i = 0; i < s.length(); i++) writeChar(s.charAt(i));
    }

    public final void writeUTF(String s) throws IOException {
        int length = 0;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            length += c >= 1 && c < 0x80 ? 1 : c < 0x800 ? 2 : 3;
        }
        if (length > 65535) throw new UTFDataFormatException();
        writeShort(length);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c >= 1 && c < 0x80) out.write(c);
            else if (c < 0x800) {
                out.write(0xc0 | (c >> 6));
                out.write(0x80 | (c & 0x3f));
            } else {
                out.write(0xe0 | (c >> 12));
                out.write(0x80 | ((c >> 6) & 0x3f));
                out.write(0x80 | (c & 0x3f));
            }
        }
    }
}
