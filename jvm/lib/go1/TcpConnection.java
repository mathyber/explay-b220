package go1;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import javax.microedition.io.SocketConnection;

/* socket://host:port over the firmware's TCP; reads and writes wait with sleep. */
public class TcpConnection implements SocketConnection {
    final String host;
    final int port;
    private int socket;
    private boolean inputOpen, outputOpen;

    public TcpConnection(String host, int port) throws IOException {
        this.host = host;
        this.port = port;
        socket = Net.connectTo(host, port);
    }

    int handle() throws IOException {
        if (socket == 0) throw new IOException("Connection closed");
        return socket;
    }

    /* Bytes read, -1 at the end. */
    int read(byte[] b, int offset, int length) throws IOException {
        if (length == 0) return 0;
        for (;;) {
            int n = Net.recv(handle(), b, offset, length);
            if (n > 0) return n;
            if (n == 0) return -1;
            if (n != Net.AGAIN) throw new IOException("Receive failed");
            Net.pause();
        }
    }

    void write(byte[] b, int offset, int length) throws IOException {
        while (length > 0) {
            int n = Net.send(handle(), b, offset, length);
            if (n > 0) { offset += n; length -= n; }
            else if (n == Net.AGAIN || n == 0) Net.pause();
            else throw new IOException("Send failed");
        }
    }

    public InputStream openInputStream() throws IOException {
        handle();
        inputOpen = true;
        return new InputStream() {
            private final byte[] one = new byte[1];
            public int read() throws IOException {
                return TcpConnection.this.read(one, 0, 1) < 0 ? -1 : one[0] & 255;
            }
            public int read(byte[] b, int offset, int length) throws IOException {
                return TcpConnection.this.read(b, offset, length);
            }
            public void close() { inputOpen = false; }
        };
    }

    public DataInputStream openDataInputStream() throws IOException { return new DataInputStream(openInputStream()); }

    public OutputStream openOutputStream() throws IOException {
        handle();
        outputOpen = true;
        return new OutputStream() {
            public void write(int value) throws IOException {
                TcpConnection.this.write(new byte[] {(byte) value}, 0, 1);
            }
            public void write(byte[] b, int offset, int length) throws IOException {
                TcpConnection.this.write(b, offset, length);
            }
            public void close() { outputOpen = false; }
        };
    }

    public DataOutputStream openDataOutputStream() throws IOException { return new DataOutputStream(openOutputStream()); }

    public void close() {
        if (socket != 0) { Net.close(socket); socket = 0; }
    }

    public void setSocketOption(byte option, int value) {}
    public int getSocketOption(byte option) { return 0; }
    public String getLocalAddress() { return "0.0.0.0"; }
    public int getLocalPort() { return 0; }
    public String getAddress() { return host; }
    public int getPort() { return port; }
}
