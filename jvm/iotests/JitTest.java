/* Hot loops over everything the JIT translates (jit.c), with exceptions thrown
   from compiled code; the output must match the interpreter's. */
public class JitTest {
    static int counter;
    static long wide;
    int field;
    long wideField;
    short[] shorts = new short[64];

    static int pixel(int a, int b) { return (a * 31 + b) ^ (a >>> 3); }

    static int ints(int n) {
        int[] a = new int[257];
        int sum = 0;
        for (int i = 0; i < n; i++) {
            a[i & 255] += i * 7 - (i >> 2);
            sum ^= a[(i * 13) & 255] << (i & 7);
            sum += i % 7 == 0 ? i / 3 : -i;
            sum = sum - (i | 5) + (i & 9);
            if (sum < 0) sum = -sum;
            sum += (i >>> 1) ^ (-i >> 3);
        }
        return sum;
    }

    static int bytesAndChars(int n) {
        byte[] b = new byte[100];
        char[] c = new char[100];
        short[] s = new short[100];
        boolean[] z = new boolean[100];
        int sum = 0;
        for (int i = 0; i < n; i++) {
            int k = i % 100;
            b[k] = (byte) (i * 3);
            c[k] = (char) (i * 1000 - 7);
            s[k] = (short) (i * -999);
            z[k] = (i & 1) != 0;
            sum += b[(k * 7) % 100] + c[(k * 3) % 100] + s[(k * 11) % 100] + (z[k] ? 1 : 0);
            sum += (byte) sum + (char) (sum >> 3) + (short) (sum * 5);
        }
        return sum;
    }

    static long longs(int n) {
        long[] l = new long[16];
        double[] d = new double[16];
        long total = 0;
        for (int i = 0; i < n; i++) {
            long v = i;
            l[i & 15] = v * 100003L + total;   /* lmul, ladd: interpreter */
            d[i & 7] = l[i & 15];
            long w = l[(i * 5) & 15];
            total = w;
            int low = (int) w;
            wide = w;
            counter += low & 3;
        }
        return total + (long) d[3] + wide;
    }

    int fields(int n) {
        for (int i = 0; i < n; i++) {
            field += i ^ field >>> 2;
            wideField = wideField + field;
            shorts[i & 63] = (short) field;
            JitTest self = this;
            self.field -= shorts[(i * 3) & 63];
        }
        return field + (int) wideField;
    }

    static int switches(int n) {
        int sum = 0;
        for (int i = 0; i < n; i++) {
            switch (i % 9) {
            case 0: sum += 3; break;
            case 1: sum ^= i; break;
            case 2: case 3: sum -= i >> 1; break;
            case 5: sum += sum >> 4; break;
            case 8: sum = sum * 3 + 1; break;
            default: sum++;
            }
            switch (i * 37 % 1000) {
            case 1: sum += 11; break;
            case 100: sum -= 100; break;
            case 555: sum ^= 0x5555; break;
            case -4: sum = 0; break;
            default: sum += 2;
            }
        }
        return sum;
    }

    static int calls(int n) {
        int sum = 0;
        int[] row = new int[64];
        for (int y = 0; y < n; y++)
            for (int x = 0; x < 64; x++) {
                row[x] = pixel(x, y) + row[(x + 1) & 63];
                sum += row[x] & 0xff;
            }
        return sum;
    }

    static int exceptions(int n) {
        int[] a = new int[10];
        int[] none = null;
        int caught = 0, sum = 0;
        for (int i = 0; i < n; i++) {
            try {
                sum += a[i % 13];
            } catch (ArrayIndexOutOfBoundsException e) { caught++; }
            try {
                sum += 1000 / (i % 5);
            } catch (ArithmeticException e) { caught += 10; }
            try {
                if (i % 50 == 49) sum += none[0];
                if (i % 77 == 76) sum += none.length;
            } catch (NullPointerException e) { caught += 100; }
            a[i % 10] = sum & 0xffff;
        }
        return caught * 1000 + (sum & 0xfff);
    }

    static int division() {
        int sum = 0;
        int[] div = {1, -1, 2, -2, 7, -7, 1000, Integer.MIN_VALUE, Integer.MAX_VALUE};
        int[] num = {0, 1, -1, 5, -5, Integer.MIN_VALUE, Integer.MAX_VALUE, 123456789};
        for (int r = 0; r < 50; r++)
            for (int i = 0; i < num.length; i++)
                for (int j = 0; j < div.length; j++) sum = sum * 31 + num[i] / div[j] + num[i] % div[j];
        return sum;
    }

    static int constants(int n) {
        int sum = 0;
        float f = 0;
        for (int i = 0; i < n; i++) {
            sum += 100000 + 0x12345678 - 0x7fffffff + (-129) + 32767 - 32768 + 255 + 256 + 0xff00ff00;
            f = 1.0f;
            f = 2.0f;
            sum += Float.floatToIntBits(f) >> 20;
            Object o = null;
            if (o == null) sum++;
            if (o != null) sum--;
        }
        return sum;
    }

    static int stack(int n) {
        int sum = 0;
        int[] a = new int[4];
        for (int i = 0; i < n; i++) {
            a[i & 3] += i;              /* dup2 */
            sum = a[0] = sum + 1;        /* dup_x2 */
            counter = counter++ + i;
        }
        return sum + a[1] + a[2] + a[3];
    }

    public static void main(String[] args) {
        System.out.println("ints " + ints(30000));
        System.out.println("bytes " + bytesAndChars(20000));
        System.out.println("longs " + longs(5000));
        System.out.println("fields " + new JitTest().fields(20000));
        System.out.println("switches " + switches(20000));
        System.out.println("calls " + calls(300));
        System.out.println("exceptions " + exceptions(3000));
        System.out.println("division " + division());
        System.out.println("constants " + constants(5000));
        System.out.println("stack " + stack(5000));
        System.out.println("counter " + counter);
        System.out.println("[finished]");
    }
}
