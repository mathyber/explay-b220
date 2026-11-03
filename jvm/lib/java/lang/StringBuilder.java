package java.lang;

/* Not in CLDC; javac uses it for string concatenation. */
public final class StringBuilder extends AbstractStringBuilder {
    public StringBuilder() {
        super(16);
    }

    public StringBuilder(int capacity) {
        super(capacity);
    }

    public StringBuilder(String s) {
        super(s.length() + 16);
        add(s);
    }

    public StringBuilder append(Object o) { add(String.valueOf(o)); return this; }
    public StringBuilder append(String s) { add(s); return this; }
    public StringBuilder append(StringBuffer s) { add(String.valueOf(s)); return this; }
    public StringBuilder append(char[] chars) { add(chars, 0, chars.length); return this; }
    public StringBuilder append(char[] chars, int start, int length) { add(chars, start, length); return this; }
    public StringBuilder append(boolean b) { add(String.valueOf(b)); return this; }
    public StringBuilder append(char c) { add(c); return this; }
    public StringBuilder append(int i) { add(Integer.toString(i)); return this; }
    public StringBuilder append(long l) { add(Long.toString(l)); return this; }
    public StringBuilder append(float f) { add(Float.toString(f)); return this; }
    public StringBuilder append(double d) { add(Double.toString(d)); return this; }
    public StringBuilder insert(int at, String s) { insertString(at, s); return this; }
    public StringBuilder insert(int at, char c) { insertString(at, String.valueOf(c)); return this; }
    public StringBuilder insert(int at, int i) { insertString(at, Integer.toString(i)); return this; }
    public StringBuilder delete(int start, int end) { remove(start, end); return this; }
    public StringBuilder deleteCharAt(int index) {
        if (index < 0 || index >= count) throw new StringIndexOutOfBoundsException(index);
        remove(index, index + 1);
        return this;
    }
    public StringBuilder reverse() { reverseChars(); return this; }
}
