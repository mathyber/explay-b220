package com.nokia.mid.ui;

import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* DirectGraphics over a plain Graphics; alpha below 0x80 draws nothing. */
class Direct implements DirectGraphics {
    /* MIDP transform for a mirror flag and a clockwise rotation in quarter turns. */
    private static final int[] TRANSFORMS = {0, 5, 3, 6, 2, 7, 1, 4};

    private final Graphics g;
    private int alpha = 0xff;

    Direct(Graphics g) { this.g = g; }

    /* Flips, then the counter-clockwise rotation (order assumed for combinations). */
    static int transform(int manipulation) {
        boolean h = (manipulation & FLIP_HORIZONTAL) != 0, v = (manipulation & FLIP_VERTICAL) != 0;
        int quarters = (4 - ((manipulation & 0x1fff) % 360) / 90) % 4;
        int mirror = h != v ? 1 : 0;
        if (v) quarters += 2;
        return TRANSFORMS[mirror * 4 + quarters % 4];
    }

    public void setARGBColor(int argb) {
        alpha = argb >>> 24;
        g.setColor(argb & 0xffffff);
    }

    public int getAlphaComponent() { return alpha; }
    public int getNativePixelFormat() { return TYPE_USHORT_565_RGB; }

    public void drawImage(Image img, int x, int y, int anchor, int manipulation) {
        if (img == null) throw new NullPointerException();
        g.drawRegion(img, 0, 0, img.getWidth(), img.getHeight(), transform(manipulation), x, y, anchor);
    }

    private boolean color(int argb) {
        g.setColor(argb & 0xffffff);
        return argb >>> 24 >= 0x80;
    }

    public void drawTriangle(int x1, int y1, int x2, int y2, int x3, int y3, int argb) {
        if (!color(argb)) return;
        g.drawLine(x1, y1, x2, y2);
        g.drawLine(x2, y2, x3, y3);
        g.drawLine(x3, y3, x1, y1);
    }

    public void fillTriangle(int x1, int y1, int x2, int y2, int x3, int y3, int argb) {
        if (color(argb)) g.fillTriangle(x1, y1, x2, y2, x3, y3);
    }

    public void drawPolygon(int[] xs, int xo, int[] ys, int yo, int n, int argb) {
        if (!color(argb)) return;
        for (int i = 0; i < n; i++) {
            int j = (i + 1) % n;
            g.drawLine(xs[xo + i], ys[yo + i], xs[xo + j], ys[yo + j]);
        }
    }

    public void fillPolygon(int[] xs, int xo, int[] ys, int yo, int n, int argb) {
        if (!color(argb)) return;
        for (int i = 1; i + 1 < n; i++)
            g.fillTriangle(xs[xo], ys[yo], xs[xo + i], ys[yo + i], xs[xo + i + 1], ys[yo + i + 1]);
    }

    public void drawPixels(int[] pixels, boolean transparency, int offset, int scan, int x, int y, int w, int h, int manipulation, int format) {
        Image image = Image.createRGBImage(slice(pixels, offset, scan, w, h), w, h, transparency);
        g.drawRegion(image, 0, 0, w, h, transform(manipulation), x, y, Graphics.TOP | Graphics.LEFT);
    }

    private static int[] slice(int[] pixels, int offset, int scan, int w, int h) {
        int[] rgb = new int[w * h];
        for (int row = 0; row < h; row++) System.arraycopy(pixels, offset + row * scan, rgb, row * w, w);
        return rgb;
    }
}
