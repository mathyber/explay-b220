/* More JIT templates (jit.c): switches with negative keys, null tests, long and
   wide fields, conversions, object arrays, a loop entered from a callee's return. */
public class JitTest2 {
    static long staticWide = 5;
    static Object staticRef;
    long wide = -3;
    double real = 1.5;
    Object ref;
    String[] names = {"a", "bb", null, "dddd"};

    static int tables(int n) {
        int sum = 0;
        for (int i = -n; i < n; i++) {
            switch (i % 7) {
            case -6: sum += 1; break;
            case -3: sum ^= 7; break;
            case -1: sum -= 2; break;
            case 0: sum *= 3; break;
            case 2: sum += 9; break;
            case 4: sum >>= 1; break;
            default: sum += 5;
            }
            switch (i * 1000) {
            case -5000: sum += 77; break;
            case -1000000: sum ^= 1; break;
            case 3000: sum -= 31; break;
            case 2000000000: sum = 1; break;
            default: break;
            }
            sum &= 0xfffffff;
        }
        return sum;
    }

    int objects(int n) {
        int sum = 0;
        Object[] objs = new Object[5];
        for (int i = 0; i < n; i++) {
            objs[i % 5] = (i & 1) == 0 ? null : names;
            Object o = objs[(i * 3) % 5];
            if (o == null) sum += 1;
            if (o != null) sum += 2;
            if (o == names) sum += 4;
            if (o != names) sum += 8;
            String s = names[i & 3];
            if (s != null) sum += s.length();
            ref = s;
            if (ref == null) sum ^= 0x100;
            staticRef = ref;
        }
        return sum;
    }

    long wides(int n) {
        long acc = 0;
        for (int i = 0; i < n; i++) {
            long w = wide;
            wide = staticWide;
            staticWide = w;
            double r = real;
            real = r;
            long x = i;                   /* i2l */
            int back = (int) (x + w);     /* ladd: interpreter, l2i */
            acc = acc + back;
            long c0 = 0L, c1 = 1L;
            acc = acc + c0 + c1;
            float f0 = 0f, f1 = 1f, f2 = 2f, f3 = 3.25f;
            acc += (int) (f0 + f1 + f2 + f3);
        }
        return acc + wide + staticWide;
    }

    static int helper(int x) { return x * 3 + 1; }

    static int nested(int n) {
        int sum = 0;
        int[][] grid = new int[8][8];
        for (int k = 0; k < n; k++)
            for (int y = 0; y < 8; y++) {
                int[] row = grid[y];
                for (int x = 0; x < 8; x++) {
                    row[x] = helper(row[x] + x - y) & 1023;
                    sum += row[x];
                }
            }
        return sum;
    }

    static int dups(int n) {
        int[] counts = new int[3];
        int value = 0, sum = 0;
        long[] longs = new long[3];
        for (int i = 0; i < n; i++) {
            sum += value = i ^ sum;          /* dup_x1-ish */
            counts[i % 3]++;
            longs[i % 3] = longs[(i + 1) % 3];
            char c = (char) (i * 77);
            short s = (short) c;
            byte b = (byte) s;
            sum += c + s + b;
        }
        return sum + counts[0] + counts[1] + counts[2] + (int) longs[2];
    }

    static int thrown(int n) {
        int sum = 0;
        for (int i = 0; i < n; i++) {
            try {
                sum += deep(i);
            } catch (RuntimeException e) {
                sum += 1000;
            }
        }
        return sum;
    }

    static int deep(int i) {
        if (i % 17 == 0) throw new RuntimeException();
        int[] a = new int[i % 4];
        return a.length + a[i % 5];
    }

    public static void main(String[] args) {
        JitTest2 t = new JitTest2();
        System.out.println("tables " + tables(20000));
        System.out.println("objects " + t.objects(20000));
        System.out.println("wides " + t.wides(10000));
        System.out.println("nested " + nested(200));
        System.out.println("dups " + dups(20000));
        System.out.println("thrown " + thrown(3000));
        System.out.println("[finished]");
    }
}
