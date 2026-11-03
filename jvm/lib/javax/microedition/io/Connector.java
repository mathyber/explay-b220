package javax.microedition.io;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/* Generic Connection Framework. No protocol is available yet: the network
   (http, socket) needs firmware sockets, and file/sms are not implemented, so
   every open reports ConnectionNotFoundException as a phone without them would. */
public class Connector {
    public static final int READ = 1;
    public static final int WRITE = 2;
    public static final int READ_WRITE = 3;

    private Connector() {}

    public static Connection open(String name) throws IOException {
        return open(name, READ_WRITE, false);
    }

    public static Connection open(String name, int mode) throws IOException {
        return open(name, mode, false);
    }

    public static Connection open(String name, int mode, boolean timeouts) throws IOException {
        if (name == null) throw new IllegalArgumentException("Null URL");
        if (mode < READ || mode > READ_WRITE) throw new IllegalArgumentException("Bad mode");
        int colon = name.indexOf(':');
        if (colon <= 0) throw new IllegalArgumentException("No scheme: " + name);
        throw new ConnectionNotFoundException("Not supported: " + name.substring(0, colon));
    }

    public static InputStream openInputStream(String name) throws IOException {
        return ((InputConnection) open(name, READ)).openInputStream();
    }

    public static DataInputStream openDataInputStream(String name) throws IOException {
        return new DataInputStream(openInputStream(name));
    }

    public static OutputStream openOutputStream(String name) throws IOException {
        return ((OutputConnection) open(name, WRITE)).openOutputStream();
    }

    public static DataOutputStream openDataOutputStream(String name) throws IOException {
        return new DataOutputStream(openOutputStream(name));
    }
}
