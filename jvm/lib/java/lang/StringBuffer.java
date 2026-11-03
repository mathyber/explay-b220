package java.lang;

public final class StringBuffer extends AbstractStringBuilder {
    public StringBuffer() {
        super(16);
    }

    public StringBuffer(int capacity) {
        super(capacity);
    }

    public StringBuffer(String s) {
        super(s.length() + 16);
        add(s);
    }

    public StringBuffer append(Object o) { add(String.valueOf(o)); return this; }
    public StringBuffer append(String s) { add(s); return this; }
    public StringBuffer append(char[] chars) { add(chars, 0, chars.length); return this; }
    public StringBuffer append(char[] chars, int start, int length) { add(chars, start, length); return this; }
    public StringBuffer append(boolean b) { add(String.valueOf(b)); return this; }
    public StringBuffer append(char c) { add(c); return this; }
    public StringBuffer append(int i) { add(Integer.toString(i)); return this; }
    public StringBuffer append(long l) { add(Long.toString(l)); return this; }
    public StringBuffer append(float f) { add(Float.toString(f)); return this; }
    public StringBuffer append(double d) { add(Double.toString(d)); return this; }
    public StringBuffer insert(int at, Object o) { insertString(at, String.valueOf(o)); return this; }
    public StringBuffer insert(int at, String s) { insertString(at, s); return this; }
    public StringBuffer insert(int at, char[] chars) { insertString(at, new String(chars)); return this; }
    public StringBuffer insert(int at, boolean b) { insertString(at, String.valueOf(b)); return this; }
    public StringBuffer insert(int at, char c) { insertString(at, String.valueOf(c)); return this; }
    public StringBuffer insert(int at, int i) { insertString(at, Integer.toString(i)); return this; }
    public StringBuffer insert(int at, long l) { insertString(at, Long.toString(l)); return this; }
    public StringBuffer insert(int at, float f) { insertString(at, Float.toString(f)); return this; }
    public StringBuffer insert(int at, double d) { insertString(at, Double.toString(d)); return this; }
    public StringBuffer delete(int start, int end) { remove(start, end); return this; }
    public StringBuffer deleteCharAt(int index) {
        if (index < 0 || index >= count) throw new StringIndexOutOfBoundsException(index);
        remove(index, index + 1);
        return this;
    }
    public StringBuffer reverse() { reverseChars(); return this; }
}
