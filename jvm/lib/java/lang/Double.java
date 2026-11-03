package java.lang;

public final class Double {
    public static final double POSITIVE_INFINITY = 1.0 / 0.0;
    public static final double NEGATIVE_INFINITY = -1.0 / 0.0;
    public static final double NaN = 0.0 / 0.0;
    public static final double MAX_VALUE = 1.7976931348623157e+308;
    public static final double MIN_VALUE = 4.9e-324;
    private final double value;

    public Double(double value) {
        this.value = value;
    }

    public static native String toString(double d);
    public static native long doubleToLongBits(double d);
    public static native double longBitsToDouble(long bits);

    public static double parseDouble(String s) throws NumberFormatException {
        s = s.trim();
        int length = s.length(), i = 0;
        if (length == 0) throw NumberFormatException.forInputString(s);
        boolean negative = false;
        if (s.charAt(0) == '-' || s.charAt(0) == '+') { negative = s.charAt(0) == '-'; i++; }
        if (s.startsWith("NaN", i)) return NaN;
        if (s.startsWith("Infinity", i)) return negative ? NEGATIVE_INFINITY : POSITIVE_INFINITY;
        double result = 0;
        int digits = 0, exponent = 0;
        for (; i < length && Character.isDigit(s.charAt(i)); i++, digits++) result = result * 10 + (s.charAt(i) - '0');
        if (i < length && s.charAt(i) == '.') {
            for (i++; i < length && Character.isDigit(s.charAt(i)); i++, digits++) {
                result = result * 10 + (s.charAt(i) - '0');
                exponent--;
            }
        }
        if (digits == 0) throw NumberFormatException.forInputString(s);
        if (i < length && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
            i++;
            boolean minus = false;
            if (i < length && (s.charAt(i) == '-' || s.charAt(i) == '+')) { minus = s.charAt(i) == '-'; i++; }
            int e = 0, start = i;
            for (; i < length && Character.isDigit(s.charAt(i)); i++) e = e * 10 + (s.charAt(i) - '0');
            if (i == start) throw NumberFormatException.forInputString(s);
            exponent += minus ? -e : e;
        }
        if (i < length && "fFdD".indexOf(s.charAt(i)) >= 0) i++;
        if (i != length) throw NumberFormatException.forInputString(s);
        for (; exponent > 0; exponent--) result *= 10;
        for (; exponent < 0; exponent++) result /= 10;
        return negative ? -result : result;
    }

    public static Double valueOf(String s) throws NumberFormatException { return new Double(parseDouble(s)); }
    public static Double valueOf(double d) { return new Double(d); }
    public static boolean isNaN(double d) { return d != d; }
    public static boolean isInfinite(double d) { return d == POSITIVE_INFINITY || d == NEGATIVE_INFINITY; }
    public boolean isNaN() { return isNaN(value); }
    public boolean isInfinite() { return isInfinite(value); }
    public double doubleValue() { return value; }
    public float floatValue() { return (float) value; }
    public int intValue() { return (int) value; }
    public long longValue() { return (long) value; }
    public String toString() { return toString(value); }
    public int hashCode() { long bits = doubleToLongBits(value); return (int) (bits ^ (bits >>> 32)); }
    public boolean equals(Object o) { return o instanceof Double && doubleToLongBits(((Double) o).value) == doubleToLongBits(value); }
}
