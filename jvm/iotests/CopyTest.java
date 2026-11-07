import java.util.Random;

/* System.arraycopy and array clearing: overlapping both ways, every alignment,
   bytes, shorts and ints, checked against a copy through a temporary array. */
public class CopyTest {
    public static void main(String[] args) {
        Random rnd = new Random(3);
        int bad = 0, cases = 0;
        for (int n = 0; n < 3000; n++) {
            int len = 1 + rnd.nextInt(120), count = rnd.nextInt(len + 1);
            int from = rnd.nextInt(len - count + 1), to = rnd.nextInt(len - count + 1);
            switch (n % 3) {
            case 0: {
                byte[] a = new byte[len], b = new byte[len], tmp = new byte[count];
                for (int i = 0; i < len; i++) a[i] = b[i] = (byte) rnd.nextInt();
                System.arraycopy(a, from, a, to, count);
                for (int i = 0; i < count; i++) tmp[i] = b[from + i];
                for (int i = 0; i < count; i++) b[to + i] = tmp[i];
                for (int i = 0; i < len; i++) if (a[i] != b[i]) { bad++; break; }
                break;
            }
            case 1: {
                short[] a = new short[len], b = new short[len], tmp = new short[count];
                for (int i = 0; i < len; i++) a[i] = b[i] = (short) rnd.nextInt();
                System.arraycopy(a, from, a, to, count);
                for (int i = 0; i < count; i++) tmp[i] = b[from + i];
                for (int i = 0; i < count; i++) b[to + i] = tmp[i];
                for (int i = 0; i < len; i++) if (a[i] != b[i]) { bad++; break; }
                break;
            }
            default: {
                int[] a = new int[len], b = new int[len], c = new int[len];
                for (int i = 0; i < len; i++) a[i] = b[i] = rnd.nextInt();
                System.arraycopy(a, from, c, to, count);
                System.arraycopy(a, from, a, to, count);
                int[] tmp = new int[count];
                for (int i = 0; i < count; i++) tmp[i] = b[from + i];
                for (int i = 0; i < count; i++) b[to + i] = tmp[i];
                for (int i = 0; i < len; i++) if (a[i] != b[i]) { bad++; break; }
                for (int i = 0; i < len; i++) if (c[i] != (i >= to && i < to + count ? tmp[i - to] : 0)) { bad++; break; }
            }
            }
            cases++;
        }
        /* new arrays are zero at every length */
        for (int len = 0; len < 70; len++) {
            byte[] z = new byte[len];
            for (int i = 0; i < len; i++) if (z[i] != 0) bad++;
            cases++;
        }
        System.out.println("copies " + cases + " bad " + bad);
        System.out.println("[finished]");
    }
}
