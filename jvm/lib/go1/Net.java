package go1;

import java.io.IOException;

/* Packet data and TCP through the host. The natives never block: AGAIN means
   "not yet", and the helpers here wait with Thread.sleep so other Java threads
   and the phone keep running. */
public final class Net {
    public static final int AGAIN = -2, ERROR = -1;
    static final int POLL_MS = 20;

    private Net() {}

    /* Starts GPRS when needed: 0 off, 1 connecting, 2 ready. Call it once, then poll state(). */
    public static native int start();
    public static native int state();
    /* IPv4 address (big-endian int), 0 while the name is still being resolved. */
    public static native int dns(String host);
    public static native int open();
    /* 0 connected, AGAIN in progress, ERROR failed. */
    public static native int connect(int socket, int ipv4, int port);
    /* Bytes moved, AGAIN, ERROR; recv returns 0 at the end of the stream. */
    public static native int send(int socket, byte[] b, int offset, int length);
    public static native int recv(int socket, byte[] b, int offset, int length);
    public static native void close(int socket);

    static void pause() throws IOException {
        try {
            Thread.sleep(POLL_MS);
        } catch (InterruptedException e) {
            throw new java.io.InterruptedIOException();
        }
    }

    static int parseIp(String host) {
        int value = 0, parts = 0, part = -1;
        for (int i = 0; i <= host.length(); i++) {
            char c = i < host.length() ? host.charAt(i) : '.';
            if (c >= '0' && c <= '9') {
                part = (part < 0 ? 0 : part*10)+(c-'0');
                if (part > 255) return 0;
            } else if (c == '.' && part >= 0) {
                value = value << 8 | part;
                parts++;
                part = -1;
            } else return 0;
        }
        return parts == 4 ? value : 0;
    }

    /* A connected socket handle; waits for GPRS, the name and the connection. */
    public static int connectTo(String host, int port) throws IOException {
        long start = System.currentTimeMillis();
        for (int s = start(); s != 2; s = state()) {
            if (System.currentTimeMillis()-start > 90000) throw new IOException("No packet data connection");
            pause();
        }
        int ip = parseIp(host);
        start = System.currentTimeMillis();
        while (ip == 0) {
            ip = dns(host);
            if (ip != 0) break;
            if (System.currentTimeMillis()-start > 30000) throw new IOException("Unknown host: " + host);
            pause();
        }
        int socket = open();
        if (socket == 0) throw new IOException("No free socket");
        start = System.currentTimeMillis();
        for (;;) {
            int r = connect(socket, ip, port);
            if (r == 0) return socket;
            if (r != AGAIN || System.currentTimeMillis()-start > 30000) {
                close(socket);
                throw new IOException("Cannot connect to " + host + ":" + port);
            }
            pause();
        }
    }
}
