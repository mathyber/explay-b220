import java.io.InputStream;

/* Reads e0..e(N-1) from the JAR and prints a checksum per entry. */
public class InflateTest {
    public static void main(String[] args) throws Exception {
        for (int i = 0; ; i++) {
            InputStream in = InflateTest.class.getResourceAsStream("/e" + i);
            if (in == null) break;
            int sum = 0, n = 0, b;
            byte[] buf = new byte[700];
            while ((b = in.read(buf, 0, 1 + (i * 37) % 700)) > 0) {
                for (int k = 0; k < b; k++) sum = sum * 31 + (buf[k] & 255);
                n += b;
            }
            System.out.println(i + " " + n + " " + sum);
        }
    }
}
