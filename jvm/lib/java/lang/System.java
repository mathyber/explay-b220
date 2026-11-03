package java.lang;

import java.io.PrintStream;

public final class System {
    public static final PrintStream out = new PrintStream(1);
    public static final PrintStream err = new PrintStream(2);

    private System() {}

    public static native void arraycopy(Object source, int from, Object destination, int to, int length);
    public static native long currentTimeMillis();
    public static native int identityHashCode(Object o);
    public static native void gc();

    public static void exit(int status) {
        Runtime.getRuntime().exit(status);
    }

    public static String getProperty(String key) {
        if ("microedition.platform".equals(key)) return "Explay-B220";
        if ("microedition.encoding".equals(key)) return "UTF-8";
        if ("microedition.configuration".equals(key)) return "CLDC-1.1";
        if ("microedition.profiles".equals(key)) return "MIDP-2.0";
        if ("microedition.locale".equals(key)) return "ru-RU";
        return null;
    }
}
