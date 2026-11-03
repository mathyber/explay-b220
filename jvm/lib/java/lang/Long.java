package java.lang;

public final class Long {
    public static final long MIN_VALUE = 0x8000000000000000L;
    public static final long MAX_VALUE = 0x7fffffffffffffffL;
    private final long value;

    public Long(long value) {
        this.value = value;
    }

    public static native String toString(long l);

    public static String toString(long l, int radix) {
        if (radix == 10 || radix < 2 || radix > 36) return toString(l);
        if (l == 0) return "0";
        char[] buffer = new char[65];
        int at = 65;
        boolean negative = l < 0;
        if (!negative) l = -l;
        while (l != 0) {
            buffer[--at] = Character.forDigit((int) -(l % radix), radix);
            l /= radix;
        }
        if (negative) buffer[--at] = '-';
        return new String(buffer, at, 65 - at);
    }

    public static long parseLong(String s, int radix) throws NumberFormatException {
        if (s == null || s.length() == 0 || radix < 2 || radix > 36) throw NumberFormatException.forInputString(s);
        int i = 0, length = s.length();
        boolean negative = false;
        if (s.charAt(0) == '-' || s.charAt(0) == '+') {
            negative = s.charAt(0) == '-';
            i = 1;
            if (length == 1) throw NumberFormatException.forInputString(s);
        }
        long limit = negative ? MIN_VALUE : -MAX_VALUE, result = 0, minimum = limit / radix;
        for (; i < length; i++) {
            int digit = Character.digit(s.charAt(i), radix);
            if (digit < 0 || result < minimum) throw NumberFormatException.forInputString(s);
            result *= radix;
            if (result < limit + digit) throw NumberFormatException.forInputString(s);
            result -= digit;
        }
        return negative ? result : -result;
    }

    public static long parseLong(String s) throws NumberFormatException {
        return parseLong(s, 10);
    }

    public static Long valueOf(long l) { return new Long(l); }
    public long longValue() { return value; }
    public int intValue() { return (int) value; }
    public float floatValue() { return value; }
    public double doubleValue() { return value; }
    public String toString() { return toString(value); }
    public int hashCode() { return (int) (value ^ (value >>> 32)); }
    public boolean equals(Object o) { return o instanceof Long && ((Long) o).value == value; }
}
