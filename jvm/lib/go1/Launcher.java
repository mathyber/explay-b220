package go1;

import java.io.InputStream;
import java.util.Hashtable;
import javax.microedition.lcdui.Display;
import javax.microedition.midlet.MIDlet;

/* Starts MIDlet-1 from the manifest and runs the MIDP event loop. */
public final class Launcher {
    private static final Hashtable properties = new Hashtable();
    private static MIDlet midlet;
    static boolean exiting;

    private Launcher() {}

    public static String getProperty(String key) {
        return (String) properties.get(key);
    }

    public static void exit() {
        exiting = true;
        Events.post(Events.REPAINT, 0);
    }

    private static void readManifest() throws Exception {
        InputStream in = Launcher.class.getResourceAsStream("/META-INF/MANIFEST.MF");
        if (in == null) return;
        byte[] buffer = new byte[in.available()];
        int n = 0;
        while (n < buffer.length) {
            int r = in.read(buffer, n, buffer.length - n);
            if (r < 0) break;
            n += r;
        }
        String text = new String(buffer, 0, n, "UTF-8");
        String key = null;
        StringBuffer value = new StringBuffer();
        int start = 0;
        while (start < text.length()) {
            int end = text.indexOf('\n', start);
            if (end < 0) end = text.length();
            String line = text.substring(start, end);
            if (line.endsWith("\r")) line = line.substring(0, line.length() - 1);
            start = end + 1;
            if (line.startsWith(" ") && key != null) {
                value.append(line.substring(1));
                continue;
            }
            if (key != null) properties.put(key, value.toString().trim());
            key = null;
            int colon = line.indexOf(':');
            if (colon > 0) {
                key = line.substring(0, colon).trim();
                value = new StringBuffer(line.substring(colon + 1));
            }
        }
        if (key != null) properties.put(key, value.toString().trim());
    }

    public static void main(String[] args) throws Exception {
        readManifest();
        String entry = getProperty("MIDlet-1");
        if (entry == null) throw new IllegalStateException("No MIDlet-1 in the manifest");
        String className = entry.substring(entry.lastIndexOf(',') + 1).trim();
        midlet = (MIDlet) Class.forName(className).newInstance();
        Display display = Display.getDisplay(midlet);
        midlet.go1Start();
        display.run();
        midlet.go1Destroy();
    }

    public static boolean isExiting() {
        return exiting;
    }
}
