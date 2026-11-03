package com.nokia.mid.ui;

import javax.microedition.lcdui.Image;

public interface DirectGraphics {
    int FLIP_HORIZONTAL = 0x2000;
    int FLIP_VERTICAL = 0x4000;
    int ROTATE_90 = 90;
    int ROTATE_180 = 180;
    int ROTATE_270 = 270;
    int TYPE_INT_888_RGB = 888;
    int TYPE_INT_8888_ARGB = 8888;
    int TYPE_USHORT_565_RGB = 565;

    void setARGBColor(int argb);
    int getAlphaComponent();
    int getNativePixelFormat();
    void drawImage(Image img, int x, int y, int anchor, int manipulation);
    void drawTriangle(int x1, int y1, int x2, int y2, int x3, int y3, int argb);
    void fillTriangle(int x1, int y1, int x2, int y2, int x3, int y3, int argb);
    void drawPolygon(int[] xs, int xo, int[] ys, int yo, int n, int argb);
    void fillPolygon(int[] xs, int xo, int[] ys, int yo, int n, int argb);
    void drawPixels(int[] pixels, boolean transparency, int offset, int scan, int x, int y, int w, int h, int manipulation, int format);
}
