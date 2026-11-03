import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.InputStream;
import java.io.InputStreamReader;

/* Resources from the application JAR (built by tests/test_jvm.py). */
public class Resources {
    static String readText(String name) throws Exception {
        InputStream in = Resources.class.getResourceAsStream(name);
        if (in == null) return null;
        InputStreamReader reader = new InputStreamReader(in, "UTF-8");
        StringBuffer b = new StringBuffer();
        char[] chunk = new char[7];
        int n;
        while ((n = reader.read(chunk, 0, chunk.length)) > 0) b.append(chunk, 0, n);
        reader.close();
        return b.toString();
    }

    public static void main(String[] args) throws Exception {
        String hello = readText("/res/hello.txt");
        System.out.println(hello.trim());
        System.out.println(hello.length() + " " + hello.indexOf("Привет"));
        System.out.println(readText("local.txt").trim());
        System.out.println(Resources.class.getResourceAsStream("/res/missing.txt") == null);

        DataInputStream data = new DataInputStream(Resources.class.getResourceAsStream("/res/data.bin"));
        System.out.println(data.readInt() + " " + data.readShort() + " " + data.readUTF() + " " + data.readLong()
            + " " + data.readBoolean() + " " + data.readUnsignedByte());
        try {
            data.readInt();
        } catch (EOFException e) {
            System.out.println("end of data");
        }

        InputStream big = Resources.class.getResourceAsStream("/res/big.txt");
        byte[] buffer = new byte[4096];
        int total = 0, hash = 0, n;
        while ((n = big.read(buffer)) > 0) {
            for (int i = 0; i < n; i++) hash = hash * 31 + buffer[i];
            total += n;
        }
        System.out.println(total + " " + hash);

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream out = new DataOutputStream(bytes);
        out.writeInt(-2);
        out.writeUTF("ёлка");
        out.writeDouble(0.5);
        out.writeChar('Z');
        DataInputStream back = new DataInputStream(new ByteArrayInputStream(bytes.toByteArray()));
        System.out.println(bytes.size() + " " + back.readInt() + " " + back.readUTF() + " " + back.readDouble() + " " + back.readChar());
    }
}
