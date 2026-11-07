/* Calls the JIT puts in place (jit.c inline_call): tiny static methods, getters
   and setters, virtual calls whose receiver sometimes overrides the method, null
   receivers, Math.min/max/abs. The output must match the interpreter's. */
public class JitTest4 {
    static int scale = 3;
    int x, y;
    float f = 1.5f;

    static int I(int a, int b) { return a >> b & 255; }
    static int mix(int a, int b, int c) { return (a * 31 ^ b) + (c << 2) - scale; }
    static void bump() { scale = scale + 1; }
    static byte low(int v) { return (byte) v; }

    int getX() { return x; }
    void setX(int v) { x = v; }
    int sum() { return x + y; }
    float getF() { return f; }
    JitTest4 self() { return this; }
    int same(int v) { return v; }

    static class Sub extends JitTest4 {
        int getX() { return -x; }
        int sum() { return x * y; }
    }

    static int statics(int n) {
        int s = 0;
        for (int i = 0; i < n; i++) {
            s += I(i * 77, i & 15) + I(s, 3) + mix(i, s, i >> 2) + low(i * 3);
            if ((i & 1023) == 0) bump();
        }
        return s + scale;
    }

    static int virtuals(int n) {
        JitTest4 a = new JitTest4(), b = new Sub();
        int s = 0;
        for (int i = 0; i < n; i++) {
            JitTest4 o = (i % 7 == 0) ? b : a;
            o.setX(i);
            o.y = i >> 1;
            s += o.getX() + o.sum() + o.self().same(i & 3) + (int) o.getF();
        }
        return s;
    }

    static int nulls(int n) {
        JitTest4 a = new JitTest4(), none = null;
        int s = 0, caught = 0;
        for (int i = 0; i < n; i++) {
            JitTest4 o = (i % 97 == 0) ? none : a;
            try {
                o.setX(i);
                s += o.getX();
            } catch (NullPointerException e) {
                caught++;
            }
        }
        return s + caught * 100000;
    }

    static int maths(int n) {
        int s = 0;
        for (int i = -n; i < n; i++) s += Math.min(i, 100) + Math.max(i * 3, -50) + Math.abs(i - 7) + Math.abs(Integer.MIN_VALUE + (i & 1));
        return s;
    }

    public static void main(String[] args) {
        System.out.println("statics " + statics(20000));
        System.out.println("virtuals " + virtuals(20000));
        System.out.println("nulls " + nulls(20000));
        System.out.println("maths " + maths(20000));
        System.out.println("[finished]");
    }
}
