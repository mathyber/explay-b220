package javax.microedition.lcdui;

import b22.Draw;
import java.io.IOException;
import java.io.InputStream;

/* RGB565 pixels with optional 8-bit alpha (fields used by b22.Draw). */
public class Image {
    short[] pixels;
    byte[] alpha;
    int width;
    int height;
    private boolean mutable;

    private Image() {}

    public static Image createImage(int w, int h) {
        if (w <= 0 || h <= 0) throw new IllegalArgumentException();
        Image img = new Image();
        img.width = w;
        img.height = h;
        img.pixels = new short[w * h];
        for (int i = 0; i < img.pixels.length; i++) img.pixels[i] = (short) 0xffff;
        img.mutable = true;
        return img;
    }

    public static Image createImage(Image source) {
        if (!source.mutable) return source;
        Image img = new Image();
        img.width = source.width;
        img.height = source.height;
        img.pixels = new short[source.pixels.length];
        System.arraycopy(source.pixels, 0, img.pixels, 0, img.pixels.length);
        return img;
    }

    public static Image createImage(Image source, int x, int y, int w, int h, int transform) {
        if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > source.width || y + h > source.height
            || transform < 0 || transform > 7) throw new IllegalArgumentException();
        Image img = new Image();
        img.width = transform >= 4 ? h : w;
        img.height = transform >= 4 ? w : h;
        img.pixels = new short[img.width * img.height];
        if (source.alpha != null) img.alpha = new byte[img.pixels.length];
        Draw.drawRegion(img, source, x, y, w, h, transform, 0, 0, 0, 0, img.width, img.height);
        return img;
    }

    public static Image createImage(String name) throws IOException {
        InputStream in = Image.class.getResourceAsStream(name);
        if (in == null) throw new IOException(name);
        return createImage(in);
    }

    public static Image createImage(InputStream in) throws IOException {
        byte[] buffer = new byte[Math.max(256, in.available())];
        int n = 0;
        while (true) {
            if (n == buffer.length) {
                byte[] bigger = new byte[buffer.length * 2];
                System.arraycopy(buffer, 0, bigger, 0, n);
                buffer = bigger;
            }
            int r = in.read(buffer, n, buffer.length - n);
            if (r < 0) break;
            n += r;
        }
        return createImage(buffer, 0, n);
    }

    public static Image createImage(byte[] data, int offset, int length) {
        Image img = new Image();
        if (!Draw.decodePng(data, offset, length, img)) throw new IllegalArgumentException("unsupported image");
        return img;
    }

    public static Image createRGBImage(int[] rgb, int w, int h, boolean processAlpha) {
        if (w <= 0 || h <= 0) throw new IllegalArgumentException();
        if (rgb.length < w * h) throw new ArrayIndexOutOfBoundsException();
        Image img = new Image();
        img.width = w;
        img.height = h;
        img.pixels = new short[w * h];
        if (processAlpha) img.alpha = new byte[w * h];
        for (int i = 0; i < w * h; i++) {
            int c = rgb[i];
            img.pixels[i] = (short) (((c >> 8) & 0xf800) | ((c >> 5) & 0x07e0) | ((c >> 3) & 0x001f));
            if (processAlpha) img.alpha[i] = (byte) (c >>> 24);
        }
        return img;
    }

    public Graphics getGraphics() {
        if (!mutable) throw new IllegalStateException();
        return new Graphics(this, width, height);
    }

    public int getWidth() { return width; }
    public int getHeight() { return height; }
    public boolean isMutable() { return mutable; }

    public void getRGB(int[] rgb, int offset, int scan, int x, int y, int w, int h) {
        Draw.getRGB(this, rgb, offset, scan, x, y, w, h);
    }
}
