package java.lang;

/* Shared storage for StringBuffer and StringBuilder. */
abstract class AbstractStringBuilder {
    char[] value;
    int count;

    AbstractStringBuilder(int capacity) {
        value = new char[capacity];
    }

    public int length() {
        return count;
    }

    public int capacity() {
        return value.length;
    }

    public void ensureCapacity(int minimum) {
        if (minimum > value.length) {
            int size = value.length * 2 + 2;
            if (size < minimum) size = minimum;
            char[] bigger = new char[size];
            System.arraycopy(value, 0, bigger, 0, count);
            value = bigger;
        }
    }

    public void setLength(int length) {
        if (length < 0) throw new StringIndexOutOfBoundsException(length);
        ensureCapacity(length);
        for (int i = count; i < length; i++) value[i] = 0;
        count = length;
    }

    public char charAt(int index) {
        if (index < 0 || index >= count) throw new StringIndexOutOfBoundsException(index);
        return value[index];
    }

    public void setCharAt(int index, char c) {
        if (index < 0 || index >= count) throw new StringIndexOutOfBoundsException(index);
        value[index] = c;
    }

    public void getChars(int start, int end, char[] destination, int at) {
        if (start < 0 || end > count || start > end) throw new StringIndexOutOfBoundsException();
        System.arraycopy(value, start, destination, at, end - start);
    }

    final void add(String s) {
        if (s == null) s = "null";
        int n = s.count;
        ensureCapacity(count + n);
        System.arraycopy(s.value, s.offset, value, count, n);
        count += n;
    }

    final void add(char[] chars, int start, int length) {
        ensureCapacity(count + length);
        System.arraycopy(chars, start, value, count, length);
        count += length;
    }

    final void add(char c) {
        ensureCapacity(count + 1);
        value[count++] = c;
    }

    final void insertString(int at, String s) {
        if (at < 0 || at > count) throw new StringIndexOutOfBoundsException(at);
        if (s == null) s = "null";
        int n = s.count;
        ensureCapacity(count + n);
        System.arraycopy(value, at, value, at + n, count - at);
        System.arraycopy(s.value, s.offset, value, at, n);
        count += n;
    }

    final void remove(int start, int end) {
        if (end > count) end = count;
        if (start < 0 || start > end) throw new StringIndexOutOfBoundsException();
        System.arraycopy(value, end, value, start, count - end);
        count -= end - start;
    }

    final void reverseChars() {
        for (int i = 0, j = count - 1; i < j; i++, j--) {
            char c = value[i];
            value[i] = value[j];
            value[j] = c;
        }
    }

    public String toString() {
        return new String(value, 0, count);
    }
}
