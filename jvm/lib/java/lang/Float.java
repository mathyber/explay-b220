package java.lang;

public final class Float {
    public static final float POSITIVE_INFINITY = 1.0f / 0.0f;
    public static final float NEGATIVE_INFINITY = -1.0f / 0.0f;
    public static final float NaN = 0.0f / 0.0f;
    public static final float MAX_VALUE = 3.4028235e+38f;
    public static final float MIN_VALUE = 1.4e-45f;
    private final float value;

    public Float(float value) {
        this.value = value;
    }

    public Float(double value) {
        this.value = (float) value;
    }

    public static native String toString(float f);
    public static native int floatToIntBits(float f);
    public static native float intBitsToFloat(int bits);

    public static float parseFloat(String s) throws NumberFormatException {
        return (float) Double.parseDouble(s);
    }

    public static Float valueOf(String s) throws NumberFormatException { return new Float(parseFloat(s)); }
    public static Float valueOf(float f) { return new Float(f); }
    public static boolean isNaN(float f) { return f != f; }
    public static boolean isInfinite(float f) { return f == POSITIVE_INFINITY || f == NEGATIVE_INFINITY; }
    public boolean isNaN() { return isNaN(value); }
    public boolean isInfinite() { return isInfinite(value); }
    public float floatValue() { return value; }
    public double doubleValue() { return value; }
    public int intValue() { return (int) value; }
    public long longValue() { return (long) value; }
    public String toString() { return toString(value); }
    public int hashCode() { return floatToIntBits(value); }
    public boolean equals(Object o) { return o instanceof Float && floatToIntBits(((Float) o).value) == floatToIntBits(value); }
}
