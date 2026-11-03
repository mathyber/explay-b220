/* Core language test; the expected output comes from running it on a real JVM. */
public class Basics {
    interface Shape { int area(); String name(); }

    static abstract class Base implements Shape {
        static int created;
        final String label;
        Base(String label) { this.label = label; created++; }
        public String name() { return label + ":" + area(); }
    }

    static class Square extends Base {
        final int side;
        Square(int side) { super("square"); this.side = side; }
        public int area() { return side * side; }
    }

    static class Rect extends Square {
        final int other;
        Rect(int a, int b) { super(a); this.other = b; }
        public int area() { return side * other; }
        public String name() { return "rect/" + super.name(); }
    }

    static final int[] TABLE = build();
    static int initOrder = 0;
    static { initOrder = TABLE.length; }

    static int[] build() {
        int[] t = new int[5];
        for (int i = 0; i < t.length; i++) t[i] = i * i;
        return t;
    }

    static int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

    static String classify(int n) {
        switch (n) {
            case 1: return "one";
            case 2: return "two";
            case 100: return "hundred";
            default: return "many";
        }
    }

    static String dense(int n) {
        switch (n) {
            case 0: return "zero";
            case 1: return "a";
            case 2: return "b";
            case 3: return "c";
            default: return "?";
        }
    }

    static int tryFinally(int[] log) {
        try {
            log[0]++;
            throw new IllegalStateException("boom");
        } catch (IllegalStateException e) {
            log[1] = e.getMessage().length();
            return 7;
        } finally {
            log[2] = 99;
        }
    }

    public static void main(String[] args) {
        System.out.println("hello, world");
        System.out.println(fib(20));
        int a = 7, b = -3;
        System.out.println(a / b + " " + a % b + " " + (a << 3) + " " + (b >> 1) + " " + (b >>> 28));
        System.out.println(Integer.MIN_VALUE / -1);
        System.out.println(Integer.MIN_VALUE % -1);
        long big = 1L << 40;
        System.out.println(big + " " + (big * 3 - 1) + " " + (big / 7) + " " + Long.MIN_VALUE);
        System.out.println((int) 3.99 + " " + (int) -3.99 + " " + (long) 1e30 + " " + (int) Double.NaN);
        System.out.println(1.5 + " " + 0.25f + " " + 100.0 + " " + (2.0 / 4) + " " + 7.5 % 2);
        System.out.println((char) ('a' + 2) + "" + (byte) 200 + " " + (short) 70000);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 5; i++) sb.append(i).append(',');
        sb.setLength(sb.length() - 1);
        System.out.println(sb.reverse());
        String s = "Explay B220";
        System.out.println(s.length() + " " + s.indexOf('B') + " " + s.substring(7) + " " + s.toUpperCase()
            + " " + s.replace('2', '9') + " " + s.startsWith("Exp") + " " + s.endsWith("20")
            + " " + "  trim me ".trim() + "|" + s.compareTo("Explay") + " " + s.hashCode());
        System.out.println("abc".equals("a" + "bc") + " " + ("ab" + "c" == "abc") + " " + new String("abc").intern() == "abc");
        System.out.println(Integer.parseInt("-12345") + Integer.parseInt("ff", 16) + " " + Integer.toHexString(-1)
            + " " + Integer.toString(255, 2) + " " + Long.parseLong("9000000000"));
        int[][] grid = new int[3][4];
        grid[2][3] = 5;
        System.out.println(grid.length + " " + grid[0].length + " " + grid[2][3]);
        Object[] objects = {"x", new Integer(5), null};
        System.out.println(objects.length + " " + (objects[1] instanceof Integer) + " " + (objects[2] instanceof String));
        char[] chars = {'h', 'i'};
        System.out.println(new String(chars) + String.valueOf(chars, 1, 1) + " " + TABLE[4] + " " + initOrder);
        Shape[] shapes = {new Square(3), new Rect(2, 5)};
        for (int i = 0; i < shapes.length; i++) System.out.println(shapes[i].name() + " " + shapes[i].area());
        System.out.println(Base.created);
        System.out.println(classify(1) + classify(2) + classify(100) + classify(5) + dense(0) + dense(3) + dense(9));
        int[] log = new int[3];
        System.out.println(tryFinally(log) + " " + log[0] + " " + log[1] + " " + log[2]);
        try {
            Object o = "string";
            Integer bad = (Integer) o;
            System.out.println(bad);
        } catch (ClassCastException e) {
            System.out.println("cast failed");
        }
        try {
            int[] small = new int[2];
            small[2] = 1;
        } catch (ArrayIndexOutOfBoundsException e) {
            System.out.println("index caught");
        }
        try {
            String n = null;
            n.length();
        } catch (NullPointerException e) {
            System.out.println("npe caught");
        }
        try {
            System.out.println(10 / (a - 7));
        } catch (ArithmeticException e) {
            System.out.println("div caught " + e.getMessage());
        }
        try {
            Integer.parseInt("12x");
        } catch (NumberFormatException e) {
            System.out.println("nfe " + e.getMessage());
        }
        long sum = 0;
        for (int i = 0; i < 100000; i++) sum += i % 7 == 0 ? i : -1;
        System.out.println(sum);
        System.out.println(Math.max(3, 9) + " " + Math.abs(-4) + " " + Math.min(-2L, 5L) + " " + Math.sqrt(16.0));
    }
}
