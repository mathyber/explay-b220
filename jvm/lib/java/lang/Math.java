package java.lang;

public final class Math {
    public static final double E = 2.718281828459045;
    public static final double PI = 3.141592653589793;

    private Math() {}

    public static int abs(int a) { return a < 0 ? -a : a; }
    public static long abs(long a) { return a < 0 ? -a : a; }
    public static float abs(float a) { return a <= 0.0f ? 0.0f - a : a; }
    public static double abs(double a) { return a <= 0.0 ? 0.0 - a : a; }
    public static int max(int a, int b) { return a >= b ? a : b; }
    public static long max(long a, long b) { return a >= b ? a : b; }
    public static float max(float a, float b) { return a != a ? a : a >= b ? a : b; }
    public static double max(double a, double b) { return a != a ? a : a >= b ? a : b; }
    public static int min(int a, int b) { return a <= b ? a : b; }
    public static long min(long a, long b) { return a <= b ? a : b; }
    public static float min(float a, float b) { return a != a ? a : a <= b ? a : b; }
    public static double min(double a, double b) { return a != a ? a : a <= b ? a : b; }

    public static double floor(double a) {
        if (a != a || a >= 4503599627370496.0 || a <= -4503599627370496.0) return a;
        long l = (long) a;
        double d = l;
        return d > a ? d - 1 : d;
    }

    public static double ceil(double a) {
        if (a != a || a >= 4503599627370496.0 || a <= -4503599627370496.0) return a;
        long l = (long) a;
        double d = l;
        return d < a ? d + 1 : d;
    }

    public static double sqrt(double a) {
        if (a != a || a < 0) return Double.NaN;
        if (a == 0 || a == Double.POSITIVE_INFINITY) return a;
        double x = a > 1 ? a / 2 : 1;
        for (int i = 0; i < 64; i++) {
            double next = (x + a / x) / 2;
            if (next == x) break;
            x = next;
        }
        return x;
    }

    public static double toRadians(double degrees) { return degrees / 180.0 * PI; }
    public static double toDegrees(double radians) { return radians * 180.0 / PI; }

    /* Range reduction to [-pi, pi] then a Taylor series; enough for games. */
    public static double sin(double a) {
        if (a != a || a == Double.POSITIVE_INFINITY || a == Double.NEGATIVE_INFINITY) return Double.NaN;
        double twoPi = 2 * PI;
        a = a - twoPi * floor(a / twoPi + 0.5);
        double term = a, sum = a, square = a * a;
        for (int n = 1; n < 12; n++) {
            term *= -square / ((2 * n) * (2 * n + 1));
            sum += term;
        }
        return sum;
    }

    public static double cos(double a) { return sin(a + PI / 2); }

    public static double tan(double a) { return sin(a) / cos(a); }

    public static int round(float a) { return (int) floor(a + 0.5f); }
    public static long round(double a) { return (long) floor(a + 0.5); }
}
