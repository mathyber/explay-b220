package go1;

/* Native drawing on the screen (target null) or a javax.microedition.lcdui.Image.
   Coordinates are absolute; clip arguments are x, y, width, height. */
public final class Draw {
    private Draw() {}

    public static native void fillRect(Object target, int x, int y, int w, int h, int rgb,
                                       int cx, int cy, int cw, int ch);
    public static native void drawLine(Object target, int x1, int y1, int x2, int y2, int rgb,
                                       int cx, int cy, int cw, int ch);
    public static native void fillTriangle(Object target, int x1, int y1, int x2, int y2, int x3, int y3, int rgb,
                                           int cx, int cy, int cw, int ch);
    public static native void drawRegion(Object target, Object image, int sx, int sy, int w, int h, int transform,
                                         int x, int y, int cx, int cy, int cw, int ch);
    public static native void drawRGB(Object target, int[] rgb, int offset, int scan, int x, int y, int w, int h,
                                      boolean alpha, int cx, int cy, int cw, int ch);
    public static native void getRGB(Object image, int[] rgb, int offset, int scan, int x, int y, int w, int h);
    public static native int textWidth(String s, int offset, int length, byte[] font);
    public static native int drawText(Object target, String s, int offset, int length, int x, int y, int rgb,
                                      byte[] font, boolean bold, int cx, int cy, int cw, int ch);
    public static native void packAlpha(Object image);

    public static native void flush(int x, int y, int w, int h);
    public static native boolean decodePng(byte[] data, int offset, int length, Object image);
}
