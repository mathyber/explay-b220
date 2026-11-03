package java.lang;

/* CLDC String: shared char array with offset and count (fields used by the VM). */
public final class String {
    char[] value;
    int offset;
    int count;
    private int hash;

    public String() {
        value = new char[0];
    }

    public String(char[] chars) {
        this(chars, 0, chars.length);
    }

    public String(char[] chars, int start, int length) {
        if (start < 0 || length < 0 || start > chars.length - length) throw new StringIndexOutOfBoundsException();
        value = new char[length];
        count = length;
        System.arraycopy(chars, start, value, 0, length);
    }

    public String(String other) {
        value = other.value;
        offset = other.offset;
        count = other.count;
    }

    public String(StringBuffer buffer) {
        this(buffer.toString());
    }

    public String(byte[] bytes) {
        this(bytes, 0, bytes.length);
    }

    public String(byte[] bytes, int start, int length) {
        if (start < 0 || length < 0 || start > bytes.length - length) throw new StringIndexOutOfBoundsException();
        char[] chars = new char[length];
        int n = 0, end = start + length;
        for (int i = start; i < end; ) {
            int c = bytes[i++] & 0xff;
            if (c >= 0xc0 && c < 0xe0 && i < end) c = ((c & 0x1f) << 6) | (bytes[i++] & 0x3f);
            else if (c >= 0xe0 && c < 0xf0 && i + 1 < end) {
                c = ((c & 0x0f) << 12) | ((bytes[i] & 0x3f) << 6) | (bytes[i + 1] & 0x3f);
                i += 2;
            }
            chars[n++] = (char) c;
        }
        value = chars;
        count = n;
    }

    public String(byte[] bytes, String encoding) throws java.io.UnsupportedEncodingException {
        this(bytes, 0, bytes.length, encoding);
    }

    public String(byte[] bytes, int start, int length, String encoding) throws java.io.UnsupportedEncodingException {
        this(bytes, start, length);
        if (isLatin1(encoding)) {
            char[] chars = new char[length];
            for (int i = 0; i < length; i++) chars[i] = (char) (bytes[start + i] & 0xff);
            value = chars;
            count = length;
            offset = 0;
        } else if (!isUtf8(encoding)) throw new java.io.UnsupportedEncodingException(encoding);
    }

    private static boolean isUtf8(String e) {
        return e.equalsIgnoreCase("UTF-8") || e.equalsIgnoreCase("UTF8");
    }

    private static boolean isLatin1(String e) {
        return e.equalsIgnoreCase("ISO-8859-1") || e.equalsIgnoreCase("ISO8859_1") || e.equalsIgnoreCase("US-ASCII");
    }

    String(int start, int length, char[] chars) {
        value = chars;
        offset = start;
        count = length;
    }

    public int length() {
        return count;
    }

    public char charAt(int index) {
        if (index < 0 || index >= count) throw new StringIndexOutOfBoundsException(index);
        return value[offset + index];
    }

    public void getChars(int start, int end, char[] destination, int at) {
        if (start < 0 || end > count || start > end) throw new StringIndexOutOfBoundsException();
        System.arraycopy(value, offset + start, destination, at, end - start);
    }

    public char[] toCharArray() {
        char[] result = new char[count];
        System.arraycopy(value, offset, result, 0, count);
        return result;
    }

    public byte[] getBytes() {
        int size = 0;
        for (int i = 0; i < count; i++) {
            char c = value[offset + i];
            size += c < 0x80 ? 1 : c < 0x800 ? 2 : 3;
        }
        byte[] result = new byte[size];
        int n = 0;
        for (int i = 0; i < count; i++) {
            char c = value[offset + i];
            if (c < 0x80) result[n++] = (byte) c;
            else if (c < 0x800) {
                result[n++] = (byte) (0xc0 | (c >> 6));
                result[n++] = (byte) (0x80 | (c & 0x3f));
            } else {
                result[n++] = (byte) (0xe0 | (c >> 12));
                result[n++] = (byte) (0x80 | ((c >> 6) & 0x3f));
                result[n++] = (byte) (0x80 | (c & 0x3f));
            }
        }
        return result;
    }

    public byte[] getBytes(String encoding) throws java.io.UnsupportedEncodingException {
        if (isUtf8(encoding)) return getBytes();
        if (!isLatin1(encoding)) throw new java.io.UnsupportedEncodingException(encoding);
        byte[] result = new byte[count];
        for (int i = 0; i < count; i++) {
            char c = value[offset + i];
            result[i] = (byte) (c < 256 ? c : '?');
        }
        return result;
    }

    public boolean equals(Object other) {
        if (this == other) return true;
        if (!(other instanceof String)) return false;
        String s = (String) other;
        if (s.count != count) return false;
        for (int i = 0; i < count; i++) if (value[offset + i] != s.value[s.offset + i]) return false;
        return true;
    }

    public boolean equalsIgnoreCase(String s) {
        if (s == null || s.count != count) return false;
        return regionMatches(true, 0, s, 0, count);
    }

    public int hashCode() {
        int h = hash;
        if (h == 0) {
            for (int i = 0; i < count; i++) h = 31 * h + value[offset + i];
            hash = h;
        }
        return h;
    }

    public int compareTo(String s) {
        int n = count < s.count ? count : s.count;
        for (int i = 0; i < n; i++) {
            int d = value[offset + i] - s.value[s.offset + i];
            if (d != 0) return d;
        }
        return count - s.count;
    }

    public boolean regionMatches(boolean ignoreCase, int at, String other, int otherAt, int length) {
        if (at < 0 || otherAt < 0 || at > count - length || otherAt > other.count - length) return false;
        for (int i = 0; i < length; i++) {
            char a = value[offset + at + i], b = other.value[other.offset + otherAt + i];
            if (a == b) continue;
            if (ignoreCase && (Character.toLowerCase(a) == Character.toLowerCase(b)
                || Character.toUpperCase(a) == Character.toUpperCase(b))) continue;
            return false;
        }
        return true;
    }

    public boolean startsWith(String prefix, int at) {
        return regionMatches(false, at, prefix, 0, prefix.count);
    }

    public boolean startsWith(String prefix) {
        return startsWith(prefix, 0);
    }

    public boolean endsWith(String suffix) {
        return startsWith(suffix, count - suffix.count);
    }

    public int indexOf(int c) {
        return indexOf(c, 0);
    }

    public int indexOf(int c, int from) {
        if (from < 0) from = 0;
        for (int i = from; i < count; i++) if (value[offset + i] == c) return i;
        return -1;
    }

    public int lastIndexOf(int c) {
        return lastIndexOf(c, count - 1);
    }

    public int lastIndexOf(int c, int from) {
        if (from >= count) from = count - 1;
        for (int i = from; i >= 0; i--) if (value[offset + i] == c) return i;
        return -1;
    }

    public int indexOf(String s) {
        return indexOf(s, 0);
    }

    public int indexOf(String s, int from) {
        if (from < 0) from = 0;
        for (int i = from; i <= count - s.count; i++) if (startsWith(s, i)) return i;
        return -1;
    }

    public int lastIndexOf(String s) {
        for (int i = count - s.count; i >= 0; i--) if (startsWith(s, i)) return i;
        return -1;
    }

    public String substring(int start) {
        return substring(start, count);
    }

    public String substring(int start, int end) {
        if (start < 0 || end > count || start > end) throw new StringIndexOutOfBoundsException();
        if (start == 0 && end == count) return this;
        return new String(offset + start, end - start, value);
    }

    public String concat(String s) {
        if (s.count == 0) return this;
        char[] chars = new char[count + s.count];
        System.arraycopy(value, offset, chars, 0, count);
        System.arraycopy(s.value, s.offset, chars, count, s.count);
        return new String(0, chars.length, chars);
    }

    public String replace(char from, char to) {
        if (indexOf(from) < 0) return this;
        char[] chars = toCharArray();
        for (int i = 0; i < chars.length; i++) if (chars[i] == from) chars[i] = to;
        return new String(0, chars.length, chars);
    }

    public String toLowerCase() {
        char[] chars = toCharArray();
        for (int i = 0; i < chars.length; i++) chars[i] = Character.toLowerCase(chars[i]);
        return new String(0, chars.length, chars);
    }

    public String toUpperCase() {
        char[] chars = toCharArray();
        for (int i = 0; i < chars.length; i++) chars[i] = Character.toUpperCase(chars[i]);
        return new String(0, chars.length, chars);
    }

    public String trim() {
        int start = 0, end = count;
        while (start < end && value[offset + start] <= ' ') start++;
        while (end > start && value[offset + end - 1] <= ' ') end--;
        return substring(start, end);
    }

    public String toString() {
        return this;
    }

    public native String intern();

    public static String valueOf(Object o) {
        return o == null ? "null" : o.toString();
    }

    public static String valueOf(char[] chars) {
        return new String(chars);
    }

    public static String valueOf(char[] chars, int start, int length) {
        return new String(chars, start, length);
    }

    public static String valueOf(boolean b) {
        return b ? "true" : "false";
    }

    public static String valueOf(char c) {
        char[] chars = {c};
        return new String(0, 1, chars);
    }

    public static String valueOf(int i) {
        return Integer.toString(i);
    }

    public static String valueOf(long l) {
        return Long.toString(l);
    }

    public static String valueOf(float f) {
        return Float.toString(f);
    }

    public static String valueOf(double d) {
        return Double.toString(d);
    }
}
