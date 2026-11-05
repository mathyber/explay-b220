package javax.microedition.io;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/* Generic Connection Framework: socket:// and http:// over the firmware's
   packet data (go1.Net). https needs TLS in the VM and file/sms are not
   implemented: they report ConnectionNotFoundException as such a phone would. */
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
        String scheme = name.substring(0, colon).toLowerCase();
        if (scheme.equals("http")) return new go1.HttpClient(name);
        if (scheme.equals("socket") && name.startsWith("//", colon+1)) {
            String address = name.substring(colon+3);
            int semicolon = address.indexOf(';');
            if (semicolon >= 0) address = address.substring(0, semicolon);
            int port = address.lastIndexOf(':');
            if (port <= 0 || port == address.length()-1)
                throw new ConnectionNotFoundException("Server sockets are not supported: " + name);
            return new go1.TcpConnection(address.substring(0, port), Integer.parseInt(address.substring(port+1)));
        }
        throw new ConnectionNotFoundException("Not supported: " + scheme);
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
