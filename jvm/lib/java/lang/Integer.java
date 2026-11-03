package java.lang;

public final class Integer {
    public static final int MIN_VALUE = 0x80000000;
    public static final int MAX_VALUE = 0x7fffffff;
    private static final char[] DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz".toCharArray();
    private final int value;

    public Integer(int value) {
        this.value = value;
    }

    public static String toString(int i, int radix) {
        if (radix < 2 || radix > 36) radix = 10;
        if (i == 0) return "0";
        char[] buffer = new char[33];
        int at = 33;
        boolean negative = i < 0;
        if (!negative) i = -i;
        while (i != 0) {
            buffer[--at] = DIGITS[-(i % radix)];
            i /= radix;
        }
        if (negative) buffer[--at] = '-';
        return new String(buffer, at, 33 - at);
    }

    public static String toString(int i) {
        return toString(i, 10);
    }

    private static String unsigned(int i, int shift) {
        char[] buffer = new char[32];
        int at = 32, mask = (1 << shift) - 1;
        do {
            buffer[--at] = DIGITS[i & mask];
            i >>>= shift;
        } while (i != 0);
        return new String(buffer, at, 32 - at);
    }

    public static String toHexString(int i) { return unsigned(i, 4); }
    public static String toOctalString(int i) { return unsigned(i, 3); }
    public static String toBinaryString(int i) { return unsigned(i, 1); }

    public static int parseInt(String s, int radix) throws NumberFormatException {
        if (s == null || s.length() == 0 || radix < 2 || radix > 36) throw NumberFormatException.forInputString(s);
        int i = 0, length = s.length();
        boolean negative = false;
        if (s.charAt(0) == '-' || s.charAt(0) == '+') {
            negative = s.charAt(0) == '-';
            i = 1;
            if (length == 1) throw NumberFormatException.forInputString(s);
        }
        int limit = negative ? MIN_VALUE : -MAX_VALUE, result = 0, minimum = limit / radix;
        for (; i < length; i++) {
            int digit = Character.digit(s.charAt(i), radix);
            if (digit < 0 || result < minimum) throw NumberFormatException.forInputString(s);
            result *= radix;
            if (result < limit + digit) throw NumberFormatException.forInputString(s);
            result -= digit;
        }
        return negative ? result : -result;
    }

    public static int parseInt(String s) throws NumberFormatException {
        return parseInt(s, 10);
    }

    public static Integer valueOf(String s, int radix) throws NumberFormatException { return new Integer(parseInt(s, radix)); }
    public static Integer valueOf(String s) throws NumberFormatException { return new Integer(parseInt(s, 10)); }
    public static Integer valueOf(int i) { return new Integer(i); }

    public byte byteValue() { return (byte) value; }
    public short shortValue() { return (short) value; }
    public int intValue() { return value; }
    public long longValue() { return value; }
    public float floatValue() { return value; }
    public double doubleValue() { return value; }
    public String toString() { return toString(value); }
    public int hashCode() { return value; }
    public boolean equals(Object o) { return o instanceof Integer && ((Integer) o).value == value; }
}
